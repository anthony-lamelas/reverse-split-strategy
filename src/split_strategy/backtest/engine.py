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


def split_factor(ticker_data: pd.DataFrame, t_split, ratio) -> Optional[float]:
    """The factor the series jumps by at `t_split`, or None if it does not jump.

    None means one of two things, and they are NOT distinguishable from prices alone:
    the provider already back-adjusted the series (so it is continuous), or the split
    never actually executed. Either way there is no mechanical jump to remove.

    This is the single source of truth for "is this series raw or adjusted?".
    `neutralize_split` uses it to decide what to divide by, and `price_basis` uses it
    to decide whether a quoted price needs converting back to real dollars - those two
    must never disagree, which is why the rule lives in one place.
    """
    if pd.isna(t_split):
        return None
    before = ticker_data[ticker_data.index < t_split]
    on_after = ticker_data[ticker_data.index >= t_split]
    if before.empty or on_after.empty:
        return None

    prev_close = before["Close"].iloc[-1]
    first_open = on_after["Open"].iloc[0]
    if not (pd.notna(prev_close) and pd.notna(first_open) and prev_close > 0):
        return None

    observed_jump = first_open / prev_close

    factor = None
    if pd.notna(ratio) and ratio and ratio > 1:
        # Declared ratio available: apply it if a broadly consistent jump is present.
        # Real trading moves alongside the split, so allow a wide tolerance band.
        if observed_jump >= ratio * 0.5:
            factor = float(ratio)
    if factor is None and observed_jump >= 1.8:
        # No usable declared ratio, but an unmistakable mechanical jump: use observed.
        factor = float(observed_jump)

    if factor is None or factor <= 1:
        return None
    return factor


