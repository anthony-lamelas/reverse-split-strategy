#!/usr/bin/env python3
"""Backtest the chosen "Optimal Safe" strategy over the recent window.

Event source: the forward-looking `early_edgar_splits` collection (the same source
the live signal generator reads), filtered to splits that executed within the last
N days. Prices: unadjusted yfinance OHLCV. Strategy: day_of_split / 40% stop / no
take-profit / skip >30% gap-up / all ratios.

Outputs three views to analysis/recent_backtest_results.md and stdout:
  1. All signals (baseline)
  2. Shortability breakdown (how many Schwab likely couldn't short + P&L split)
  3. Shortable-only (the realistic expectation)

Usage:
  python scripts/backtest_recent.py [--days 60] [--min-confidence High] [--save-prices]
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "src"))

from split_strategy.backtest.engine import backtest_mega, summarize_trades, CHOSEN_STRATEGY
from split_strategy.backtest.events import build_events_from_early_edgar
from split_strategy.backtest.prices import fetch_prices
from split_strategy.backtest.shortability import classify_shortability, load_exchange_map

INITIAL_CAPITAL = 10000.0


def _fmt(summary: dict) -> str:
    if summary["total_trades"] == 0:
        return "no trades"
    return (
        f"trades={summary['total_trades']}, "
        f"win_rate={summary['win_rate']:.1f}%, "
        f"total_return={summary['total_return_pct']:+.2f}%, "
        f"avg={summary['avg_return']:+.2f}%, median={summary['median_return']:+.2f}%, "
        f"maxDD={summary['max_drawdown_pct']:.2f}%"
    )


def _subset_pnl(trades: pd.DataFrame, mask) -> dict:
    sub = trades[mask]
    return dict(
        n=int(len(sub)),
        win_rate=float((sub["net_return"] > 0).mean() * 100) if len(sub) else float("nan"),
        total_pnl=float(sub["pnl"].sum()),
        avg_return=float(sub["net_return"].mean() * 100) if len(sub) else float("nan"),
    )


def run(days: int, min_confidence, save_prices: bool, use_cached: bool = False) -> str:
    as_of = pd.Timestamp.now().normalize()
    cutoff = as_of - pd.Timedelta(days=days)

    events = build_events_from_early_edgar(min_confidence=min_confidence, require_executed=True, as_of=as_of)
    if events.empty:
        return "No events found in early_edgar_splits."
    events = events[events["t_split"] >= cutoff].reset_index(drop=True)
    if events.empty:
        return f"No executed events with t_split in the last {days} days."

    tickers = sorted(events["ticker"].unique())
    start = events["t_ann"].min() - pd.Timedelta(days=7)
    end = events["t_split"].max() + pd.Timedelta(days=10)  # buffer for post-split survival check
    cache_pkl = ROOT / "DATA" / "prices_recent.pkl"

    if use_cached and cache_pkl.exists():
        prices = pd.read_pickle(cache_pkl)
        have = set(prices.columns.get_level_values(0))
        missing = [t for t in tickers if t not in have]
        print(f"Loaded cached price panel from {cache_pkl} ({len(have)} tickers).")
    else:
        print(f"Fetching prices for {len(tickers)} tickers, {start.date()} -> {end.date()} ...")
        prices, missing = fetch_prices(tickers, start, end)
        if save_prices and not prices.empty:
            prices.to_pickle(cache_pkl)
            print(f"Saved price panel -> {cache_pkl}")

    exchange_map = load_exchange_map()

    # Two entry conventions: 0 = same-session open (optimistic, faithful to notebook),
    # 1 = next session open (realistic "morning after the announcement").
    results = {}
    for label, offset in [("optimistic (announcement-day open)", 0), ("realistic (next-day open)", 1)]:
        trades = backtest_mega(events, prices, entry_offset=offset, **CHOSEN_STRATEGY, initial_capital=INITIAL_CAPITAL)
        if not trades.empty:
            trades = classify_shortability(trades, prices, exchange_map)
        results[label] = trades

    # Build the report on the realistic run; show optimistic as a sensitivity line.
    realistic = results["realistic (next-day open)"]
    lines = []
    lines.append("# Recent Backtest — Chosen 'Optimal Safe' Strategy\n")
    lines.append(f"_Generated {as_of.date()} · window = last {days} days · source = `early_edgar_splits`_\n")
    lines.append("**Strategy:** enter short the morning after the SEC announcement, exit at the open on the "
                 "execution date; 40% stop-loss; no take-profit; skip if the entry gaps up >30%; all ratios. "
                 "Position size = 5% of equity per trade; 1.5% flat slippage/fees.\n")
    lines.append("> **Methodology note.** This uses the forward-looking `early_edgar_splits` scanner as the event "
                 "source (what the live bot sees), not the original Tier A/B historical join. Numbers are therefore "
                 "**not directly comparable** to the published 574-trade / 60.97% baseline. Sample is small; treat "
                 "as directional, not statistically significant.\n")

    lines.append(f"- Executed events in window: **{len(events)}** ({len(tickers)} tickers)")
    lines.append(f"- Tickers with no yfinance price data (delisted/OTC/unmapped): **{len(missing)}** "
                 f"{'— ' + ', '.join(missing) if missing and len(missing) <= 25 else ''}")
    lines.append("")

    # View 1: baseline
    s_real = summarize_trades(realistic, INITIAL_CAPITAL)
    s_opt = summarize_trades(results["optimistic (announcement-day open)"], INITIAL_CAPITAL)
    lines.append("## 1. All signals (baseline)\n")
    lines.append(f"- **Realistic (next-day entry):** {_fmt(s_real)}")
    lines.append(f"- Optimistic (same-day entry, look-ahead): {_fmt(s_opt)}")
    lines.append("")

    if realistic.empty:
        lines.append("_No trades executed on the realistic run (insufficient price data)._")
        report = "\n".join(lines)
        (ROOT / "analysis" / "recent_backtest_results.md").write_text(report, encoding="utf-8")
        return report

    # View 2: shortability breakdown
    n = len(realistic)
    n_unshort = int(realistic["likely_unshortable"].sum())
    pct_unshort = n_unshort / n * 100
    short_stats = _subset_pnl(realistic, ~realistic["likely_unshortable"])
    unshort_stats = _subset_pnl(realistic, realistic["likely_unshortable"])
    lines.append("## 2. Shortability breakdown (Schwab realism)\n")
    lines.append(f"**{n_unshort} of {n} trades ({pct_unshort:.0f}%) were likely NOT shortable at Schwab** "
                 f"(proxy classifier — see docs).\n")
    lines.append("| Subset | Trades | Win rate | Sum P&L ($) | Avg return |")
    lines.append("|---|---:|---:|---:|---:|")
    lines.append(f"| Shortable (tradeable) | {short_stats['n']} | {short_stats['win_rate']:.1f}% | "
                 f"{short_stats['total_pnl']:+,.2f} | {short_stats['avg_return']:+.2f}% |")
    lines.append(f"| Likely unshortable | {unshort_stats['n']} | {unshort_stats['win_rate']:.1f}% | "
                 f"{unshort_stats['total_pnl']:+,.2f} | {unshort_stats['avg_return']:+.2f}% |")
    lines.append("")
    # reason breakdown (aggregated by category, not by unique dollar figure)
    def _category(reason: str) -> str:
        r = reason.lower()
        if "exchange" in r or "unlisted" in r:
            return "OTC / non-major exchange"
        if "price" in r or "marginable" in r:
            return "sub-$1 price (non-marginable)"
        if "liquidity" in r:
            return "thin liquidity (no locate)"
        if "warrant" in r:
            return "warrant/unit/right"
        if "delisted" in r or "halted" in r:
            return "delisted/halted after split"
        return "other"

    cats = realistic[realistic["likely_unshortable"]]["primary_reason"].map(_category).value_counts()
    if not cats.empty:
        lines.append("Primary unshortable reason (by category):")
        for cat, cnt in cats.items():
            lines.append(f"- {cat}: {cnt}")
        lines.append("")

    # View 3: shortable-only
    shortable_trades = _rerun_subset(events, prices, exchange_map, realistic)
    s_short = summarize_trades(shortable_trades, INITIAL_CAPITAL)
    lines.append("## 3. Shortable-only (realistic expectation)\n")
    lines.append("Re-run of the strategy restricted to the tradeable subset (portfolio compounding recomputed "
                 "on just those trades):\n")
    lines.append(f"- {_fmt(s_short)}")
    lines.append("")

    # Trade log
    lines.append("## Trade log (realistic run)\n")
    lines.append("| Ticker | Entry | Exit | Entry $ | Exit $ | Reason | Net % | Shortable? |")
    lines.append("|---|---|---|---:|---:|---|---:|:--:|")
    for _, t in realistic.sort_values("entry_date").iterrows():
        lines.append(
            f"| {t['ticker']} | {pd.Timestamp(t['entry_date']).date()} | {pd.Timestamp(t['exit_date']).date()} | "
            f"{t['entry_price']:.2f} | {t['exit_price']:.2f} | {t['exit_reason']} | {t['net_return']*100:+.1f}% | "
            f"{'no' if t['likely_unshortable'] else 'yes'} |"
        )
    lines.append("")
    lines.append("_Shortability is a proxy estimate; exact historical Schwab borrow data is not retrievable. "
                 "See docs/VALIDATION_REPORT.md and the plan for caveats._")

    report = "\n".join(lines)
    (ROOT / "analysis" / "recent_backtest_results.md").write_text(report, encoding="utf-8")
    return report


def _rerun_subset(events, prices, exchange_map, classified_trades) -> pd.DataFrame:
    """Re-run the backtest over only the tickers/events classified as shortable."""
    shortable_keys = set(
        zip(classified_trades.loc[~classified_trades["likely_unshortable"], "ticker"],
            pd.to_datetime(classified_trades.loc[~classified_trades["likely_unshortable"], "t_split"]))
    )
    mask = events.apply(lambda r: (r["ticker"], pd.Timestamp(r["t_split"])) in shortable_keys, axis=1)
    sub_events = events[mask].reset_index(drop=True)
    if sub_events.empty:
        return pd.DataFrame()
    return backtest_mega(sub_events, prices, entry_offset=1, **CHOSEN_STRATEGY, initial_capital=INITIAL_CAPITAL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60, help="lookback window in days (by execution date)")
    ap.add_argument("--min-confidence", default=None, choices=[None, "High", "Medium", "Low"],
                    help="minimum scanner confidence to include")
    ap.add_argument("--save-prices", action="store_true", help="cache the price panel to DATA/prices_recent.pkl")
    ap.add_argument("--use-cached", action="store_true", help="reuse DATA/prices_recent.pkl if present (skip yfinance)")
    args = ap.parse_args()

    report = run(args.days, args.min_confidence, args.save_prices, args.use_cached)
    print("\n" + "=" * 80)
    print(report)


if __name__ == "__main__":
    main()
