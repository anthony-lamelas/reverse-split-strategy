#!/usr/bin/env python3
"""Does the drift continue AFTER the reverse split executes?

    python scripts/postsplit_entry_test.py
    python scripts/postsplit_entry_test.py --holds 5,10,20 --out analysis/postsplit_entry.md

The question
------------
The live system refuses any entry under $1.00. A reverse split mechanically lifts
the quoted price above $1 - a 1-for-10 turns $0.10 into $1.00 - so the obvious
thought is to short these names after the split instead of before, and get the
cheap-stock problems for free.

Half of that reasoning is wrong and half is right, and only one half is testable
here:

  WRONG   The split does not reduce exposure. The broker divides the share count by
          the same factor it multiplies the price by, so the position is worth
          exactly what it was. `engine.neutralize_split` exists for this reason.

  RIGHT   Above $1 the name becomes marginable and borrowable, the FINRA 4210(c)
          $2.50/share floor stops costing 25x notional and costs 2.5x, and a
          one-cent spread stops being 10% of the price. Those are real and large -
          but they are execution economics, and a price backtest cannot see them.

So this script tests only the part prices can answer: entered at the effective date
and held N sessions, do these names fall enough to pay for the trade?

What it does NOT do
-------------------
It does not claim the answer transfers to live trading unchanged. Borrow cost is
applied as a sensitivity, not a certainty, and a reverse split shrinks the float -
so borrow on exactly these names tends to spike at exactly the wrong moment.
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "src"))

# The report is written as UTF-8 and contains arrows and inequality signs. Echoing it
# to a cp1252 console raises UnicodeEncodeError - the same failure that took down the
# dashboard and the kill switch. Never let printing a result destroy the run that
# produced it; the file on disk keeps the real characters either way.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from split_strategy.analysis.stats import (borrow_adjusted, bootstrap_ci,  # noqa: E402
                                           t_stat)
from split_strategy.backtest.engine import INF, backtest_mega  # noqa: E402
from split_strategy.backtest.events import build_events_combined  # noqa: E402
from split_strategy.backtest.price_basis import entry_basis, summarize_basis  # noqa: E402

# Match the LIVE configuration, not the notebook's CHOSEN_STRATEGY. Live runs with
# STOP_LOSS_PCT=0.40, TAKE_PROFIT_PCT=0.20 (run_trading.TAKE_PROFIT_PCT) and
# MAX_GAP_UP_PCT=inf (the walk-forward selected no gap filter in 9 of 11 folds).
# Testing the parameters that are actually deployed is the only version of this
# result that means anything operationally.
LIVE_PARAMS = dict(stop_loss=0.40, take_profit=0.20, max_gap_up=INF)
FLOOR = 1.00
BORROW_RATES = (0.0, 0.25, 0.50, 1.00, 2.00)


def describe(trades: pd.DataFrame, label: str) -> dict:
    """Headline statistics for one arm of the test."""
    if trades is None or trades.empty:
        return dict(label=label, n=0)
    r = trades["net_return"].to_numpy(dtype=float)
    lo, hi, p_pos = bootstrap_ci(r)
    days = (pd.to_datetime(trades["exit_date"])
            - pd.to_datetime(trades["entry_date"])).dt.days.clip(lower=1)
    return dict(
        label=label, n=len(r), mean=float(r.mean()), median=float(np.median(r)),
        win_rate=float((r > 0).mean()), t=t_stat(r), ci_lo=lo, ci_hi=hi,
        p_positive=p_pos, hold_days=float(days.mean()),
        total_return=float(trades["portfolio_value"].iloc[-1] / 10_000.0 - 1.0),
    )


def row(d: dict) -> str:
    if d.get("n", 0) == 0:
        return f"| {d['label']} | 0 | - | - | - | - | - |"
    return (f"| {d['label']} | {d['n']} | {d['mean']*100:+.2f}% | "
            f"{d['win_rate']*100:.0f}% | {d['t']:+.2f} | "
            f"[{d['ci_lo']*100:+.2f}%, {d['ci_hi']*100:+.2f}%] | "
            f"{d['hold_days']:.0f}d |")


HEADER = ("| Arm | Trades | Mean/trade | Win rate | t | 95% CI (mean) | Avg hold |\n"
          "|---|---:|---:|---:|---:|---|---:|")


def period_table(trades: pd.DataFrame) -> str:
    """Mean return by calendar half-year.

    A pooled mean hides a result that lived in one quarter and died. This is the
    cheap version of a walk-forward: if the sign flips period to period, the pooled
    number is describing history, not an edge.
    """
    if trades is None or trades.empty:
        return "_no trades_\n"
    t = trades.copy()
    t["entry_date"] = pd.to_datetime(t["entry_date"])
    t["period"] = (t["entry_date"].dt.year.astype(str) + "-H"
                   + ((t["entry_date"].dt.month > 6).astype(int) + 1).astype(str))
    g = t.groupby("period")["net_return"]
    lines = ["| Period | Trades | Mean/trade |", "|---|---:|---:|"]
    for period, s in g:
        lines.append(f"| {period} | {len(s)} | {s.mean()*100:+.2f}% |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--holds", default="5,10,20",
                    help="comma-separated hold lengths in sessions (default 5,10,20)")
    ap.add_argument("--entry-offset", type=int, default=1,
                    help="sessions after the effective date to enter (default 1)")
    ap.add_argument("--out", default="analysis/postsplit_entry.md")
    args = ap.parse_args()
    holds = [int(h) for h in args.holds.split(",") if h.strip()]

    prices = pd.read_pickle(ROOT / "DATA" / "prices_full.pkl")
    events = build_events_combined()
    basis = entry_basis(events, prices, entry_offset=1)
    summary = summarize_basis(basis, floor=FLOOR)

    # Buckets by the price a LIVE filter would have seen, not the panel price.
    resolvable = basis["quoted_price"].notna()
    below = basis[resolvable & (basis["quoted_price"] < FLOOR)]
    above = basis[resolvable & (basis["quoted_price"] >= FLOOR)]
    ev_cols = ["ticker", "t_ann", "t_split", "ratio"]

    out: list[str] = []
    A = out.append
    A("# Shorting reverse splits AFTER the split executes\n")
    A(f"_Generated {pd.Timestamp.now().date()} · "
      f"events {events['t_ann'].min().date()} → {events['t_ann'].max().date()} · "
      f"prices {prices.index.min().date()} → {prices.index.max().date()}_\n")
    A("Live parameters: stop-loss 40%, take-profit 20%, no gap-up filter, "
      "1.5% flat slippage.\n")

    # ---- price basis -------------------------------------------------------------
    A("## 0. The price basis problem (found while building this test)\n")
    A(f"- Events: **{summary['events']}**, with price data: {summary['evaluable']}, "
      f"priced at entry: {summary['priced']}\n")
    A(f"- Series showing a split jump (raw): **{summary['with_jump']}**\n")
    A(f"- Series with NO jump (back-adjusted, or the split never executed): "
      f"**{summary['back_adjusted']}**\n")
    A(f"- Median panel entry price: **${summary['panel_median']:.2f}** — "
      f"{summary['panel_above_floor']} of {summary['priced']} are ≥ ${FLOOR:.2f}\n")
    A(f"- Median implied *quoted* price: **${summary['quoted_median']:.2f}** — "
      f"{summary['quoted_above_floor']} of {summary['quoted_resolvable']} are "
      f"≥ ${FLOOR:.2f}\n")
    A("\nCompanies reverse-split because they are under $1 and face delisting. A "
      "median entry price of "
      f"${summary['panel_median']:.2f} is not a population that needs to split; "
      f"${summary['quoted_median']:.2f} is. The panel is back-adjusted, so "
      "`price_floor_walkforward.py` — which reads entry prices straight off it — "
      "calibrated the $1.00 floor against adjusted dollars.\n")
    A("\n> Caveat: a continuous series means either the provider adjusted it or the "
      "split never executed, and prices alone cannot separate those. Buckets below "
      "use the adjusted reading, which the medians above support.\n")

    # ---- baselines ---------------------------------------------------------------
    A("\n## 1. Baseline — the current strategy (enter after the announcement)\n")
    base_above = backtest_mega(above[ev_cols], prices, hold_rule="day_of_split",
                               entry_offset=1, **LIVE_PARAMS)
    base_below = backtest_mega(below[ev_cols], prices, hold_rule="day_of_split",
                               entry_offset=1, **LIVE_PARAMS)
    A(HEADER)
    A(row(describe(base_above, f"Quoted ≥ ${FLOOR:.2f} (live trades these)")))
    A(row(describe(base_below, f"Quoted < ${FLOOR:.2f} (live REJECTS these)")))

    # ---- the hypothesis ----------------------------------------------------------
    A("\n## 2. The hypothesis — enter at the effective date instead\n")
    A(f"Entry: Open of session +{args.entry_offset} on/after the effective date. "
      f"Exit: after N sessions, or on the stop/target.\n")
    results: dict[tuple[str, int], pd.DataFrame] = {}
    A(HEADER)
    for name, sub in (("< $1.00", below), ("≥ $1.00", above), ("all", basis[resolvable])):
        for n in holds:
            tr = backtest_mega(sub[ev_cols], prices, hold_rule=n,
                               entry_offset=args.entry_offset,
                               entry_anchor="t_split", **LIVE_PARAMS)
            results[(name, n)] = tr
            A(row(describe(tr, f"Quoted {name}, hold {n}d")))
    A("\n> Several (bucket, hold) cells are reported. The best-looking cell is "
      "selected by having looked at all of them, so treat it as the ceiling of a "
      "search, not an estimate. The period table below is the honest check.\n")

    # ---- the headline arm --------------------------------------------------------
    best_n = holds[len(holds) // 2]
    focus = results[("< $1.00", best_n)]
    A(f"\n## 3. Consistency over time — quoted < ${FLOOR:.2f}, hold {best_n}d\n")
    A(period_table(focus))

    # ---- borrow ------------------------------------------------------------------
    A("\n## 4. What borrow costs do to it\n")
    A("| Annual borrow | Mean/trade (< $1.00) | Mean/trade (≥ $1.00) |")
    A("|---|---:|---:|")
    hi_arm = results[("≥ $1.00", best_n)]
    for rate in BORROW_RATES:
        lo_v = borrow_adjusted(focus, rate) if not focus.empty else float("nan")
        hi_v = borrow_adjusted(hi_arm, rate) if not hi_arm.empty else float("nan")
        A(f"| {rate*100:.0f}% | {lo_v*100:+.2f}% | {hi_v*100:+.2f}% |")
    A("\n_A reverse split shrinks the float, so borrow on these names tends to rise "
      "right when the position is opened. Rates of 50-200% are routine here._\n")

    text = "\n".join(out) + "\n"
    dest = ROOT / args.out
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    print(text)
    print(f"\nWrote {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