def neutralize_split(ticker_data: pd.DataFrame, t_split, ratio) -> pd.DataFrame:
    """Remove the mechanical reverse-split price jump from an OHLC frame.

    WHY THIS IS REQUIRED: a reverse split multiplies the quoted price by the split
    factor overnight (1-for-10 => price x10), but it does NOT hurt a short position —
    the broker divides your share count by the same factor, so the position value is
    unchanged. Yahoo/yfinance frequently does NOT record micro-cap reverse splits in
    its adjustment table, so BOTH auto_adjust=True and auto_adjust=False leave the raw
    jump in the series. Left uncorrected, the backtest reads a 1-for-10 split as a
    +900% adverse move and fires a catastrophic false stop-loss.

    Fix: divide prices on/after the effective date by the split factor, making the
    series economically continuous (i.e. expressed in pre-split share terms).

    The adjustment is applied only when a jump consistent with the split is actually
    observed, so already-adjusted series are not double-adjusted.

    Returns a copy; the input is not mutated.
    """
    if pd.isna(t_split):
        return ticker_data

    before = ticker_data[ticker_data.index < t_split]
    on_after = ticker_data[ticker_data.index >= t_split]
    if before.empty or on_after.empty:
        return ticker_data

    factor = split_factor(ticker_data, t_split, ratio)
    if factor is None:
        return ticker_data  # already adjusted, or no split jump present

    adjusted = ticker_data.copy()
    mask = adjusted.index >= t_split
    for col in ("Open", "High", "Low", "Close"):
        if col in adjusted.columns:
            adjusted.loc[mask, col] = adjusted.loc[mask, col] / factor
    if "Volume" in adjusted.columns:
        adjusted["Volume"] = adjusted["Volume"].astype(float)
        adjusted.loc[mask, "Volume"] = adjusted.loc[mask, "Volume"] * factor
    return adjusted


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
    entry_anchor: str = "t_ann",
    adjust_for_split: bool = True,
    slippage_model: str = "flat",
    max_spread_pct: float = INF,
    spread_lookback: int = 20,
) -> pd.DataFrame:
    """Run the short strategy over `df_events` using the `prices` OHLCV panel.

    Args:
        df_events: columns ['ticker', 't_ann', 't_split', 'ratio'] (t_* are Timestamps).
        prices: yfinance-style panel with a MultiIndex column (ticker, OHLCV) and a
            DatetimeIndex. `prices[ticker]['Open'/'High'/'Low'/'Close']`.
        entry_offset: 0 = enter at first session on/after the anchor (faithful to
            notebook); 1 = enter at the next session (realistic "morning after").
        entry_anchor: which event date entry is measured from - "t_ann" (default,
            the announcement: the validated strategy) or "t_split" (the effective
            date, for testing whether the drift continues AFTER the split). Note
            "t_split" requires an integer `hold_rule`; see the guard below.
        slippage_model: "flat" uses `slippage_and_fees` for every trade (the original
            behavior, preserved so published results stay reproducible).
            "spread_estimated" replaces it with a per-trade Corwin-Schultz bid-ask
            spread estimate — far more realistic on sub-$1 names, where a flat 1.5%
            badly understates the cost of crossing the spread twice.
        max_spread_pct: skip trades whose estimated spread exceeds this. Mirrors the
            live spread filter, so the backtest can answer what refusing the
            worst-priced names does to the edge.
        adjust_for_split: neutralize the mechanical reverse-split price jump (default
            True, and required for correctness). Set False only to reproduce the
            original notebook's buggy behavior for comparison.

    Returns:
        A DataFrame with one row per executed trade, including ticker, entry/exit
        prices and dates, net return, pnl dollars, the portfolio value after the trade,
        and the exit reason. Empty DataFrame if no trades executed.
    """
    # An empty event set is a legitimate outcome (e.g. a walk-forward fold or a date
    # filter that matched nothing). `build_events_*` can return a column-less empty
    # frame, so bail before touching df_events["t_ann"].
    if entry_anchor not in ("t_ann", "t_split"):
        raise ValueError(f"entry_anchor must be 't_ann' or 't_split', got {entry_anchor!r}")

    # Anchoring entry to the split makes every split-relative hold rule degenerate:
    # entry_date is then >= t_split, so "day_of_split" asks for bars after entry and
    # on/before the split and gets an empty frame. `_holding_window` returning empty
    # makes the loop `continue`, so the whole run would report zero trades and read as
    # "the hypothesis failed" when nothing was ever tested. Refuse instead.
    if entry_anchor == "t_split" and not isinstance(hold_rule, int):
        raise ValueError(
            f"entry_anchor='t_split' requires an integer hold_rule (a number of "
            f"sessions to hold after entry); got hold_rule={hold_rule!r}, which is "
            f"measured relative to the split and would select an empty window."
        )

    if df_events is None or df_events.empty:
        return pd.DataFrame()

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

        # Remove the mechanical reverse-split jump; a short is not harmed by a split
        # because the broker adjusts share count too. See neutralize_split().
        if adjust_for_split:
            ticker_data = neutralize_split(ticker_data, row["t_split"], ratio)

        # --- Entry: Open of the (entry_offset-th) session on/after the anchor date ---
        anchor_date = row["t_ann"] if entry_anchor == "t_ann" else row["t_split"]
        if pd.isna(anchor_date):
            continue
        future_data = ticker_data[ticker_data.index >= anchor_date]
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

        # --- Execution cost: flat, or estimated from the bid-ask spread ---
        trade_cost = slippage_and_fees
        if slippage_model == "spread_estimated" or max_spread_pct < INF:
            from .slippage import estimate_spread, round_trip_cost

            spread = estimate_spread(ticker_data, entry_date, lookback=spread_lookback)
            if pd.notna(spread) and spread > max_spread_pct:
                continue  # too expensive to trade; mirrors the live spread veto
            if slippage_model == "spread_estimated":
                # Fall back to the flat assumption when the spread is unknowable
                # rather than silently pricing the trade as free.
                trade_cost = round_trip_cost(spread, base_cost=0.0) if pd.notna(spread) \
                    else slippage_and_fees

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
        net_pct_return = raw_pct_return - trade_cost
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
