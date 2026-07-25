"""Core backtest engine.

This is a faithful reproduction of `backtest_mega` from analysis/strategy.ipynb
(cell 5), refactored into an importable function that returns per-trade records so
callers can slice results (e.g. shortable vs. unshortable subsets) and compute
drawdown. The strategy math is unchanged from the original grid search so results
remain comparable to the published 574-trade / 60.97% baseline.

Two intentional, documented deviations from the notebook:

1. `entry_offset` (default 0) — the original enters at the Open of the first trading
   day on/after the announcement (`t_ann`). That is optimistic: an 8-K/6-K files
   intraday or after the close, so that Open is not tradeable on the signal (see
   docs/VALIDATION_REPORT.md, issue 1.1). `entry_offset=1` enters at the next
   session's Open instead, matching the strategy's own description ("enter short the
   morning after the announcement"). Callers can report both.

2. Ratio filter under "All Ratios" — the notebook drops any event whose ratio is NaN
   (`if pd.isna(ratio): continue`). Because the forward-looking `early_edgar_splits`
   source has messier ratio strings, we only apply the ratio filter when a non-default
   bound is set; under the default (0, inf) an unparseable ratio still trades. This
   only changes behavior when you deliberately filter by ratio bucket.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

INF = float("inf")

# The chosen "Optimal Safe" strategy from analysis/strategy.md.
CHOSEN_STRATEGY = dict(
    hold_rule="day_of_split",
    stop_loss=0.40,
    take_profit=INF,      # None
    max_gap_up=0.30,      # skip if gaps up >30% on entry
    min_ratio=0,
    max_ratio=INF,        # All ratios
)


def _holding_window(future_data: pd.DataFrame, entry_date, t_split, hold_rule):
    """Return the slice of bars used for the holding period, per the hold rule.

    Mirrors the branch logic in the notebook's backtest_mega exactly.
    """
    if isinstance(hold_rule, int):
        return future_data.iloc[1 : hold_rule + 1]

    after_entry = future_data[future_data.index > entry_date]

    if hold_rule == "day_before_split":
        return future_data[(future_data.index > entry_date) & (future_data.index < t_split)]
    if hold_rule == "day_of_split":
        return future_data[(future_data.index > entry_date) & (future_data.index <= t_split)]
    if hold_rule == "day_after_split":
        post = after_entry[after_entry.index > t_split]
        if not post.empty:
            return after_entry.loc[: post.index[0]]
        return future_data[(future_data.index > entry_date) & (future_data.index <= t_split)]
    if hold_rule in ("5_days_after_split", "10_days_after_split", "14_days_after_split"):
        n = {"5_days_after_split": 5, "10_days_after_split": 10, "14_days_after_split": 14}[hold_rule]
        post = after_entry[after_entry.index >= t_split]
        if len(post) > n:
            return after_entry.loc[: post.index[n]]
        return after_entry
    return None


def backtest_mega(
    df_events: pd.DataFrame,
    prices: pd.DataFrame,
    hold_rule="day_of_split",
    stop_loss: float = 0.20,
    take_profit: float = INF,
    max_gap_up: float = INF,
    min_ratio: float = 0,
    max_ratio: float = INF,
    initial_capital: float = 10000.0,
    trade_pct: float = 0.05,
    slippage_and_fees: float = 0.015,
    entry_offset: int = 0,
) -> pd.DataFrame:
    """Run the short strategy over `df_events` using the `prices` OHLCV panel.

    Args:
        df_events: columns ['ticker', 't_ann', 't_split', 'ratio'] (t_* are Timestamps).
        prices: yfinance-style panel with a MultiIndex column (ticker, OHLCV) and a
            DatetimeIndex. `prices[ticker]['Open'/'High'/'Low'/'Close']`.
        entry_offset: 0 = enter at first session on/after t_ann (faithful to notebook);
            1 = enter at the next session (realistic "morning after").

    Returns:
        A DataFrame with one row per executed trade, including ticker, entry/exit
        prices and dates, net return, pnl dollars, the portfolio value after the trade,
        and the exit reason. Empty DataFrame if no trades executed.
    """
    ratio_filter_active = (min_ratio > 0) or (max_ratio < INF)

    try:
        available = set(prices.columns.levels[0])
    except AttributeError:
        available = set(prices.columns.get_level_values(0))

    portfolio_value = float(initial_capital)
    records = []

    df_events = df_events.sort_values("t_ann")

    for _, row in df_events.iterrows():
        ticker = row["ticker"]
        ratio = row.get("ratio", np.nan)

        # --- Ratio bucket filter (only when a real bound is set) ---
        if ratio_filter_active:
            if pd.isna(ratio) or ratio < min_ratio or ratio > max_ratio:
                continue

        if ticker not in available:
            continue
        ticker_data = prices[ticker].dropna(how="all")
        if ticker_data.empty:
            continue

        # --- Entry: Open of the (entry_offset-th) session on/after the announcement ---
        future_data = ticker_data[ticker_data.index >= row["t_ann"]]
        if len(future_data) <= entry_offset:
            continue
        future_data = future_data.iloc[entry_offset:]
        entry_price = future_data.iloc[0]["Open"]
        entry_date = future_data.index[0]
        if pd.isna(entry_price) or entry_price <= 0:
            continue

        # --- Gap-up filter: skip if the entry open gapped up too far vs prior close ---
        prev_data = ticker_data[ticker_data.index < entry_date]
        if not prev_data.empty:
            prev_close = prev_data.iloc[-1]["Close"]
            if pd.notna(prev_close) and prev_close > 0:
                gap_up = (entry_price - prev_close) / prev_close
                if gap_up > max_gap_up:
                    continue

        # --- Holding window per hold rule ---
        holding_data = _holding_window(future_data, entry_date, row["t_split"], hold_rule)
        if holding_data is None or holding_data.empty:
            continue

        # --- Position size: 5% of live portfolio value (per notebook) ---
        current_bet_size = portfolio_value * trade_pct

        # --- Stop-loss / take-profit (short side) ---
        stop_loss_price = entry_price * (1 + stop_loss)      # price up = loss on a short
        take_profit_price = entry_price * (1 - take_profit)  # price down = profit

        exit_price = None
        exit_date = None
        exit_reason = None
        for h_idx, h_row in holding_data.iterrows():
            if pd.notna(h_row["High"]) and h_row["High"] >= stop_loss_price:
                exit_price, exit_date, exit_reason = stop_loss_price, h_idx, "stop_loss"
                break
            if pd.notna(h_row["Low"]) and h_row["Low"] <= take_profit_price:
                exit_price, exit_date, exit_reason = take_profit_price, h_idx, "take_profit"
                break

        if exit_price is None:
            exit_price = holding_data.iloc[-1]["Open"]
            exit_date = holding_data.index[-1]
            exit_reason = "time_exit"
            if pd.isna(exit_price) or exit_price <= 0:
                continue

        raw_pct_return = (entry_price - exit_price) / entry_price  # + when price fell (short gain)
        net_pct_return = raw_pct_return - slippage_and_fees
        pnl = current_bet_size * net_pct_return
        portfolio_value += pnl

        records.append(
            dict(
                ticker=ticker,
                t_ann=row["t_ann"],
                t_split=row["t_split"],
                ratio=ratio,
                entry_date=entry_date,
                entry_price=entry_price,
                exit_date=exit_date,
                exit_price=exit_price,
                exit_reason=exit_reason,
                net_return=net_pct_return,
                pnl=pnl,
                portfolio_value=portfolio_value,
            )
        )

        if portfolio_value <= 0:
            portfolio_value = 0
            break

    return pd.DataFrame.from_records(records)


def summarize_trades(trades: pd.DataFrame, initial_capital: float = 10000.0) -> dict:
    """Compute headline metrics from a trades DataFrame produced by backtest_mega."""
    if trades is None or trades.empty:
        return dict(total_trades=0, win_rate=np.nan, total_return_pct=np.nan,
                    final_value=initial_capital, max_drawdown_pct=np.nan,
                    avg_return=np.nan, median_return=np.nan)

    equity = pd.concat([pd.Series([initial_capital]), trades["portfolio_value"].reset_index(drop=True)],
                       ignore_index=True)
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    final_value = float(trades["portfolio_value"].iloc[-1])

    return dict(
        total_trades=int(len(trades)),
        win_rate=float((trades["net_return"] > 0).mean() * 100),
        total_return_pct=float((final_value - initial_capital) / initial_capital * 100),
        final_value=final_value,
        max_drawdown_pct=float(drawdown.min() * 100),
        avg_return=float(trades["net_return"].mean() * 100),
        median_return=float(trades["net_return"].median() * 100),
    )
