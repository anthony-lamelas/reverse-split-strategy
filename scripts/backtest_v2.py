#!/usr/bin/env python3
"""Backtest v2: the deployed rule on executable events and unadjusted prices.

    python scripts/backtest_v2.py pull              # export events_v2 -> DATA/events_v2.json
    python scripts/backtest_v2.py run               # development window, primary rule + grid
    python scripts/backtest_v2.py run --placebo 200 # add the placebo benchmark
    python scripts/backtest_v2.py run --holdout     # the held-out year. ONCE.

What is tested, how, and what counts as a pass is fixed in
analysis/backtest_v2_preregistration.md. Read it before changing anything here.
"""
import argparse
import itertools
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "src"))

from split_strategy import config  # noqa: E402
from split_strategy.analysis.stats import bootstrap_ci, t_stat  # noqa: E402
from split_strategy.backtest import sim  # noqa: E402
from split_strategy.backtest.events import executable_events  # noqa: E402
from split_strategy.backtest.prices_v2 import PolygonPrices  # noqa: E402

EVENTS_PATH = config.DATA_DIR / "events_v2.json"
HOLDOUT_START = pd.Timestamp("2025-10-01")
ANNOUNCE_PRIMARY = sim.Rule(entry="live", exit_sessions_before=1, stop=0.40, target=0.20, min_price=1.00)
ANNOUNCE_GRID = [sim.Rule(entry=e, exit_sessions_before=x, stop=s, target=t, min_price=f)
        for e, x, s, t, f in itertools.product(
            ("live", "first_open"), (1, 0), (None, 0.40, 0.25), (0.20, None), (None, 0.50, 1.00))]


LS_PRIMARY = sim.LastSessionRule(cover="close", known="live", stop=None, min_price=None)
LS_GRID = [sim.LastSessionRule(cover=c, known=k, stop=st, min_price=f)
           for c, k, st, f in itertools.product(
               ("close", "next_open"), ("live", "first_open"), (None, 0.40), (None, 0.50, 1.00))]


def pull() -> int:
    """Export the classified filings from MongoDB through the deployed Modal app."""
    os.environ.setdefault("MODAL_PROFILE", config.MODAL_PROFILE)
    import modal

    docs = modal.Function.from_name("reverse-split-strategy", "export_events_v2").remote()
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVENTS_PATH.write_text(json.dumps(docs, default=str))
    dates = sorted(d["filing_date"] for d in docs if d.get("filing_date"))
    print(f"{len(docs)} definitive filings -> {EVENTS_PATH} "
          f"({dates[0] if dates else '?'} .. {dates[-1] if dates else '?'})")
    return 0


def summarize(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"n": 0}
    r = trades["net_return"].to_numpy()
    lo, hi, _ = bootstrap_ci(r)
    return {"n": len(r), "mean": r.mean(), "t": t_stat(r), "ci_lo": lo, "ci_hi": hi,
            "win": (r > 0).mean(), "days": trades["days"].mean(),
            "gross": trades["gross_return"].mean()}


def fmt(s: dict) -> str:
    if not s.get("n"):
        return "no trades"
    return (f"n={s['n']:4d}  net {s['mean']:+7.2%}  t={s['t']:+5.2f}  "
            f"CI [{s['ci_lo']:+.2%}, {s['ci_hi']:+.2%}]  win {s['win']:.0%}  "
            f"gross {s['gross']:+.2%}  hold {s['days']:.1f}d")


