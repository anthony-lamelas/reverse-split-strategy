#!/usr/bin/env python3
"""Tail-risk mitigation test.

Walk-forward validation found "no stop-loss" was the winning parameter in EVERY
single fold, for both selection philosophies. That's a real edge (removing the stop
avoids getting shaken out by ordinary volatility), but it also means every trade
carries open-ended downside - the worst trade in the pooled out-of-sample set was
SBET at -709% (a ~-35% single-trade portfolio hit at 5% sizing). A 2-year sample may
simply not contain a true black-swan squeeze.

This script isolates the effect of ADDING a stop back in, holding everything else
(hold_rule, take_profit, gap filter, ratio bucket) fixed to what each fold's optimizer
actually selected. For a range of "catastrophic-only" stop caps (well above where the
optimizer would ever choose to stop out on normal volatility), it re-simulates each
fold's out-of-sample events and reports the tradeoff: how much expectancy/equity does
a wide stop cost, versus how much it caps the single-trade tail?

Usage: python scripts/tail_risk_test.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "src"))

from split_strategy.backtest.engine import backtest_mega, summarize_trades
from split_strategy.backtest.walkforward import walk_forward, compounded_oos_curve

INITIAL_CAPITAL = 10000.0
TRADE_PCT = 0.05
STOP_CAPS = [float("inf"), 3.00, 2.00, 1.50, 1.00, 0.75, 0.50]  # inf = baseline (no stop)


def run_with_forced_stop(fold_results, stop_cap, entry_offset=1, adjust_for_split=True):
    """Re-simulate each fold's OOS events with hold/TP/gap/ratio frozen as selected,
    but stop_loss forced to `stop_cap` (instead of whatever the optimizer picked -
    which was always inf/None in this dataset)."""
    frames = []
    for f in fold_results:
        p = dict(f["selected_params"])
        p["stop_loss"] = stop_cap
        trades = backtest_mega(f["out_sample_events"], PRICES, entry_offset=entry_offset,
                               adjust_for_split=adjust_for_split, initial_capital=INITIAL_CAPITAL,
                               trade_pct=TRADE_PCT, **p)
        if not trades.empty:
            frames.append(trades)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values("entry_date").reset_index(drop=True)


def report(label, fold_results):
    print(f"\n{'='*100}\n{label}\n{'='*100}")
    print(f"{'Stop cap':<14}{'Trades':>8}{'Win%':>8}{'Mean/trade':>12}{'Worst trade':>13}"
         f"{'100%-cap equity':>20}{'MaxDD':>9}{'Stopped-out':>13}")
    for cap in STOP_CAPS:
        pooled = run_with_forced_stop(fold_results, cap)
        if pooled.empty:
            continue
        r = pooled["net_return"].to_numpy()
        n_stopped = int((pooled["exit_reason"] == "stop_loss").sum())
        curve = compounded_oos_curve(pooled, INITIAL_CAPITAL, TRADE_PCT, max_exposure=1.0)
        final = curve["portfolio_value"].iloc[-1] if not curve.empty else INITIAL_CAPITAL
        ret = (final - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
        dd = np.nan
        if not curve.empty:
            running_max = curve["portfolio_value"].cummax()
            dd = ((curve["portfolio_value"] - running_max) / running_max).min() * 100
        cap_label = "None" if cap == float("inf") else f"{cap*100:.0f}%"
        print(f"{cap_label:<14}{len(pooled):>8}{(r>0).mean()*100:>7.1f}%{r.mean()*100:>11.2f}%"
             f"{r.min()*100:>12.1f}%{f'${final:,.0f} ({ret:+.0f}%)':>20}{dd:>8.1f}%{n_stopped:>13}")


def main():
    global PRICES
    events = pd.read_pickle(ROOT / "DATA" / "events_combined_cache.pkl")
    PRICES = pd.read_pickle(ROOT / "DATA" / "prices_full.pkl")

    print("Re-running walk-forward to recover per-fold selected params + OOS event sets...")
    folds_a = walk_forward(events, PRICES, test_window_days=60, min_initial_events=50, min_test_events=5,
                           selection_metric="total_return_pct", min_grid_trades=15,
                           entry_offset=1, adjust_for_split=True,
                           initial_capital=INITIAL_CAPITAL, trade_pct=TRADE_PCT)
    folds_b = walk_forward(events, PRICES, test_window_days=60, min_initial_events=50, min_test_events=5,
                           selection_metric="t_stat", min_grid_trades=15,
                           entry_offset=1, adjust_for_split=True,
                           initial_capital=INITIAL_CAPITAL, trade_pct=TRADE_PCT)

    report("Return-maximizing selection (day_before_split-dominant)", folds_a)
    report("Stability-favoring selection (day_of_split-dominant)", folds_b)

    print("\nNote: 'Trades' count can shift slightly vs. the main walk-forward report because "
         "adding a stop changes which bar a trade exits on (stop vs. time_exit), which can change "
         "downstream compounding but not which events qualify.")


if __name__ == "__main__":
    main()
