#!/usr/bin/env python3
"""Robustness / statistical-significance analysis of the strategy.

Answers "is the backtested edge real, or an artifact?" with four checks:
  1. Statistical power: bootstrap CI on per-trade expectancy (recent window).
  2. Cost sensitivity: what borrow fees do to the result.
  3. Fill realism: stop-loss filling at the *open* after the trigger (gap-through)
     instead of exactly at the stop price.
  4. Multiple-testing: how good the best of 4,620 grid permutations would look by
     pure chance, given the sample size.

Usage: python scripts/analyze_robustness.py [--days 60]
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "src"))

from split_strategy.backtest.engine import backtest_mega, CHOSEN_STRATEGY, neutralize_split
from split_strategy.backtest.events import build_events_from_early_edgar
from split_strategy.backtest.shortability import classify_shortability, load_exchange_map

RNG = np.random.default_rng(12345)


def bootstrap_ci(returns: np.ndarray, n_boot: int = 20000, alpha: float = 0.05):
    """Bootstrap CI for the mean per-trade return."""
    if len(returns) == 0:
        return np.nan, np.nan, np.nan
    means = RNG.choice(returns, size=(n_boot, len(returns)), replace=True).mean(axis=1)
    lo, hi = np.percentile(means, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    p_positive = float((means > 0).mean())
    return lo, hi, p_positive


def borrow_adjusted(trades: pd.DataFrame, annual_rate: float) -> float:
    """Mean per-trade return after subtracting a borrow fee for the holding period."""
    days = (pd.to_datetime(trades["exit_date"]) - pd.to_datetime(trades["entry_date"])).dt.days.clip(lower=1)
    fee = annual_rate * days / 365.0
    return float((trades["net_return"] - fee).mean())


def gap_through_returns(trades: pd.DataFrame, prices: pd.DataFrame) -> pd.Series:
    """Recompute stop-loss trades filling at the triggering day's OPEN, not the stop price.

    A short stopped out on a gap-up fills at the open, which is worse than the stop.
    """
    adj = trades["net_return"].copy()
    for i, t in trades.iterrows():
        if t["exit_reason"] != "stop_loss":
            continue
        tkr = t["ticker"]
        try:
            tp = prices[tkr].dropna(how="all")
        except Exception:
            continue
        # Same split neutralization the engine applies, else the mechanical
        # reverse-split jump masquerades as a catastrophic gap.
        tp = neutralize_split(tp, t["t_split"], t.get("ratio"))
        row = tp[tp.index == pd.Timestamp(t["exit_date"])]
        if row.empty:
            continue
        open_px = float(row["Open"].iloc[0])
        if not np.isfinite(open_px) or open_px <= 0:
            continue
        # If it opened above the stop, that's the real fill.
        fill = max(open_px, float(t["exit_price"]))
        raw = (float(t["entry_price"]) - fill) / float(t["entry_price"])
        adj.at[i] = raw - 0.015
    return adj


def expected_max_winrate(n_trades: int, n_tests: int, true_p: float = 0.5, sims: int = 4000):
    """Expected best win rate across `n_tests` correlated-ish trials of `n_trades`.

    Upper bound (treats tests as independent). Real grid permutations are highly
    correlated, so the true expected max is lower - reported as a bracket.
    """
    sigma = np.sqrt(true_p * (1 - true_p) / n_trades)
    # Expected max of n_tests standard normals (approximation).
    z_max = np.sqrt(2 * np.log(n_tests))
    return true_p + z_max * sigma, sigma


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    args = ap.parse_args()

    as_of = pd.Timestamp.now().normalize()
    events = build_events_from_early_edgar(require_executed=True, as_of=as_of)
    events = events[events["t_split"] >= as_of - pd.Timedelta(days=args.days)].reset_index(drop=True)

    cache = ROOT / "DATA" / "prices_recent.pkl"
    if not cache.exists():
        print("Missing DATA/prices_recent.pkl - run scripts/backtest_recent.py --save-prices first.")
        sys.exit(1)
    prices = pd.read_pickle(cache)

    trades = backtest_mega(events, prices, entry_offset=1, **CHOSEN_STRATEGY)
    trades = classify_shortability(trades, prices, load_exchange_map())
    r = trades["net_return"].to_numpy()

    out = []
    out.append("# Robustness Analysis\n")
    out.append(f"_Generated {as_of.date()} | window = last {args.days} days | n = {len(trades)} trades_\n")

    # --- 1. Statistical power ---
    out.append("## 1. Is the sample big enough to conclude anything?\n")
    mean, sd = r.mean(), r.std(ddof=1)
    tstat = mean / (sd / np.sqrt(len(r))) if len(r) > 1 else np.nan
    lo, hi, p_pos = bootstrap_ci(r)
    out.append(f"- Mean per-trade return: **{mean*100:+.2f}%** (std {sd*100:.1f}%)")
    out.append(f"- t-statistic: **{tstat:+.2f}** (needs |t| > ~2 for significance)")
    out.append(f"- Bootstrap 95% CI on expectancy: **[{lo*100:+.2f}%, {hi*100:+.2f}%]**")
    out.append(f"- P(true expectancy > 0) given this data: **{p_pos*100:.0f}%**")
    # trades needed to resolve
    if sd > 0 and abs(mean) > 0:
        n_needed = int((2 * sd / abs(mean)) ** 2)
        out.append(f"- Trades needed to detect an edge this size at 95% confidence: **~{n_needed:,}**")
    out.append("")

    # --- 2. Borrow costs ---
    out.append("## 2. What do borrow fees do to it?\n")
    hold = (pd.to_datetime(trades["exit_date"]) - pd.to_datetime(trades["entry_date"])).dt.days.clip(lower=1)
    out.append(f"- Median holding period: **{hold.median():.0f} days** (mean {hold.mean():.1f})")
    out.append("")
    out.append("| Borrow rate (annual) | Mean per-trade return |")
    out.append("|---|---:|")
    out.append(f"| 0% (as backtested) | {borrow_adjusted(trades, 0.0)*100:+.2f}% |")
    for rate in (0.10, 0.30, 0.50, 1.00, 2.00):
        out.append(f"| {rate*100:.0f}% | {borrow_adjusted(trades, rate)*100:+.2f}% |")
    out.append("")
    out.append("_Hard-to-borrow micro-caps routinely cost 50-200%+ annualized._\n")

    # --- 3. Gap-through fills ---
    out.append("## 3. What if stops fill on the gap, not at the stop price?\n")
    n_stops = int((trades["exit_reason"] == "stop_loss").sum())
    adj = gap_through_returns(trades, prices)
    out.append(f"- Stop-loss exits: **{n_stops}** of {len(trades)} trades")
    out.append(f"- Mean return, idealized stop fill: **{r.mean()*100:+.2f}%**")
    out.append(f"- Mean return, realistic gap-through fill: **{adj.mean()*100:+.2f}%**")
    out.append(f"- Cost of this assumption: **{(adj.mean()-r.mean())*100:+.2f}%** per trade\n")

    # --- 4. Multiple testing ---
    out.append("## 4. The 4,620-permutation problem\n")
    n_orig = 574
    exp_max, sigma = expected_max_winrate(n_orig, 4620)
    out.append(f"The published strategy was the winner of a **4,620-permutation grid search** over "
               f"{n_orig} trades. If the strategy had **zero real edge** (true win rate 50%):")
    out.append(f"- Standard error of win rate at n={n_orig}: **{sigma*100:.2f}%**")
    out.append(f"- Expected *best* win rate across 4,620 trials by pure chance: **~{exp_max*100:.1f}%**")
    out.append(f"- Published win rate: **60.97%**")
    out.append("")
    out.append("The permutations are highly correlated (many share most parameters), so the true "
               "chance-adjusted threshold sits below this independent-tests upper bound - but 60.97% "
               "is not comfortably above it. **The selected parameters are plausibly overfit.**\n")

    report = "\n".join(out)
    (ROOT / "analysis" / "robustness_analysis.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
