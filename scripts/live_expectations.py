#!/usr/bin/env python3
"""What Strategy B should realistically return under the ACTUAL live constraints.

The headline walk-forward figure (+1208%) assumes 5% position sizing, zero borrow cost,
a flat 1.5% for all execution costs, and no spread filter. The live system runs 2%
sizing, reads real borrow rates, prices every order as a marketable limit, and refuses
names whose spread is too wide.

This script re-runs the same walk-forward out-of-sample trades under those constraints
so there is an honest number to judge live performance against. It also sweeps the
spread threshold to choose the live default.

Usage: python scripts/live_expectations.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "src"))

from split_strategy.backtest.engine import INF, backtest_mega
from split_strategy.backtest.shortability import classify_shortability, load_exchange_map
from split_strategy.backtest.slippage import estimate_spread
from split_strategy.backtest.walkforward import compounded_oos_curve, walk_forward

INITIAL = 10_000.0
LIVE_TRADE_PCT = 0.02      # config.TRADE_PCT
LIVE_MAX_EXPOSURE = 1.0    # config.MAX_EXPOSURE
SPREAD_THRESHOLDS = [0.02, 0.03, 0.05, 0.08, INF]


def equity_for(pooled, trade_pct, max_exposure):
    if pooled is None or pooled.empty:
        return INITIAL, 0, 0
    curve = compounded_oos_curve(pooled, INITIAL, trade_pct, max_exposure=max_exposure)
    final = curve["portfolio_value"].iloc[-1] if not curve.empty else INITIAL
    return final, curve.attrs.get("n_taken", 0), curve.attrs.get("n_skipped", 0)


def drawdown(pooled, trade_pct, max_exposure):
    curve = compounded_oos_curve(pooled, INITIAL, trade_pct, max_exposure=max_exposure)
    if curve.empty:
        return float("nan")
    running = curve["portfolio_value"].cummax()
    return float(((curve["portfolio_value"] - running) / running).min() * 100)


def spread_for_trades(pooled, prices):
    """Estimated bid-ask spread at entry for each pooled trade."""
    out = []
    for _, t in pooled.iterrows():
        try:
            tp = prices[t["ticker"]].dropna(how="all")
        except Exception:
            out.append(np.nan)
            continue
        out.append(estimate_spread(tp, t["entry_date"]))
    return pd.Series(out, index=pooled.index)


def main():
    events = pd.read_pickle(ROOT / "DATA" / "events_combined_cache.pkl")
    prices = pd.read_pickle(ROOT / "DATA" / "prices_full.pkl")

    print("Running walk-forward (Strategy B selection) ...")
    folds = walk_forward(
        events, prices, test_window_days=60, min_initial_events=50, min_test_events=5,
        selection_metric="t_stat", min_grid_trades=15, entry_offset=1,
        adjust_for_split=True, initial_capital=INITIAL, trade_pct=LIVE_TRADE_PCT,
    )
    frames = [f["oos_trades"] for f in folds if not f["oos_trades"].empty]
    pooled = pd.concat(frames, ignore_index=True).sort_values("entry_date").reset_index(drop=True)

    out = ["# Live Expectations — Strategy B under real constraints\n"]
    out.append(f"_Generated {pd.Timestamp.now().date()} · {len(pooled)} pooled "
               f"out-of-sample trades_\n")
    out.append("The published +1208% figure assumes 5% sizing, no borrow cost, a flat "
               "1.5% for all execution costs, and no spread filter. The live system "
               "uses 2% sizing, real borrow rates, marketable limits, and a spread "
               "veto. **The bottom line here — not the headline backtest — is what "
               "live performance should be measured against.**\n")

    # --- 1. sizing -------------------------------------------------------------
    out.append("## 1. Position sizing: 5% (backtest) vs 2% (live)\n")
    out.append("| Sizing | Trades taken | Skipped (no capital) | Final equity | Max DD |")
    out.append("|---|---:|---:|---:|---:|")
    for label, pct in [("5% (backtested)", 0.05), ("2% (live default)", LIVE_TRADE_PCT)]:
        final, taken, skipped = equity_for(pooled, pct, LIVE_MAX_EXPOSURE)
        dd = drawdown(pooled, pct, LIVE_MAX_EXPOSURE)
        out.append(f"| {label} | {taken} | {skipped} | ${final:,.0f} "
                   f"({(final - INITIAL) / INITIAL * 100:+.0f}%) | {dd:.1f}% |")
    out.append("\nWith no concurrent-position cap, 2% sizing means capital rarely "
               "binds — nearly every signal is taken. The lower return is purely less "
               "leverage per trade, and the drawdown falls correspondingly.\n")

    # --- 2. spread -------------------------------------------------------------
    print("Estimating bid-ask spreads (Corwin-Schultz) ...")
    spreads = spread_for_trades(pooled, prices)
    valid = spreads.dropna()
    out.append("## 2. Estimated bid-ask spread at entry\n")
    if not valid.empty:
        out.append(f"- Median: **{valid.median() * 100:.2f}%** · mean "
                   f"{valid.mean() * 100:.2f}% · 90th pct {valid.quantile(0.9) * 100:.2f}%")
        out.append(f"- Trades where the estimated spread alone exceeds the flat 1.5% "
                   f"assumption: **{(valid > 0.015).sum()} of {len(valid)} "
                   f"({(valid > 0.015).mean() * 100:.0f}%)**")
        out.append(f"- Not estimable (too few bars): {len(spreads) - len(valid)}\n")
    else:
        out.append("- Could not estimate spreads for any trade.\n")

    out.append("### Spread-veto sweep\n")
    out.append("Skipping names wider than the threshold, exactly as the live filter does:\n")
    out.append("| Max spread | Trades kept | Win rate | Mean/trade | Final equity (2%) |")
    out.append("|---|---:|---:|---:|---:|")
    for threshold in SPREAD_THRESHOLDS:
        keep = pooled[(spreads.isna()) | (spreads <= threshold)]
        if keep.empty:
            continue
        final, _, _ = equity_for(keep, LIVE_TRADE_PCT, LIVE_MAX_EXPOSURE)
        r = keep["net_return"]
        label = "none" if threshold == INF else f"{threshold * 100:.0f}%"
        out.append(f"| {label} | {len(keep)} | {(r > 0).mean() * 100:.1f}% | "
                   f"{r.mean() * 100:+.2f}% | ${final:,.0f} "
                   f"({(final - INITIAL) / INITIAL * 100:+.0f}%) |")
    out.append("")

    # --- 3. spread-based costs --------------------------------------------------
    out.append("## 3. Replacing the flat 1.5% with estimated spread costs\n")
    adjusted = pooled.copy()
    extra = (spreads.fillna(0.015) - 0.015).clip(lower=0)
    adjusted["net_return"] = pooled["net_return"] - extra
    final_flat, _, _ = equity_for(pooled, LIVE_TRADE_PCT, LIVE_MAX_EXPOSURE)
    final_spread, _, _ = equity_for(adjusted, LIVE_TRADE_PCT, LIVE_MAX_EXPOSURE)
    out.append(f"- Flat 1.5% assumption: mean {pooled['net_return'].mean() * 100:+.2f}%/trade, "
               f"${final_flat:,.0f}")
    out.append(f"- Spread-estimated: mean {adjusted['net_return'].mean() * 100:+.2f}%/trade, "
               f"${final_spread:,.0f}")
    out.append("")

    # --- 4. shortability + borrow ------------------------------------------------
    out.append("## 4. Shortability and borrow cost\n")
    classified = classify_shortability(pooled, prices, load_exchange_map())
    tradeable = classified[~classified["likely_unshortable"]]
    out.append(f"- Proxy says **{int(classified['likely_unshortable'].sum())} of "
               f"{len(classified)}** trades were likely unshortable. Live Schwab data has "
               f"already contradicted this proxy on sub-$1 names, so treat it as a "
               f"pessimistic bound.")
    if not tradeable.empty:
        final_short, _, _ = equity_for(tradeable.sort_values("entry_date"),
                                       LIVE_TRADE_PCT, LIVE_MAX_EXPOSURE)
        out.append(f"- Shortable-only subset: {len(tradeable)} trades, "
                   f"{(tradeable['net_return'] > 0).mean() * 100:.1f}% win rate, "
                   f"${final_short:,.0f}")
    hold_days = (pd.to_datetime(pooled["exit_date"]) - pd.to_datetime(pooled["entry_date"])).dt.days.clip(lower=1)
    out.append("")
    out.append("| Annual borrow | Mean/trade | Final equity (2%) |")
    out.append("|---|---:|---:|")
    for rate in (0.0, 0.10, 0.30, 0.50, 1.00):
        adj = pooled.copy()
        adj["net_return"] = pooled["net_return"] - rate * hold_days / 365.0
        final, _, _ = equity_for(adj, LIVE_TRADE_PCT, LIVE_MAX_EXPOSURE)
        out.append(f"| {rate * 100:.0f}% | {adj['net_return'].mean() * 100:+.2f}% | "
                   f"${final:,.0f} ({(final - INITIAL) / INITIAL * 100:+.0f}%) |")
    out.append("")

    # --- 5. everything at once ----------------------------------------------------
    out.append("## 5. All constraints combined — the realistic target\n")
    combined = classified[~classified["likely_unshortable"]].copy()
    combined_spreads = spreads.reindex(combined.index)
    combined = combined[(combined_spreads.isna()) | (combined_spreads <= 0.05)]
    if not combined.empty:
        extra_c = (combined_spreads.reindex(combined.index).fillna(0.015) - 0.015).clip(lower=0)
        hold_c = (pd.to_datetime(combined["exit_date"]) - pd.to_datetime(combined["entry_date"])).dt.days.clip(lower=1)
        combined["net_return"] = combined["net_return"] - extra_c - 0.30 * hold_c / 365.0
        combined = combined.sort_values("entry_date")
        final_c, taken_c, skipped_c = equity_for(combined, LIVE_TRADE_PCT, LIVE_MAX_EXPOSURE)
        dd_c = drawdown(combined, LIVE_TRADE_PCT, LIVE_MAX_EXPOSURE)
        r = combined["net_return"]
        out.append("2% sizing · shortable-only · 5% spread veto · spread-based execution "
                   "costs · 30%/yr borrow:\n")
        out.append(f"- **{len(combined)} candidate trades, {taken_c} taken**")
        out.append(f"- Win rate **{(r > 0).mean() * 100:.1f}%**, mean **{r.mean() * 100:+.2f}%**/trade")
        out.append(f"- Equity **${INITIAL:,.0f} -> ${final_c:,.0f} "
                   f"({(final_c - INITIAL) / INITIAL * 100:+.0f}%)**, max drawdown {dd_c:.1f}%")
        out.append("")
        out.append("> This is the number to compare live results against. It is still "
                   "optimistic in ways this project cannot fix without paid data: it "
                   "assumes limit orders fill, and it inherits the survivorship bias of "
                   "yfinance dropping delisted tickers.")
    out.append("")

    report = "\n".join(out)
    (ROOT / "analysis" / "live_expectations.md").write_text(report, encoding="utf-8")
    print("\n" + report)


if __name__ == "__main__":
    main()
