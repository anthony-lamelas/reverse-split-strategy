"""Backtest v2: simulate the trade the live system places, on prices that were quoted.

What is different from `engine.backtest_mega`, and why each difference exists:

- **Unadjusted prices.** The entry price is the dollar price on the screen that day,
  so the price floor, the margin rule and the per-share fee mean what they mean live.
- **Executable events only.** Entry and exit dates come from `events.executable_events`:
  the exit date was published in the filing that triggered the entry.
- **Costs are explicit inputs** (`Costs`), not a flat 1.5%: half the quoted spread on
  each market fill, borrow for the days held, regulatory fees on the entry.
- **One position per ticker**, as live enforces.
- **No compounding inside the trade table.** `run_rule` returns independent per-trade
  returns; `portfolio` turns them into account growth under the real margin rule, with
  overlapping positions competing for it.

Conventions: prices are unadjusted; a split inside the holding window is undone with
the provider's split table so every price is in entry-day dollars. When a bar could
have hit both the stop and the target, the stop is assumed - the conservative reading
of a daily bar.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

import numpy as np
import pandas as pd

from .. import fees as fee_model
from .. import margin as mgn
from ..live import calendar as mcal


@dataclass(frozen=True)
class Rule:
    #: "first_open": the first session open after EDGAR accepted the filing.
    #: "live": the session after the filing DATE - what the live system does today.
    entry: str = "live"
    #: Cover this many sessions before the effective date (0 = on it, post-split).
    exit_sessions_before: int = 1
    stop: Optional[float] = 0.40      # fraction above entry; None = no stop
    target: Optional[float] = 0.20    # fraction below entry; None = no take-profit
    min_price: Optional[float] = 1.00  # entry-price floor; None = no floor

    def label(self) -> str:
        return (f"entry={self.entry} exit=T-{self.exit_sessions_before} "
                f"stop={self.stop} target={self.target} floor={self.min_price}")


@dataclass(frozen=True)
class Costs:
    """Trading costs as functions of the entry price.

    `spread` is the full quoted spread as a fraction of price; a market fill pays half
    of it. `borrow` is the annual borrow rate as a fraction. The defaults are
    ASSUMPTIONS, not measurements - the shadow book (live/shadow.py) exists to replace
    them - so every result should be shown at `scaled(0)`, `scaled(1)` and `scaled(2)`.
    """
    spread: Callable[[float], float] = lambda px: (
        0.06 if px < 0.5 else 0.04 if px < 1 else 0.025 if px < 5 else 0.01)
    borrow: Callable[[float], float] = lambda px: 0.50 if px < 1 else 0.30
    notional: float = 50.0  # sizes the per-share regulatory fee

    def scaled(self, k: float) -> "Costs":
        s, b = self.spread, self.borrow
        return Costs(spread=lambda px: k * s(px), borrow=lambda px: k * b(px),
                     notional=self.notional)


def cover_day(t_split, sessions_before: int) -> pd.Timestamp:
    day = pd.Timestamp(t_split).normalize()
    for _ in range(max(int(sessions_before), 0)):
        day = mcal.prev_trading_day(day)
    return day


def entry_basis(bars: pd.DataFrame, splits: Optional[pd.DataFrame], entry_day) -> pd.DataFrame:
    """`bars` from `entry_day` on, restated in entry-day dollars.

    Every bar on or after a split's execution date is divided by that split's price
    factor, cumulatively. Before any split the frame is unchanged.
    """
    out = bars.loc[bars.index >= entry_day, ["Open", "High", "Low", "Close"]].astype(float).copy()
    if splits is None or splits.empty:
        return out
    for _, s in splits.iterrows():
        when = pd.Timestamp(s["execution_date"])
        if when > entry_day and s["factor"] and s["factor"] != 1:
            out.loc[out.index >= when] = out.loc[out.index >= when] / float(s["factor"])
    return out


def simulate_trade(bars: pd.DataFrame, splits: Optional[pd.DataFrame], entry_day, t_split,
                   rule: Rule, costs: Costs) -> tuple[Optional[dict], Optional[str]]:
    """One short. Returns `(trade, None)` or `(None, reason_skipped)`."""
    entry_day, t_split = pd.Timestamp(entry_day).normalize(), pd.Timestamp(t_split).normalize()
    cover = cover_day(t_split, rule.exit_sessions_before)
    if cover <= entry_day:
        return None, "no_holding_period"
    if bars is None or bars.empty or entry_day not in bars.index:
        return None, "no_entry_bar"
    entry_px = float(bars.loc[entry_day, "Open"])
    if not entry_px > 0:
        return None, "no_entry_bar"
    if rule.min_price and entry_px < rule.min_price:
        return None, "below_floor"
    if rule.exit_sessions_before == 0:
        # Covering on the effective date means reading a post-split price. Without
        # the provider's split on that date there is no factor to undo, and an
        # un-undone 1-for-20 looks like a 1,900% loss.
        on_day = None if splits is None or splits.empty else splits[
            pd.to_datetime(splits["execution_date"]) == t_split]
        if on_day is None or on_day.empty:
            return None, "split_not_in_table"

    window = entry_basis(bars, splits, entry_day)
    stop_px = entry_px * (1 + rule.stop) if rule.stop else None
    target_px = entry_px * (1 - rule.target) if rule.target else None

    exit_px = exit_day = reason = None
    for day, bar in window[window.index < cover].iterrows():
        o, h, l = bar["Open"], bar["High"], bar["Low"]
        if day > entry_day:  # a gap through either level fills at the open
            if stop_px and o >= stop_px:
                exit_px, exit_day, reason = o, day, "stop"
                break
            if target_px and o <= target_px:
                exit_px, exit_day, reason = o, day, "target"
                break
        if stop_px and h >= stop_px:
            exit_px, exit_day, reason = stop_px, day, "stop"
            break
        if target_px and l <= target_px:
            exit_px, exit_day, reason = target_px, day, "target"
            break
    if reason is None:
        at_cover = window[window.index >= cover]
        if at_cover.empty:
            # Halted or delisted before the cover: the short cannot be closed at a
            # known price. Counted, never silently dropped or assumed profitable.
            return None, "no_exit_bar"
        exit_day, exit_px, reason = at_cover.index[0], float(at_cover.iloc[0]["Open"]), "time"

    half = costs.spread(entry_px) / 2.0
    entry_fill = entry_px * (1 - half)               # sell at the bid
    # A resting limit (the target) fills at its price; a market cover pays the ask.
    exit_fill = exit_px if reason == "target" and exit_px == target_px else exit_px * (1 + half)
    days = max((exit_day - entry_day).days, 1)
    borrow = costs.borrow(entry_px) * days / 365.0
    fee = fee_model.entry_fees_pct(costs.notional / entry_px, entry_px) or 0.0
    return {
        "entry_date": entry_day, "exit_date": exit_day, "entry_px": entry_px,
        "exit_px": float(exit_px), "exit_reason": reason, "days": days,
        "gross_return": (entry_px - float(exit_px)) / entry_px,
        "net_return": (entry_fill - exit_fill) / entry_px - borrow - fee,
    }, None


def run_rule(events: pd.DataFrame, bars_for: Callable[[str], pd.DataFrame],
             splits_for: Callable[[str], Optional[pd.DataFrame]], rule: Rule,
             costs: Costs) -> tuple[pd.DataFrame, Counter]:
    """Simulate every event under `rule`. Returns `(trades, skipped_reasons)`.

    `events` needs `ticker`, `t_split` and the entry column the rule names
    (`entry_first_open` / `entry_live`). One position per ticker: an event that
    arrives while that ticker's previous trade is still open is skipped.
    """
    entry_col = {"first_open": "entry_first_open", "live": "entry_live"}[rule.entry]
    skipped: Counter = Counter()
    trades, busy_until = [], {}
    for _, ev in events.sort_values(entry_col).iterrows():
        ticker, entry_day = ev["ticker"], pd.Timestamp(ev[entry_col])
        if ticker in busy_until and entry_day <= busy_until[ticker]:
            skipped["already_short"] += 1
            continue
        trade, why = simulate_trade(bars_for(ticker), splits_for(ticker), entry_day,
                                    ev["t_split"], rule, costs)
        if trade is None:
            skipped[why] += 1
            continue
        busy_until[ticker] = trade["exit_date"]
        trades.append({"ticker": ticker, **trade})
    return pd.DataFrame(trades), skipped


def placebo_events(events: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """The same dates on the wrong tickers.

    Every event keeps its entry and split dates but is handed another event's ticker.
    The result shorts the same kind of stock at the same times with no reverse split
    behind it, which is the fair benchmark for "is this the event, or just shorting
    microcaps". A rule's mean is only evidence to the extent it beats this.
    """
    shuffled = events.copy()
    shuffled["ticker"] = rng.permutation(events["ticker"].to_numpy())
    return shuffled


def portfolio(trades: pd.DataFrame, equity: float = 5000.0, trade_pct: float = 0.02,
              max_notional: Optional[float] = 50.0, margin_pct: float = 0.5,
              max_new_per_day: Optional[int] = 1) -> dict:
    """Account growth from `trades` under the FINRA short-margin rule.

    Trades are taken in entry order; one is skipped when its margin requirement does
    not fit in `margin_pct` of current equity alongside the positions still open, or
    when the day's new-short limit is reached. P&L is booked at the exit.
    """
    if trades.empty:
        return {"final_equity": equity, "taken": 0, "skipped_margin": 0,
                "skipped_daily_cap": 0, "return": 0.0, "annualized": 0.0}
    start_equity = equity
    open_pos: list[dict] = []
    taken = skipped_margin = skipped_cap = 0
    per_day: Counter = Counter()
    for _, t in trades.sort_values("entry_date").iterrows():
        for p in [p for p in open_pos if p["exit_date"] <= t["entry_date"]]:
            equity += p["pnl"]
            open_pos.remove(p)
        if max_new_per_day and per_day[t["entry_date"]] >= max_new_per_day:
            skipped_cap += 1
            continue
        notional = equity * trade_pct
        if max_notional:
            notional = min(notional, max_notional)
        shares = int(notional // t["entry_px"])
        if shares <= 0:
            skipped_margin += 1
            continue
        need = mgn.short_maintenance_requirement(t["entry_px"], shares)
        if sum(p["margin"] for p in open_pos) + need > equity * margin_pct:
            skipped_margin += 1
            continue
        per_day[t["entry_date"]] += 1
        taken += 1
        open_pos.append({"exit_date": t["exit_date"], "margin": need,
                         "pnl": shares * t["entry_px"] * t["net_return"]})
    equity += sum(p["pnl"] for p in open_pos)
    years = max((trades["exit_date"].max() - trades["entry_date"].min()).days / 365.25, 1 / 365.25)
    total = equity / start_equity - 1
    return {"final_equity": equity, "taken": taken, "skipped_margin": skipped_margin,
            "skipped_daily_cap": skipped_cap, "return": total,
            "annualized": (1 + total) ** (1 / years) - 1 if total > -1 else -1.0}