def run(args) -> int:
    if not EVENTS_PATH.exists():
        print(f"{EVENTS_PATH} not found - run `backtest_v2.py pull` first.")
        return 1
    events = executable_events(json.loads(EVENTS_PATH.read_text()))
    if events.empty:
        print("No executable events.")
        return 1
    held = events["entry_live"] >= HOLDOUT_START
    events = events[held] if args.holdout else events[~held]
    if args.start:
        events = events[events["entry_live"] >= pd.Timestamp(args.start)]
    if args.end:
        events = events[events["entry_live"] <= pd.Timestamp(args.end)]
    if args.max_events:
        events = events.tail(args.max_events)
    print(f"{'HOLDOUT' if args.holdout else 'Development'} window: {len(events)} executable "
          f"events, {events['ticker'].nunique()} tickers, "
          f"{events['entry_live'].min().date()} .. {events['entry_live'].max().date()}")

    source = PolygonPrices()
    lo = events["entry_first_open"].min() - pd.Timedelta(days=7)
    hi = min(events["t_split"].max() + pd.Timedelta(days=14), pd.Timestamp.now().normalize())
    bars, splits = {}, {}
    tickers = sorted(events["ticker"].unique())
    for i, ticker in enumerate(tickers, 1):
        try:
            bars[ticker], splits[ticker] = source.daily(ticker, lo, hi), source.splits(ticker)
        except Exception as e:
            print(f"  {ticker}: price fetch failed ({str(e)[:80]})")
            bars[ticker], splits[ticker] = pd.DataFrame(), None
        if i % 50 == 0:
            print(f"  prices: {i}/{len(tickers)}")
    unpriced = [t for t in tickers if bars[t].empty]
    print(f"Tickers with no price history: {len(unpriced)} of {len(tickers)}"
          + (f" ({', '.join(unpriced[:15])}{'...' if len(unpriced) > 15 else ''})" if unpriced else ""))

    costs = sim.Costs()
    last = args.variant == "last"
    PRIMARY, GRID = (LS_PRIMARY, LS_GRID) if last else (ANNOUNCE_PRIMARY, ANNOUNCE_GRID)
    runner = sim.run_last_session if last else sim.run_rule

    def go(evs, rule, k=1.0):
        return runner(evs, bars.get, splits.get, rule, costs.scaled(k))

    print(f"\n== PRIMARY: {PRIMARY.label()}")
    for k in (0.0, 1.0, 2.0):
        trades, skipped = go(events, PRIMARY, k)
        print(f"  costs x{k:.0f}: {fmt(summarize(trades))}")
    print(f"  skipped: {dict(skipped)}")
    stuck = skipped.get("no_exit_bar", 0)
    if stuck and not trades.empty:
        worst = np.append(go(events, PRIMARY)[0]["net_return"].to_numpy(), [-1.0] * stuck)
        print(f"  if the {stuck} unclosable trade(s) each lost 100%: mean {worst.mean():+.2%}")
    primary_trades = go(events, PRIMARY)[0]
    if not primary_trades.empty:
        print("  exits:", dict(primary_trades["exit_reason"].value_counts()))
        for label, kw in (("deployed caps ($50, 1/day)", {}),
                          ("2% of equity, no daily cap", {"max_notional": None, "max_new_per_day": None})):
            p = sim.portfolio(primary_trades, **kw)
            print(f"  $5,000 account, {label}: {p['taken']} trades, "
                  f"{p['annualized']:+.1%}/yr (margin-skipped {p['skipped_margin']})")

    if args.placebo:
        rng = np.random.default_rng(12345)
        real = summarize(primary_trades).get("mean", float("nan"))
        means = []
        for _ in range(args.placebo):
            t, _ = go(sim.placebo_events(events, rng), PRIMARY)
            if not t.empty:
                means.append(t["net_return"].mean())
        means = np.array(means)
        print(f"\n== PLACEBO ({len(means)} ticker-shuffled runs, primary rule, costs x1)")
        print(f"  real {real:+.2%} | placebo mean {means.mean():+.2%}, p95 "
              f"{np.percentile(means, 95):+.2%} | runs >= real: {(means >= real).sum()}")

    if not args.no_grid:
        print("\n== SENSITIVITY GRID (costs x1) - reported in full, not searched")
        for rule in GRID:
            trades, _ = go(events, rule)
            print(f"  {rule.label():64s} {fmt(summarize(trades))}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Backtest v2")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pull")
    r = sub.add_parser("run")
    r.add_argument("--holdout", action="store_true", help="evaluate the held-out year (once)")
    r.add_argument("--placebo", type=int, default=0, help="number of placebo runs")
    r.add_argument("--no-grid", action="store_true")
    r.add_argument("--start", default=None, help="ignore events entered before this date")
    r.add_argument("--end", default=None, help="ignore events entered after this date")
    r.add_argument("--variant", choices=("announce", "last"), default="announce",
                   help="announce: hold from the announcement (deployed). "
                        "last: the last-session variant")
    r.add_argument("--max-events", type=int, default=0, help="most recent N events (smoke test)")
    args = ap.parse_args()
    return pull() if args.cmd == "pull" else run(args)


if __name__ == "__main__":
    sys.exit(main())
