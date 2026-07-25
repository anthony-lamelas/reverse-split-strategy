#!/usr/bin/env python3
"""Build the OHLCV price pickle the backtest engine expects.

Fetches unadjusted (auto_adjust=False) yfinance data for a set of tickers over a date
range and writes a MultiIndex (ticker, field) panel to a pickle. Tickers can come from
the recent event set (default) or an explicit --tickers list.

Usage:
  # Build from recent early_edgar events (last N days):
  python scripts/build_prices.py --from-events --days 90 --out DATA/prices_recent.pkl
  # Build from an explicit list:
  python scripts/build_prices.py --tickers AAPL,MSFT --start 2026-05-01 --end 2026-07-25
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "src"))

from split_strategy.backtest.prices import fetch_prices
from split_strategy.backtest.events import build_events_from_early_edgar


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-events", action="store_true", help="derive tickers/date-range from recent early_edgar events")
    ap.add_argument("--days", type=int, default=90, help="lookback window when using --from-events")
    ap.add_argument("--tickers", default=None, help="comma-separated ticker list (overrides --from-events)")
    ap.add_argument("--start", default=None, help="YYYY-MM-DD (required with --tickers)")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD (required with --tickers)")
    ap.add_argument("--out", default="DATA/prices_recent.pkl", help="output pickle path")
    args = ap.parse_args()

    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
        if not (args.start and args.end):
            ap.error("--start and --end are required with --tickers")
        start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    else:
        as_of = pd.Timestamp.now().normalize()
        events = build_events_from_early_edgar(require_executed=True, as_of=as_of)
        events = events[events["t_split"] >= as_of - pd.Timedelta(days=args.days)]
        if events.empty:
            print("No recent events found; nothing to fetch.")
            return
        tickers = sorted(events["ticker"].unique())
        start = events["t_ann"].min() - pd.Timedelta(days=7)
        end = events["t_split"].max() + pd.Timedelta(days=10)

    print(f"Fetching {len(tickers)} tickers, {pd.Timestamp(start).date()} -> {pd.Timestamp(end).date()} ...")
    panel, missing = fetch_prices(tickers, start, end)
    if panel.empty:
        print("No price data returned.")
        return
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    panel.to_pickle(out)
    got = len(set(panel.columns.get_level_values(0)))
    print(f"Wrote {out}  ({got} tickers with data, {len(missing)} missing)")
    if missing:
        print("Missing:", ", ".join(missing[:40]) + (" ..." if len(missing) > 40 else ""))


if __name__ == "__main__":
    main()
