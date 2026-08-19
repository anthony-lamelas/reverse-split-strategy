#!/usr/bin/env python3
"""Re-validate Strategy B with a minimum entry price applied FROM THE START.

Why this exists
---------------
Partitioning the existing 560 pooled out-of-sample trades by entry price showed
the edge is monotonic in price and unmeasurable at the bottom:

    <$1     n=29   mean +5.88%   t=0.46   95% CI [-20.6%, +32.3%]
    $1-5    n=110  mean +8.10%   t=3.09   95% CI [ +2.8%, +13.4%]
    >=$5    n=421  mean +20.52%  t=18.66  95% CI [+18.4%, +22.7%]

But those buckets were chosen AFTER seeing the results, which is exactly how you
manufacture an edge that isn't there. A post-hoc slice of one walk-forward is not
a validation.

This re-runs the whole walk-forward with the floor applied to the event universe
before any parameter selection happens, so the grid search only ever sees the
names the live system would actually trade. That makes the resulting figure an
honest out-of-sample expectation rather than a filtered summary of an old one.

    python scripts/price_floor_walkforward.py                 # $1.00 floor
    python scripts/price_floor_walkforward.py --floor 5.00
    python scripts/price_floor_walkforward.py --floor 0       # unfiltered baseline
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "src"))

from split_strategy import margin as mgn  # noqa: E402
from split_strategy.backtest.walkforward import walk_forward  # noqa: E402

INITIAL = 10_000.0
TRADE_PCT = 0.02


def entry_prices(events: pd.DataFrame, prices: pd.DataFrame) -> pd.Series:
    """Open on the first session strictly after the announcement (Strategy B entry)."""
    tickers = set(prices.columns.get_level_values(0))
    out = []
    for _, ev in events.iterrows():
        if ev["ticker"] not in tickers:
            out.append(np.nan)
            continue
        try:
            opens = prices[ev["ticker"]]["Open"].dropna()
        except Exception:
            out.append(np.nan)
            continue
        future = opens[opens.index > ev["t_ann"]]
        out.append(float(future.iloc[0]) if len(future) else np.nan)
    return pd.Series(out, index=events.index)


def summarize(trades: pd.DataFrame, label: str) -> str:
    if trades.empty:
        return f"{label}: no trades"
    r = trades["net_return"]
    n = len(r)
    mean = r.mean()
    se = r.std(ddof=1) / np.sqrt(n) if n > 1 else np.nan
    t = mean / se if se and se > 0 else np.nan
    lo, hi = (mean - 1.96 * se, mean + 1.96 * se) if se and se > 0 else (np.nan, np.nan)
    return (f"{label}\n"
            f"  trades        {n}\n"
            f"  win rate      {100 * (r > 0).mean():.1f}%\n"
            f"  mean/trade    {100 * mean:+.2f}%\n"
            f"  t-stat        {t:.2f}\n"
            f"  95% CI        [{100 * lo:+.2f}%, {100 * hi:+.2f}%]\n"
            f"  cumulative    {100 * r.sum():+.0f}%")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--floor", type=float, default=1.00,
                    help="minimum entry price; 0 disables the filter")
    ap.add_argument("--out", default="analysis/price_floor_walkforward.md")
    args = ap.parse_args()

    events = pd.read_pickle(ROOT / "DATA" / "events_combined_cache.pkl")
    prices = pd.read_pickle(ROOT / "DATA" / "prices_full.pkl")

    events = events.copy()
    events["entry_price"] = entry_prices(events, prices)
    priced = events["entry_price"].notna()

    if args.floor > 0:
        keep = priced & (events["entry_price"] >= args.floor)
        print(f"Filtering to entry price >= ${args.floor:.2f}: "
              f"{keep.sum()} of {priced.sum()} priced events kept "
              f"({100 * keep.sum() / priced.sum():.0f}%)")
        filtered = events[keep].drop(columns=["entry_price"])
    else:
        print(f"No price floor: {priced.sum()} priced events")
        filtered = events[priced].drop(columns=["entry_price"])

    print(f"Running walk-forward on {len(filtered)} events (this takes a while) ...")
    folds = walk_forward(
        filtered, prices, test_window_days=60, min_initial_events=50,
        min_test_events=5, selection_metric="t_stat", min_grid_trades=15,
        entry_offset=1, adjust_for_split=True, initial_capital=INITIAL,
        trade_pct=TRADE_PCT,
    )
    frames = [f["oos_trades"] for f in folds if not f["oos_trades"].empty]
    if not frames:
        print("No out-of-sample trades produced. Floor may be too restrictive.")
        return 1
    pooled = pd.concat(frames, ignore_index=True).sort_values("entry_date")

    label = (f"Strategy B, entry price >= ${args.floor:.2f}" if args.floor > 0
             else "Strategy B, no price floor")
    report = summarize(pooled, label)
    print("\n" + report)

    # Margin efficiency: the binding constraint once the cheap tail is excluded.
    if "entry_price" in pooled.columns:
        mult = pooled["entry_price"].map(mgn.margin_multiple)
        med = mult.median()
        extra = (f"\n  margin mult   {med:.1f}x (median)\n"
                 f"  ret/margin$   {100 * pooled['net_return'].mean() / med:+.2f}%")
        print(extra)
        report += extra

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        f"# Price-floor walk-forward\n\n"
        f"_Generated {pd.Timestamp.now().date()} · floor ${args.floor:.2f} · "
        f"{len(folds)} folds_\n\n"
        f"Parameter selection ran on the filtered universe, so this is an honest\n"
        f"out-of-sample expectation rather than a post-hoc slice of an older run.\n\n"
        f"```\n{report}\n```\n",
        encoding="utf-8")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
