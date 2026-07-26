#!/usr/bin/env python3
"""Extensive grid search + walk-forward validation of the reverse-split short strategy.

For each of two parameter-selection philosophies (return-maximizing vs. stability-
favoring), runs a full 4,620-permutation grid search per fold using ONLY in-sample
(already-announced) events, then evaluates the frozen winning parameters on the
following out-of-sample window. Only pooled out-of-sample results are trustworthy.

Usage: python scripts/walk_forward_analysis.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "src"))

from split_strategy.backtest.engine import backtest_mega, summarize_trades, CHOSEN_STRATEGY
from split_strategy.backtest.walkforward import walk_forward, pooled_oos_trades, compounded_oos_curve
from split_strategy.backtest.shortability import classify_shortability, load_exchange_map

INITIAL_CAPITAL = 10000.0
TRADE_PCT = 0.05
RNG = np.random.default_rng(20260726)


def bootstrap_stats(returns: np.ndarray, n_boot: int = 20000):
    if len(returns) < 2:
        return dict(lo=np.nan, hi=np.nan, p_pos=np.nan)
    means = RNG.choice(returns, size=(n_boot, len(returns)), replace=True).mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return dict(lo=float(lo), hi=float(hi), p_pos=float((means > 0).mean()))


def fmt_pct(x):
    return f"{x:+.2f}%" if pd.notna(x) else "n/a"


def param_str(p):
    tp = "None" if p["take_profit"] == float("inf") else f"{p['take_profit']*100:.0f}%"
    sl = "None" if p["stop_loss"] == float("inf") else f"{p['stop_loss']*100:.0f}%"
    gap = "None" if p["max_gap_up"] == float("inf") else f"{p['max_gap_up']*100:.0f}%"
    rmax = "inf" if p["max_ratio"] == float("inf") else f"{p['max_ratio']:.0f}"
    return f"hold={p['hold_rule']}, stop={sl}, TP={tp}, gap={gap}, ratio=({p['min_ratio']:.0f},{rmax})"


def run_selection(events, prices, exchange_map, selection_metric, label):
    fold_results = walk_forward(
        events, prices, test_window_days=60, min_initial_events=50, min_test_events=5,
        selection_metric=selection_metric, min_grid_trades=15, entry_offset=1, adjust_for_split=True,
        initial_capital=INITIAL_CAPITAL, trade_pct=TRADE_PCT,
    )
    pooled = pooled_oos_trades(fold_results)
    lines = [f"## Selection: {label} (`{selection_metric}`)\n"]

    if pooled.empty:
        lines.append("_No out-of-sample trades produced._\n")
        return lines, fold_results, pooled

    lines.append(f"{len(fold_results)} folds, {len(pooled)} pooled out-of-sample trades.\n")
    lines.append("| Fold | Test window | In-N | Selected params | In-sample metric | OOS trades | OOS win% | OOS return% |")
    lines.append("|---|---|---:|---|---:|---:|---:|---:|")
    for f in fold_results:
        p = f["selected_params"]
        lines.append(
            f"| | {f['test_start'].date()} -> {f['test_end'].date()} | {f['in_sample_n']} | "
            f"{param_str(p)} | {f['in_sample_metric_value']:.2f} | "
            f"{f['oos_summary']['total_trades']} | {fmt_pct(f['oos_summary']['win_rate'])} | "
            f"{fmt_pct(f['oos_summary']['total_return_pct'])} |"
        )
    lines.append("")

    # Pooled stats (the honest number)
    r = pooled["net_return"].to_numpy()
    boot = bootstrap_stats(r)
    tstat = float(r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))) if len(r) > 1 and r.std(ddof=1) > 0 else np.nan
    lines.append(f"**Pooled out-of-sample: {len(pooled)} trades, win rate {(r>0).mean()*100:.1f}%, "
                f"mean return/trade {r.mean()*100:+.2f}%, t-stat {tstat:+.2f}**")
    lines.append(f"- Bootstrap 95% CI on expectancy: [{boot['lo']*100:+.2f}%, {boot['hi']*100:+.2f}%], "
                f"P(edge>0) = {boot['p_pos']*100:.0f}%")

    # Compounded, capital-constrained equity curves. Trades routinely overlap (median
    # concurrent open positions can be double digits), so an unconstrained curve
    # silently assumes unlimited buying power. 100% exposure = fully cash-collateralized
    # (realistic default; most of these microcaps are non-marginable so this is the
    # binding real-world constraint, not a conservative choice).
    lines.append("**Compounded equity under realistic capital constraints** "
                f"(trades routinely overlap - see concurrency note below):\n")
    lines.append("| Max exposure | Trades taken | Trades skipped (no capital) | Final equity | Max drawdown |")
    lines.append("|---|---:|---:|---:|---:|")
    for cap, cap_label in [(1.0, "100% (cash-collateralized)"), (2.0, "200%"), (float("inf"), "Unconstrained (unrealistic)")]:
        curve = compounded_oos_curve(pooled, INITIAL_CAPITAL, TRADE_PCT, max_exposure=cap)
        final = curve["portfolio_value"].iloc[-1] if not curve.empty else INITIAL_CAPITAL
        taken = curve.attrs.get("n_taken", 0)
        skipped = curve.attrs.get("n_skipped", 0)
        if not curve.empty:
            eq = pd.concat([pd.Series([INITIAL_CAPITAL]), curve["portfolio_value"]], ignore_index=True)
            dd = ((eq - eq.cummax()) / eq.cummax()).min() * 100
        else:
            dd = float("nan")
        lines.append(f"| {cap_label} | {taken} | {skipped} | "
                    f"${INITIAL_CAPITAL:,.0f} -> ${final:,.0f} ({(final-INITIAL_CAPITAL)/INITIAL_CAPITAL*100:+.1f}%) | {dd:.1f}% |")
    lines.append("")

    # Parameter stability across folds
    lines.append("**Parameter stability across folds** (how often each choice was selected):")
    for key in ["hold_rule", "stop_loss", "take_profit", "max_gap_up"]:
        vals = [f["selected_params"][key] for f in fold_results]
        vc = pd.Series(vals).value_counts()
        top = ", ".join(f"{k}×{v}" for k, v in vc.items())
        lines.append(f"- {key}: {top}")
    lines.append("")

    # Shortability breakdown on pooled OOS trades
    classified = classify_shortability(pooled, prices, exchange_map)
    n = len(classified)
    n_un = int(classified["likely_unshortable"].sum())
    short_only = classified[~classified["likely_unshortable"]]
    lines.append(f"**Shortability: {n_un} of {n} pooled OOS trades ({n_un/n*100:.0f}%) likely NOT shortable at Schwab.**")
    if not short_only.empty:
        rs = short_only["net_return"].to_numpy()
        curve_s = compounded_oos_curve(short_only.sort_values("entry_date"), INITIAL_CAPITAL, TRADE_PCT, max_exposure=1.0)
        final_s = curve_s["portfolio_value"].iloc[-1] if not curve_s.empty else INITIAL_CAPITAL
        lines.append(f"- Shortable-only, 100% exposure cap: {len(short_only)} candidate trades, "
                    f"{curve_s.attrs.get('n_taken',0)} taken, win rate {(rs>0).mean()*100:.1f}%, "
                    f"mean return/trade {rs.mean()*100:+.2f}%, compounded equity "
                    f"${INITIAL_CAPITAL:,.0f} -> ${final_s:,.0f} ({(final_s-INITIAL_CAPITAL)/INITIAL_CAPITAL*100:+.1f}%)")
    lines.append("")

    # Concurrency (why the exposure cap matters)
    ov = pooled.copy()
    ov["entry_date"] = pd.to_datetime(ov["entry_date"])
    ov["exit_date"] = pd.to_datetime(ov["exit_date"])
    span = pd.date_range(ov["entry_date"].min(), ov["exit_date"].max(), freq="D")
    conc = pd.Series(0, index=span)
    for _, r in ov.iterrows():
        conc.loc[r["entry_date"]:r["exit_date"]] += 1
    lines.append(f"**Concurrency:** up to {int(conc.max())} positions open simultaneously "
                f"(median {conc[conc>0].median():.0f} on days with any open) - this is why an "
                f"exposure cap changes the equity curve so much above.\n")

    # Borrow-cost sensitivity (100% exposure cap - the realistic capital constraint)
    hold_days = (pd.to_datetime(pooled["exit_date"]) - pd.to_datetime(pooled["entry_date"])).dt.days.clip(lower=1)
    lines.append(f"**Borrow-cost sensitivity** (100% exposure cap; median OOS holding period "
                f"{hold_days.median():.0f} days):\n")
    lines.append("| Annual borrow rate | Mean return/trade | Compounded equity (100% cap) |")
    lines.append("|---|---:|---:|")
    for rate in (0.0, 0.10, 0.30, 0.50, 1.00, 2.00):
        fee = rate * hold_days / 365.0
        adj_return = pooled["net_return"] - fee
        adj_pooled = pooled.copy()
        adj_pooled["net_return"] = adj_return
        curve_b = compounded_oos_curve(adj_pooled, INITIAL_CAPITAL, TRADE_PCT, max_exposure=1.0)
        final_b = curve_b["portfolio_value"].iloc[-1] if not curve_b.empty else INITIAL_CAPITAL
        lines.append(f"| {rate*100:.0f}% | {adj_return.mean()*100:+.2f}% | "
                    f"${INITIAL_CAPITAL:,.0f} -> ${final_b:,.0f} ({(final_b-INITIAL_CAPITAL)/INITIAL_CAPITAL*100:+.1f}%) |")
    lines.append("")

    exit_reasons = pooled["exit_reason"].value_counts()
    lines.append("Exit reasons in pooled OOS trades: " +
                ", ".join(f"{k}={v}" for k, v in exit_reasons.items()) + "\n")

    return lines, fold_results, pooled


def static_baseline(events, prices, start_date, end_date):
    """The published CHOSEN_STRATEGY, frozen, applied blind (no re-optimization) over
    the same out-of-sample period — the fair comparison point for 'is optimizing per
    fold even worth it, vs. just trading the original recipe.'"""
    window = events[(events["t_ann"] >= start_date) & (events["t_ann"] < end_date)]
    trades = backtest_mega(window, prices, entry_offset=1, adjust_for_split=True,
                           initial_capital=INITIAL_CAPITAL, trade_pct=TRADE_PCT, **CHOSEN_STRATEGY)
    return summarize_trades(trades, INITIAL_CAPITAL), trades


