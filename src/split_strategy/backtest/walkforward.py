"""Walk-forward validation.

Answers "does this strategy have a real edge, or did the grid search just find the
best-fitting noise?" For each rolling test window: optimize parameters using ONLY
events announced before the window (in-sample), then evaluate those frozen
parameters on the window itself (out-of-sample, never seen by the optimizer). Only
the pooled out-of-sample results are a legitimate estimate of live performance — the
in-sample number is discarded (it's definitionally the best the grid could do on data
it already knows).
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from .engine import backtest_mega, summarize_trades
from .gridsearch import run_grid


def make_folds(df_events: pd.DataFrame, test_window_days: int = 60,
               min_initial_events: int = 50, min_test_events: int = 5) -> list[tuple]:
    """Chronological (test_start, test_end) windows, in-sample = everything before test_start."""
    ev = df_events.sort_values("t_ann")
    start, end = ev["t_ann"].min(), ev["t_ann"].max()

    boundary = start
    while (ev["t_ann"] < boundary).sum() < min_initial_events and boundary < end:
        boundary += pd.Timedelta(days=7)

    folds = []
    while boundary < end:
        test_end = boundary + pd.Timedelta(days=test_window_days)
        n_test = int(((ev["t_ann"] >= boundary) & (ev["t_ann"] < test_end)).sum())
        is_last = test_end >= end
        if n_test >= min_test_events or (is_last and n_test > 0):
            folds.append((boundary, test_end))
        boundary = test_end
    return folds


def walk_forward(
    df_events: pd.DataFrame,
    prices: pd.DataFrame,
    test_window_days: int = 60,
    min_initial_events: int = 50,
    min_test_events: int = 5,
    selection_metric: str = "total_return_pct",
    min_grid_trades: int = 15,
    entry_offset: int = 1,
    adjust_for_split: bool = True,
    initial_capital: float = 10000.0,
    trade_pct: float = 0.05,
    slippage_and_fees: float = 0.015,
    **grid_kwargs,
) -> list[dict]:
    """Run walk-forward validation. Returns one dict per fold with in/out-of-sample detail.

    selection_metric: which column of the in-sample grid to optimize ('total_return_pct'
        for return-maximizing selection, 't_stat' for a robustness-favoring selection
        that penalizes noisy small samples).
    """
    folds = make_folds(df_events, test_window_days, min_initial_events, min_test_events)
    results = []

    for test_start, test_end in folds:
        in_sample = df_events[df_events["t_ann"] < test_start]
        out_sample = df_events[(df_events["t_ann"] >= test_start) & (df_events["t_ann"] < test_end)]
        if in_sample.empty or out_sample.empty:
            continue

        grid = run_grid(in_sample, prices, entry_offset=entry_offset, adjust_for_split=adjust_for_split,
                        initial_capital=initial_capital, trade_pct=trade_pct,
                        slippage_and_fees=slippage_and_fees, min_trades=min_grid_trades, **grid_kwargs)
        if grid.empty:
            continue
        # Only scrub inf/NaN in the metric being sorted on - stop_loss/take_profit/
        # max_gap_up/max_ratio legitimately use inf to mean "no constraint" and must
        # not be touched here.
        metric_col = grid[selection_metric].replace([np.inf, -np.inf], np.nan)
        grid = grid[metric_col.notna()]
        if grid.empty:
            continue

        best = grid.sort_values(selection_metric, ascending=False).iloc[0].to_dict()
        params = dict(hold_rule=best["hold_rule"], stop_loss=best["stop_loss"],
                     take_profit=best["take_profit"], max_gap_up=best["max_gap_up"],
                     min_ratio=best["min_ratio"], max_ratio=best["max_ratio"])

        oos_trades = backtest_mega(out_sample, prices, entry_offset=entry_offset,
                                   adjust_for_split=adjust_for_split, initial_capital=initial_capital,
                                   trade_pct=trade_pct, slippage_and_fees=slippage_and_fees, **params)
        oos_summary = summarize_trades(oos_trades, initial_capital)

        results.append(dict(
            test_start=test_start, test_end=test_end,
            in_sample_n=len(in_sample), out_sample_n=len(out_sample),
            out_sample_events=out_sample,
            selected_params=params,
            in_sample_metric_value=best[selection_metric],
            in_sample_summary={k: best[k] for k in
                              ("total_trades", "win_rate", "total_return_pct", "t_stat", "max_drawdown_pct")},
            oos_trades=oos_trades,
            oos_summary=oos_summary,
        ))
    return results


def pooled_oos_trades(fold_results: list[dict]) -> pd.DataFrame:
    """Concatenate all folds' out-of-sample trades (each still has its own net_return)."""
    frames = [f["oos_trades"] for f in fold_results if not f["oos_trades"].empty]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values("entry_date").reset_index(drop=True)


