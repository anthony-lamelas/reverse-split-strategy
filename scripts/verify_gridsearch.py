#!/usr/bin/env python3
"""Spot-check that the optimized gridsearch.run_grid matches engine.backtest_mega exactly."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "src"))

from split_strategy.backtest.engine import backtest_mega, summarize_trades
from split_strategy.backtest.gridsearch import run_grid, HOLD_RULES, STOP_LOSSES, TAKE_PROFITS, GAP_UPS, RATIO_BUCKETS
from split_strategy.backtest.events import build_events_combined

events = pd.read_pickle(ROOT / "DATA" / "events_combined_cache.pkl")
prices = pd.read_pickle(ROOT / "DATA" / "prices_full.pkl")

# Sample a handful of permutations spanning different hold rules / filters.
samples = [
    dict(hold_rule="day_of_split", stop_loss=0.40, take_profit=float("inf"), max_gap_up=0.30, min_ratio=0, max_ratio=float("inf")),
    dict(hold_rule=5, stop_loss=0.20, take_profit=0.40, max_gap_up=0.10, min_ratio=0, max_ratio=float("inf")),
    dict(hold_rule="5_days_after_split", stop_loss=0.25, take_profit=float("inf"), max_gap_up=float("inf"), min_ratio=10, max_ratio=50),
    dict(hold_rule="day_before_split", stop_loss=0.15, take_profit=0.20, max_gap_up=0.30, min_ratio=0, max_ratio=10),
    dict(hold_rule=14, stop_loss=float("inf"), take_profit=0.60, max_gap_up=float("inf"), min_ratio=0, max_ratio=float("inf")),
]

all_ok = True
for i, p in enumerate(samples):
    ref_trades = backtest_mega(events, prices, entry_offset=1, adjust_for_split=True, **p)
    ref = summarize_trades(ref_trades)

    grid = run_grid(events, prices, hold_rules=[p["hold_rule"]], stop_losses=[p["stop_loss"]],
                    take_profits=[p["take_profit"]], gap_ups=[p["max_gap_up"]],
                    ratio_buckets=[(p["min_ratio"], p["max_ratio"])], entry_offset=1, adjust_for_split=True)
    if grid.empty:
        opt = dict(total_trades=0, win_rate=np.nan, total_return_pct=np.nan)
    else:
        opt = grid.iloc[0].to_dict()

    ok = (ref["total_trades"] == opt["total_trades"]) and \
         (np.isnan(ref["win_rate"]) == np.isnan(opt["win_rate"]) or abs(ref["win_rate"] - opt["win_rate"]) < 1e-6) and \
         (np.isnan(ref["total_return_pct"]) == np.isnan(opt["total_return_pct"]) or abs(ref["total_return_pct"] - opt["total_return_pct"]) < 1e-6)
    all_ok &= ok
    print(f"[{i}] {p['hold_rule']} sl={p['stop_loss']} tp={p['take_profit']} gap={p['max_gap_up']} ratio=({p['min_ratio']},{p['max_ratio']})")
    print(f"     ref : trades={ref['total_trades']}, win={ref['win_rate']:.4f}, return={ref['total_return_pct']:.4f}")
    print(f"     grid: trades={opt['total_trades']}, win={opt['win_rate']:.4f}, return={opt['total_return_pct']:.4f}")
    print(f"     MATCH: {ok}")

print()
print("ALL MATCH" if all_ok else "MISMATCH DETECTED")
sys.exit(0 if all_ok else 1)