def main():
    events = pd.read_pickle(ROOT / "DATA" / "events_combined_cache.pkl")
    prices = pd.read_pickle(ROOT / "DATA" / "prices_full.pkl")
    exchange_map = load_exchange_map()

    out = ["# Walk-Forward Validation & Extensive Grid Search\n"]
    out.append(f"_Generated {pd.Timestamp.now().date()} | {len(events)} historical events "
              f"({events['t_ann'].min().date()} to {events['t_ann'].max().date()}) | "
              f"corrected engine (split-jump neutralized) | realistic entry (next-session open)_\n")
    out.append("**Method:** rolling 60-day out-of-sample windows. For each window, a full "
              "4,620-permutation grid search runs on events announced strictly BEFORE that "
              "window (in-sample); the winning parameters are frozen and evaluated on the "
              "window itself (out-of-sample, never seen by the optimizer). Two selection "
              "philosophies are compared: picking the permutation with the best in-sample "
              "**return** (prone to overfitting to a few lucky trades) vs. the best in-sample "
              "**t-statistic** (favors an edge that shows up consistently, penalizes noisy "
              "small samples). Only the pooled out-of-sample numbers below should be trusted "
              "as an estimate of live performance — in-sample numbers are what the optimizer "
              "already knew and are not evidence of anything.\n")

    lines_a, folds_a, pooled_a = run_selection(events, prices, exchange_map, "total_return_pct", "Return-maximizing")
    out += lines_a
    lines_b, folds_b, pooled_b = run_selection(events, prices, exchange_map, "t_stat", "Stability-favoring (t-stat)")
    out += lines_b

    pooled_a.to_pickle(ROOT / "DATA" / "wf_pooled_return_max.pkl")
    pooled_b.to_pickle(ROOT / "DATA" / "wf_pooled_tstat.pkl")

    # Static baseline over the same period as fold A's out-of-sample coverage
    if folds_a:
        start = folds_a[0]["test_start"]
        end = folds_a[-1]["test_end"]
        base_summary, base_trades = static_baseline(events, prices, start, end)
        out.append(f"## Baseline: published strategy, frozen, no re-optimization\n")
        out.append(f"Same period ({start.date()} -> {end.date()}), applying the original "
                  f"published 'Optimal Safe' strategy (day_of_split / 40% stop / no TP / 30% gap "
                  f"filter) with NO per-fold re-optimization — i.e., what happens if you just "
                  f"trade the original recipe blindly:\n")
        out.append(f"- trades={base_summary['total_trades']}, win_rate={fmt_pct(base_summary['win_rate'])}, "
                  f"total_return={fmt_pct(base_summary['total_return_pct'])}, "
                  f"maxDD={fmt_pct(base_summary['max_drawdown_pct'])}\n")

    out.append("## Bottom line\n")
    out.append("See the pooled out-of-sample stats above for both selection methods. If both "
              "the return-maximizing AND the t-stat-selection out-of-sample results show a "
              "win rate meaningfully above 50% with a positive, statistically resolved "
              "expectancy (t-stat comfortably > 2, tight bootstrap CI excluding zero), the edge "
              "is real. If the in-sample numbers look great but pooled out-of-sample collapses "
              "toward breakeven, that confirms the strategy was overfit to its own history.\n")

    report = "\n".join(out)
    (ROOT / "analysis" / "walk_forward_results.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