def compounded_oos_curve(pooled: pd.DataFrame, initial_capital: float = 10000.0,
                         trade_pct: float = 0.05, max_exposure: float = float("inf")) -> pd.DataFrame:
    """Replay pooled OOS trades as ONE event-driven equity curve, respecting actual
    holding periods and (optionally) a cap on total concurrent notional exposure.

    Trades routinely overlap in time (see the concurrency check run during analysis:
    median 13-55 positions open at once in this dataset). Naively applying each
    trade's P&L in entry-date order — as if every trade resolves instantly before the
    next one is sized — lets gains compound before they've actually been realized and
    assumes unlimited buying power. This function instead processes OPEN and CLOSE as
    separate events in chronological order (closes before opens on the same date, to
    free capital first): a new position is only opened if the capital it would commit,
    plus what's already committed to other open positions, fits under
    `max_exposure * portfolio_value`. Trades that don't fit are SKIPPED (a real missed
    signal under real capital constraints), not silently deferred or discounted.

    max_exposure=inf reproduces "unlimited buying power" (upper bound, not realistic).
    max_exposure=1.0 models full cash collateral only (realistic default for
    non-marginable short sales, which most of these microcaps are).

    Returns a DataFrame with one row per CLOSE event (chronological), plus attrs
    'n_taken' and 'n_skipped' for the count of trades actually opened vs. skipped for
    lack of capital.
    """
    if pooled.empty:
        return pd.DataFrame()

    trades = pooled.reset_index(drop=True).copy()
    trades["entry_date"] = pd.to_datetime(trades["entry_date"])
    trades["exit_date"] = pd.to_datetime(trades["exit_date"])

    events = []
    for i, t in trades.iterrows():
        events.append((t["entry_date"], 1, i, "open"))
        events.append((t["exit_date"], 0, i, "close"))
    events.sort(key=lambda e: (e[0], e[1]))

    portfolio_value = float(initial_capital)
    open_positions = {}  # idx -> (bet_size, net_return, ticker)
    committed = 0.0
    n_taken = n_skipped = 0
    rows = []

    for date, _, idx, kind in events:
        if kind == "close":
            if idx in open_positions:
                bet_size, net_return, ticker = open_positions.pop(idx)
                committed -= bet_size
                pnl = bet_size * net_return
                portfolio_value += pnl
                rows.append(dict(entry_date=trades.loc[idx, "entry_date"], exit_date=date,
                                 ticker=ticker, net_return=net_return, pnl=pnl,
                                 portfolio_value=portfolio_value))
                if portfolio_value <= 0:
                    portfolio_value = 0
        else:
            prospective = portfolio_value * trade_pct
            if committed + prospective <= max_exposure * portfolio_value:
                open_positions[idx] = (prospective, trades.loc[idx, "net_return"], trades.loc[idx, "ticker"])
                committed += prospective
                n_taken += 1
            else:
                n_skipped += 1

    out = pd.DataFrame(rows)
    out.attrs["n_taken"] = n_taken
    out.attrs["n_skipped"] = n_skipped
    return out
