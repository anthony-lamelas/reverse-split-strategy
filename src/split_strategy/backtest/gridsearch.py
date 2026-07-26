"""Optimized grid search over the strategy parameter space.

The naive approach — calling `backtest_mega` once per permutation — redoes the same
expensive work (ticker slicing, split neutralization, entry lookup, holding-window
slicing) for every one of the 4,620 combinations, even though almost all of it is
shared across permutations. For walk-forward testing (many folds, each re-optimizing
over a growing in-sample set), that's computationally infeasible (hours-to-days).

This module precomputes, ONCE per event:
  - the split-neutralized ticker series (independent of any grid parameter)
  - the entry price/date and gap-up % (independent of hold_rule/stop/tp/ratio)
  - the holding-window bars for EACH of the 11 hold_rule options (not each of the
    4,620 permutations — hold_rule x n_events, not permutations x n_events)

Then evaluating one of the 4,620 permutations is a cheap numpy scan over already-
sliced bars plus a small Python loop for the sequential portfolio compounding.
Produces numerically identical results to calling `engine.backtest_mega` for the same
parameters (see `scripts/verify_gridsearch.py` for a spot-check against it).
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from .engine import INF, neutralize_split, _holding_window

# The original grid from analysis/strategy.ipynb: 11 x 7 x 5 x 3 x 4 = 4,620.
HOLD_RULES = [1, 2, 5, 10, 14, "day_before_split", "day_of_split", "day_after_split",
              "5_days_after_split", "10_days_after_split", "14_days_after_split"]
STOP_LOSSES = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, INF]
TAKE_PROFITS = [0.20, 0.40, 0.60, 0.80, INF]
GAP_UPS = [0.10, 0.30, INF]
RATIO_BUCKETS = [(0, INF), (0, 10), (10, 50), (50, INF)]


def _prepare_events(df_events: pd.DataFrame, prices: pd.DataFrame, entry_offset: int,
                    adjust_for_split: bool) -> list[dict]:
    """One-time per-event prep: neutralized series, entry price/date, gap-up %."""
    try:
        available = set(prices.columns.levels[0])
    except AttributeError:
        available = set(prices.columns.get_level_values(0))

    prepared = []
    for _, row in df_events.sort_values("t_ann").iterrows():
        ticker = row["ticker"]
        ratio = row.get("ratio", np.nan)
        if ticker not in available:
            continue
        ticker_data = prices[ticker].dropna(how="all")
        if ticker_data.empty:
            continue
        if adjust_for_split:
            ticker_data = neutralize_split(ticker_data, row["t_split"], ratio)

        future_data = ticker_data[ticker_data.index >= row["t_ann"]]
        if len(future_data) <= entry_offset:
            continue
        future_data = future_data.iloc[entry_offset:]
        entry_price = future_data.iloc[0]["Open"]
        entry_date = future_data.index[0]
        if pd.isna(entry_price) or entry_price <= 0:
            continue

        prev_data = ticker_data[ticker_data.index < entry_date]
        gap_up = np.nan
        if not prev_data.empty:
            prev_close = prev_data.iloc[-1]["Close"]
            if pd.notna(prev_close) and prev_close > 0:
                gap_up = (entry_price - prev_close) / prev_close

        prepared.append(dict(
            ticker=ticker, t_ann=row["t_ann"], t_split=row["t_split"], ratio=ratio,
            entry_price=float(entry_price), entry_date=entry_date, gap_up=gap_up,
            future_data=future_data,
        ))
    return prepared


def _precompute_holding_bars(prepared: list[dict], hold_rules: list) -> dict:
    """For each (event index, hold_rule), precompute High/Low/Open numpy arrays."""
    bars = {}
    for i, ev in enumerate(prepared):
        for hr in hold_rules:
            hd = _holding_window(ev["future_data"], ev["entry_date"], ev["t_split"], hr)
            if hd is None or hd.empty:
                bars[(i, hr)] = None
                continue
            bars[(i, hr)] = dict(
                high=hd["High"].to_numpy(dtype=float),
                low=hd["Low"].to_numpy(dtype=float),
                open=hd["Open"].to_numpy(dtype=float),
                dates=hd.index.to_numpy(),
            )
    return bars


def _simulate(prepared: list[dict], bars: dict, hold_rule, stop_loss: float, take_profit: float,
             max_gap_up: float, min_ratio: float, max_ratio: float,
             initial_capital: float, trade_pct: float, slippage_and_fees: float) -> pd.DataFrame:
    """Run one permutation over precomputed events/bars. Sequential (compounding)."""
    ratio_filter_active = (min_ratio > 0) or (max_ratio < INF)
    portfolio_value = float(initial_capital)
    records = []

    for i, ev in enumerate(prepared):
        ratio = ev["ratio"]
        if ratio_filter_active and (pd.isna(ratio) or ratio < min_ratio or ratio > max_ratio):
            continue
        if pd.notna(ev["gap_up"]) and ev["gap_up"] > max_gap_up:
            continue

        b = bars.get((i, hold_rule))
        if b is None or len(b["high"]) == 0:
            continue

        entry_price = ev["entry_price"]
        stop_loss_price = entry_price * (1 + stop_loss)
        take_profit_price = entry_price * (1 - take_profit)

        trig_stop = np.where(b["high"] >= stop_loss_price)[0]
        trig_tp = np.where(b["low"] <= take_profit_price)[0]
        first_stop = trig_stop[0] if len(trig_stop) else None
        first_tp = trig_tp[0] if len(trig_tp) else None

        if first_stop is not None and (first_tp is None or first_stop <= first_tp):
            exit_price, exit_date, exit_reason = stop_loss_price, b["dates"][first_stop], "stop_loss"
        elif first_tp is not None:
            exit_price, exit_date, exit_reason = take_profit_price, b["dates"][first_tp], "take_profit"
        else:
            exit_price = b["open"][-1]
            exit_date = b["dates"][-1]
            exit_reason = "time_exit"
            if not np.isfinite(exit_price) or exit_price <= 0:
                continue

        current_bet_size = portfolio_value * trade_pct
        raw_pct_return = (entry_price - exit_price) / entry_price
        net_pct_return = raw_pct_return - slippage_and_fees
        pnl = current_bet_size * net_pct_return
        portfolio_value += pnl

        records.append((ev["ticker"], ev["t_ann"], exit_date, net_pct_return, pnl, portfolio_value))

        if portfolio_value <= 0:
            portfolio_value = 0
            break

    if not records:
        return pd.DataFrame(columns=["ticker", "t_ann", "exit_date", "net_return", "pnl", "portfolio_value"])
    return pd.DataFrame.from_records(
        records, columns=["ticker", "t_ann", "exit_date", "net_return", "pnl", "portfolio_value"])


def _summary(trades: pd.DataFrame, initial_capital: float) -> dict:
    if trades.empty:
        return dict(total_trades=0, win_rate=np.nan, total_return_pct=np.nan,
                    avg_return=np.nan, median_return=np.nan, std_return=np.nan,
                    t_stat=np.nan, max_drawdown_pct=np.nan, final_value=initial_capital)
    n = len(trades)
    r = trades["net_return"].to_numpy()
    final_value = float(trades["portfolio_value"].iloc[-1])
    equity = np.concatenate([[initial_capital], trades["portfolio_value"].to_numpy()])
    running_max = np.maximum.accumulate(equity)
    dd = (equity - running_max) / running_max
    std = float(r.std(ddof=1)) if n > 1 else np.nan
    # Floor the std for t-stat purposes: a handful of trades that exit at a fixed
    # take-profit price can have near-zero variance (mechanically identical % move),
    # which blows the t-stat up toward infinity - a sample-size artifact, not real
    # robustness. 2% is well below realistic per-trade return variability in this
    # domain (typically 15-25%), so it only bites these degenerate cases.
    std_floor = 0.02
    tstat = float(r.mean() / (max(std, std_floor) / np.sqrt(n))) if n > 1 else np.nan
    return dict(
        total_trades=n,
        win_rate=float((r > 0).mean() * 100),
        total_return_pct=float((final_value - initial_capital) / initial_capital * 100),
        avg_return=float(r.mean() * 100),
        median_return=float(np.median(r) * 100),
        std_return=float(std * 100) if std == std else np.nan,
        t_stat=tstat,
        max_drawdown_pct=float(dd.min() * 100),
        final_value=final_value,
    )


def run_grid(
    df_events: pd.DataFrame,
    prices: pd.DataFrame,
    hold_rules: Optional[list] = None,
    stop_losses: Optional[list] = None,
    take_profits: Optional[list] = None,
    gap_ups: Optional[list] = None,
    ratio_buckets: Optional[list] = None,
    entry_offset: int = 1,
    adjust_for_split: bool = True,
    initial_capital: float = 10000.0,
    trade_pct: float = 0.05,
    slippage_and_fees: float = 0.015,
    min_trades: int = 0,
) -> pd.DataFrame:
    """Run the full (or a custom) parameter grid; return one summary row per permutation.

    `min_trades`: if > 0, permutations with fewer executed trades are dropped from the
    output (they're statistically meaningless and just clutter "best of grid" rankings).
    """
    hold_rules = HOLD_RULES if hold_rules is None else hold_rules
    stop_losses = STOP_LOSSES if stop_losses is None else stop_losses
    take_profits = TAKE_PROFITS if take_profits is None else take_profits
    gap_ups = GAP_UPS if gap_ups is None else gap_ups
    ratio_buckets = RATIO_BUCKETS if ratio_buckets is None else ratio_buckets

    if df_events.empty:
        return pd.DataFrame()

    prepared = _prepare_events(df_events, prices, entry_offset, adjust_for_split)
    if not prepared:
        return pd.DataFrame()
    bars = _precompute_holding_bars(prepared, hold_rules)

    rows = []
    for hr in hold_rules:
        for sl in stop_losses:
            for tp in take_profits:
                for gap in gap_ups:
                    for (rmin, rmax) in ratio_buckets:
                        trades = _simulate(prepared, bars, hr, sl, tp, gap, rmin, rmax,
                                          initial_capital, trade_pct, slippage_and_fees)
                        s = _summary(trades, initial_capital)
                        if s["total_trades"] < min_trades:
                            continue
                        rows.append(dict(hold_rule=hr, stop_loss=sl, take_profit=tp,
                                         max_gap_up=gap, min_ratio=rmin, max_ratio=rmax, **s))
    return pd.DataFrame(rows)
