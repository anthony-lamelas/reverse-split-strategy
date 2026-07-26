#!/usr/bin/env python3
"""Realistic, capital-constrained replay of the walk-forward OOS trades.

The compounding equity curves reported in walk_forward_analysis.py size every trade
at 5% of CURRENT portfolio value with no check on how much capital is already tied up
in other open positions. With a median of 13-55 concurrent open positions in the
pooled OOS set, that assumes unlimited buying power, which is not real. This script
event-simulates capital properly: opens are only taken if there's enough uncommitted
capital under a max total exposure cap; trades that don't fit are SKIPPED (a real
missed signal, not a free lunch). Short sales on non-marginable securities generally
require full cash collateral, so a 100% exposure cap is a reasonable (if anything,
generous) default.
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "src"))


def simulate_capped(trades: pd.DataFrame, initial_capital=10000.0, trade_pct=0.05, max_exposure=1.0):
    trades = trades.copy()
    trades["entry_date"] = pd.to_datetime(trades["entry_date"])
    trades["exit_date"] = pd.to_datetime(trades["exit_date"])

    events = []
    for i, t in trades.iterrows():
        events.append((t["entry_date"], 0, i, "open"))   # opens process before closes on tie... see below
        events.append((t["exit_date"], 1, i, "close"))
    # Process CLOSES before OPENS on the same date (free capital first) -> close sorts first (tag 0), open tag1
    events = []
    for i, t in trades.iterrows():
        events.append((t["entry_date"], 1, i, "open"))
        events.append((t["exit_date"], 0, i, "close"))
    events.sort(key=lambda e: (e[0], e[1]))

    portfolio_value = float(initial_capital)
    open_positions = {}  # idx -> (bet_size, net_return)
    committed = 0.0
    n_skipped = 0
    n_taken = 0
    equity_log = []

    for date, _, idx, kind in events:
        if kind == "close":
            if idx in open_positions:
                bet_size, net_return = open_positions.pop(idx)
                committed -= bet_size
                pnl = bet_size * net_return
                portfolio_value += pnl
                equity_log.append((date, portfolio_value))
        else:  # open
            prospective = portfolio_value * trade_pct
            if committed + prospective <= max_exposure * portfolio_value:
                open_positions[idx] = (prospective, trades.loc[idx, "net_return"])
                committed += prospective
                n_taken += 1
            else:
                n_skipped += 1

    return dict(final_value=portfolio_value, n_taken=n_taken, n_skipped=n_skipped,
               equity_log=equity_log, total_trades=len(trades))


def main():
    for label, path in [("Return-maximizing", "DATA/wf_pooled_return_max.pkl"),
                        ("Stability (t-stat)", "DATA/wf_pooled_tstat.pkl")]:
        trades = pd.read_pickle(ROOT / path)
        print(f"=== {label} ({len(trades)} total OOS trades) ===")
        for cap in (1.0, 2.0, 5.0, 100.0):
            r = simulate_capped(trades, max_exposure=cap)
            ret = (r["final_value"] - 10000) / 10000 * 100
            label_cap = "unconstrained" if cap >= 100 else f"{cap*100:.0f}% max exposure"
            print(f"  {label_cap:<22}: taken={r['n_taken']:>4}/{r['total_trades']}, "
                 f"skipped={r['n_skipped']:>4}, final=${r['final_value']:>14,.0f} ({ret:+.1f}%)")
        print()


if __name__ == "__main__":
    main()
