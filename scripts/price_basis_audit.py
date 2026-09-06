#!/usr/bin/env python3
"""Settle the price-basis question with the provider's own split table.

    python scripts/price_basis_audit.py            # fetch actions, then audit
    python scripts/price_basis_audit.py --no-fetch # reuse DATA/yahoo_split_actions.pkl

What it establishes
-------------------
1. `DATA/prices_full.pkl` is split-adjusted, as a MEASUREMENT rather than an
   inference: for each split the provider reports, check whether the panel is
   continuous across it.
2. The adjustment is cumulative, so the panel's entry price is scaled by EVERY
   later split, not just the event's own. Dividing by the event ratio - what
   `backtest.price_basis` does - is the right idea applied once too few times.
3. Because of that, a high panel entry price is mostly a statement about splits
   that had not happened yet at entry. Any rule that filters or buckets on the
   panel price is therefore selecting on the future.

Writes DATA/yahoo_split_actions.pkl and DATA/survivorship_coverage.csv (the file
`build_prices.py` has always defined and never produced).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "src"))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from split_strategy.backtest.split_actions import (  # noqa: E402
    DEFAULT_ACTIONS_PATH, DEFAULT_COVERAGE_PATH, classify_applied,
    fetch_split_actions, quoted_entry_prices, quoted_factors)

FLOOR = 1.00


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true",
                    help="reuse the cached action table instead of downloading")
    ap.add_argument("--events", default="DATA/events_combined_cache.pkl")
    ap.add_argument("--prices", default="DATA/prices_full.pkl")
    ap.add_argument("--out", default="analysis/price_basis_audit.md")
    args = ap.parse_args()

    events = pd.read_pickle(ROOT / args.events)
    prices = pd.read_pickle(ROOT / args.prices)
    cache = ROOT / DEFAULT_ACTIONS_PATH

    covcache = ROOT / DEFAULT_COVERAGE_PATH
    if args.no_fetch:
        if not cache.exists():
            print(f"No cached action table at {cache}; drop --no-fetch to build it.")
            return 1
        actions = pd.read_pickle(cache)
        served = (set(pd.read_csv(covcache)["ticker"].str.upper())
                  if covcache.exists() else None)
    else:
        tickers = sorted({str(t).upper() for t in events["ticker"].unique()})
        print(f"Fetching split actions for {len(tickers)} tickers ...")
        actions, served_list = fetch_split_actions(tickers)
        cache.parent.mkdir(parents=True, exist_ok=True)
        actions.to_pickle(cache)
        pd.DataFrame({"ticker": served_list}).to_csv(covcache, index=False)
        served = set(served_list)
        print(f"Wrote {cache} ({len(actions)} split records) and {covcache}")

    cls = classify_applied(prices, actions)
    factors = quoted_factors(prices, cls)
    basis = quoted_entry_prices(events, prices, factors, entry_offset=1, classified=cls)
    priced = basis[basis["panel_price"].notna()]

    out: list[str] = []
    A = out.append
    A("# Price basis, settled against the provider's split table\n")
    A(f"_Generated {pd.Timestamp.now().date()} · `scripts/price_basis_audit.py` · "
      f"events {len(events)} · panel {prices.index.min().date()} → "
      f"{prices.index.max().date()}_\n")

    A("## 1. Is the panel split-adjusted?\n")
    inside = cls[cls["verdict"] != "outside_panel"]
    n_app = int((inside["verdict"] == "applied").sum())
    n_not = int((inside["verdict"] == "not_applied").sum())
    A(f"- Split records from the provider: **{len(cls)}**, "
      f"{len(inside)} of them inside the panel's date range.")
    A(f"- Panel is continuous across the split (**adjusted**): **{n_app}**")
    A(f"- Panel jumps at the split (**not adjusted**): **{n_not}**")
    if len(inside):
        cutoff = prices.index.max() - pd.Timedelta(days=30)
        tail = inside[pd.to_datetime(inside["split_date"]) >= cutoff]
        old = inside[pd.to_datetime(inside["split_date"]) < cutoff]
        A(f"- Of the unadjusted ones, "
          f"**{int((tail['verdict'] == 'not_applied').sum())}** fall in the panel's "
          f"final 30 days — provider ingestion lag, not a property of the company.")
        A(f"- Before that cutoff: {int((old['verdict'] == 'applied').sum())} adjusted "
          f"vs {int((old['verdict'] == 'not_applied').sum())} not "
          f"({100 * (old['verdict'] == 'applied').mean():.1f}% adjusted).\n")
    A("This is the question `backtest/price_basis.py` records as unanswerable "
      "(\"prices alone cannot separate those\"). Prices alone cannot. The provider "
      "publishes the split it applied, and that does.\n")

    A("## 2. The adjustment is cumulative\n")
    A("| Splits absorbed AFTER the entry bar | Events | Median panel price | Median quoted price |")
    A("|---:|---:|---:|---:|")
    g = priced.groupby("later_splits")
    for k, s in g:
        A(f"| {int(k)} | {len(s)} | ${s.panel_price.median():,.2f} | "
          f"${s.quoted_price.median():,.3f} |")
    A("")
    A("The panel price climbs with the number of splits still to come; the quoted "
      "price does not. A filter on the panel price is a filter on the future.\n")

    A("## 3. Entry price by basis\n")
    A("| Basis | Events priced | Median | Share ≥ $1.00 | Share ≥ $5.00 |")
    A("|---|---:|---:|---:|---:|")
    for label, col in (("Panel (what every backtest reads)", "panel_price"),
                       ("Quoted (reconstructed)", "quoted_price")):
        s = priced[col].dropna()
        A(f"| {label} | {len(s)} | ${s.median():,.2f} | "
          f"{100 * (s >= FLOOR).mean():.1f}% | {100 * (s >= 5).mean():.1f}% |")
    naive = priced["panel_price"] / priced["ratio"]
    ok = naive.notna() & priced["quoted_price"].notna()
    err = (naive[ok] / priced["quoted_price"][ok])
    A(f"| Naive quoted (panel ÷ event ratio) | {int(ok.sum())} | "
      f"${naive[ok].median():,.2f} | {100 * (naive[ok] >= FLOOR).mean():.1f}% | "
      f"{100 * (naive[ok] >= 5).mean():.1f}% |")
    A("")
    A(f"The naive conversion agrees with the reconstruction on only "
      f"**{100 * (err.sub(1).abs() < 0.01).mean():.0f}%** of events; it is more than "
      f"2x too high on {100 * (err > 2).mean():.0f}% and more than 2x too low on "
      f"{100 * (err < 0.5).mean():.0f}%. It flips the $1.00 floor decision for "
      f"**{int(((naive[ok] >= FLOOR) != (priced['quoted_price'][ok] >= FLOOR)).sum())}** "
      f"of {int(ok.sum())} events.\n")

    A("## 4. Survivorship coverage\n")
    try:
        in_panel = set(prices.columns.levels[0])
    except AttributeError:
        in_panel = set(prices.columns.get_level_values(0))
    ev_t = events["ticker"].str.upper()
    missing = sorted(set(ev_t) - in_panel)
    A(f"- Event tickers: {ev_t.nunique()}; present in the panel: {len(set(ev_t) & in_panel)}")
    A(f"- Events whose ticker is absent from the panel: "
      f"**{int(ev_t.isin(missing).sum())}** of {len(events)}")
    if served is None:
        A("- Attrition since the panel was built: not measured "
          f"(`{DEFAULT_COVERAGE_PATH}` absent; re-run without `--no-fetch`).\n")
    else:
        gone = sorted(in_panel - served)
        A(f"- Tickers in the panel the provider no longer serves: **{len(gone)}** "
          f"of {len(in_panel)} ({100 * len(gone) / max(len(in_panel), 1):.1f}%), "
          f"measured {(pd.Timestamp.now().normalize() - prices.index.max()).days} "
          f"days after the panel's last bar.\n")

    cov = pd.DataFrame([dict(
        logged_at=pd.Timestamp.now().isoformat(),
        window_start=prices.index.min().strftime("%Y-%m-%d"),
        window_end=prices.index.max().strftime("%Y-%m-%d"),
        tickers_requested=int(ev_t.nunique()),
        tickers_with_data=int(len(set(ev_t) & in_panel)),
        tickers_missing=int(len(missing)),
        events_total=int(len(events)),
        events_dropped=int(ev_t.isin(missing).sum()),
        missing_tickers=";".join(missing),
    )])
    covpath = ROOT / "DATA" / "survivorship_coverage.csv"
    cov.to_csv(covpath, mode="a", header=not covpath.exists(), index=False)
    A(f"_Coverage row appended to `DATA/survivorship_coverage.csv`._\n")

    text = "\n".join(out) + "\n"
    dest = ROOT / args.out
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    print(text)
    print(f"Wrote {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
