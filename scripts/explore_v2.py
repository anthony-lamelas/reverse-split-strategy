#!/usr/bin/env python3
"""Exploratory analysis of reverse-split shorts on the development window.

    python scripts/explore_v2.py

EXPLORATION, NOT EVIDENCE. Everything printed here was looked at freely, on the same
data, in many cuts - so the best-looking row is partly luck. Its job is to suggest
what to write down and test on the held-out year (2025-10-01 on), which this script
never touches.

Three parts:
  1. When does the price move? Day-by-day short returns around the announcement and
     around the stated effective date.
  2. Candidate strategies: different entry and exit points, with and without a price
     floor, each shown for two halves of the window so a one-period fluke is visible.
  3. What kind of event works? The base strategy split by price, ratio, time to
     split, liquidity and so on.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "src"))

from split_strategy import config  # noqa: E402
from split_strategy import fees as fee_model  # noqa: E402
from split_strategy.analysis.stats import t_stat  # noqa: E402
from split_strategy.backtest import sim  # noqa: E402
from split_strategy.backtest.events import executable_events  # noqa: E402
from split_strategy.backtest.prices_v2 import PolygonPrices  # noqa: E402

DEV_START, HOLDOUT_START = pd.Timestamp("2021-10-12"), pd.Timestamp("2025-10-01")
HALF = pd.Timestamp("2024-01-01")
COSTS = sim.Costs()


def load():
    events = executable_events(json.loads((config.DATA_DIR / "events_v2.json").read_text()))
    events = events[(events["entry_live"] >= DEV_START) & (events["entry_live"] < HOLDOUT_START)]
    src = PolygonPrices()
    lo = events["entry_first_open"].min() - pd.Timedelta(days=45)
    hi = events["t_split"].max() + pd.Timedelta(days=30)
    rows = []
    for _, ev in events.iterrows():
        bars, splits = src.daily(ev["ticker"], lo, hi), src.splits(ev["ticker"])
        a = ev["entry_first_open"]
        if bars.empty or a not in bars.index:
            continue
        w = sim.entry_basis(bars, splits, a)
        w["Volume"] = bars["Volume"].reindex(w.index)
        if sim.has_unrecorded_split(w, ev["t_split"], ev["ratio"]) or len(w) < 2:
            continue
        pre = bars[bars.index < a].tail(20)
        actual = None
        if splits is not None and not splits.empty:
            when = pd.to_datetime(splits["execution_date"])
            near = when[(when >= ev["t_split"] - pd.Timedelta(days=1))
                        & (when <= ev["t_split"] + pd.Timedelta(days=15))]
            actual = near.min() if len(near) else None
        rows.append(dict(
            ev=ev, w=w, px=float(w.iloc[0]["Open"]),
            dollar_vol=float((pre["Close"] * pre["Volume"]).median()) if len(pre) else np.nan,
            runup=float(pre["Close"].iloc[-1] / pre["Close"].iloc[-6] - 1) if len(pre) >= 6 else np.nan,
            gap=float(w.iloc[0]["Open"] / pre["Close"].iloc[-1] - 1) if len(pre) else np.nan,
            actual=actual))
    print(f"Development window {DEV_START.date()} .. {(HOLDOUT_START - pd.Timedelta(days=1)).date()}: "
          f"{len(events)} executable events, {len(rows)} usable (priced, no unexplained jump)")
    return rows


def line(label, r, extra=""):
    r = np.asarray([x for x in r if x == x], dtype=float)
    if len(r) < 5:
        return f"  {label:46s} n={len(r):4d}  (too few)"
    return (f"  {label:46s} n={len(r):4d}  mean {r.mean():+6.2%}  median {np.median(r):+6.2%}  "
            f"t {t_stat(r):+5.2f}  win {(r > 0).mean():3.0%}{extra}")


def net(entry_px, exit_px, days, px0, market_exit=True):
    half = COSTS.spread(px0) / 2
    fee = fee_model.entry_fees_pct(COSTS.notional / entry_px, entry_px) or 0.0
    return ((entry_px * (1 - half) - exit_px * (1 + (half if market_exit else 0))) / entry_px
            - COSTS.borrow(px0) * max(days, 1) / 365 - fee)


def idx_at_or_after(w, day):
    i = w.index.searchsorted(pd.Timestamp(day))
    return i if i < len(w) else None


def part1(rows):
    print("\n=== 1. WHEN DOES THE PRICE MOVE? (short's return per leg, before costs)")
    print(" Sessions counted from the stated effective date E (0 = first session on/after E).")
    print(" Only sessions on or after the announcement are included.")
    for k in range(-5, 4):
        on, intra = [], []
        for r in rows:
            w, e = r["w"], idx_at_or_after(r["w"], r["ev"]["t_split"])
            if e is None or not 0 <= e + k < len(w):
                continue
            i = e + k
            if i >= 1:
                on.append(1 - w.iloc[i]["Open"] / w.iloc[i - 1]["Close"])
            intra.append(1 - w.iloc[i]["Close"] / w.iloc[i]["Open"])
        print(line(f"E{k:+d}  overnight into it (prev close -> open)", on))
        print(line(f"E{k:+d}  during it (open -> close)", intra))
    print(" Sessions counted from the announcement (0 = first open after the filing).")
    print(line("announcement gap (prior close -> first open)", [-r["gap"] for r in rows]))
    for k in range(0, 4):
        intra = [1 - r["w"].iloc[k]["Close"] / r["w"].iloc[k]["Open"]
                 for r in rows if k < len(r["w"]) and r["w"].index[k] < r["ev"]["t_split"]]
        print(line(f"A+{k}  during it (open -> close), still pre-split", intra))


def simulate(r, entry, exit_, target=None, stop=None):
    """Generic trade. entry/exit are (anchor, offset, field); anchors: 'A' announcement
    bar, 'E' first bar on/after the stated date, 'X' the bar the split actually executed
    on (else 10 sessions after E). Returns (gross, net, days) or None."""
    w, ev = r["w"], r["ev"]
    e = idx_at_or_after(w, ev["t_split"])
    if e is None:
        return None
    x = None
    if r["actual"] is not None:
        x = idx_at_or_after(w, r["actual"])
    if x is None:
        x = min(e + 10, len(w) - 1)
    base = {"A": 0, "E": e, "X": x}
    i, j = base[entry[0]] + entry[1], base[exit_[0]] + exit_[1]
    if i < 0 or j >= len(w) or j < i or (j == i and not (entry[2] == "Open" and exit_[2] == "Close")):
        return None
    p_in = float(w.iloc[i][entry[2]])
    if not p_in > 0:
        return None
    p_out, k_out, market = float(w.iloc[j][exit_[2]]), j, True
    if target or stop:
        for k in range(i, j + (1 if exit_[2] == "Close" else 0)):
            b = w.iloc[k]
            if k > i and stop and b["Open"] >= p_in * (1 + stop):
                p_out, k_out = float(b["Open"]), k
                break
            if k > i and target and b["Open"] <= p_in * (1 - target):
                p_out, k_out = float(b["Open"]), k
                break
            if stop and b["High"] >= p_in * (1 + stop):
                p_out, k_out = p_in * (1 + stop), k
                break
            if target and b["Low"] <= p_in * (1 - target):
                p_out, k_out, market = p_in * (1 - target), k, False
                break
    days = (w.index[k_out] - w.index[i]).days
    return (p_in - p_out) / p_in, net(p_in, p_out, days, p_in, market), days, p_in, w.index[i]


STRATEGIES = [
    ("announce open -> E open (cover on split day)", ("A", 0, "Open"), ("E", 0, "Open"), {}),
    ("  same, take-profit 20%", ("A", 0, "Open"), ("E", 0, "Open"), {"target": 0.20}),
    ("  same, take-profit 20% + stop 40%", ("A", 0, "Open"), ("E", 0, "Open"), {"target": 0.20, "stop": 0.40}),
    ("announce open -> actual split open (wait <=10d)", ("A", 0, "Open"), ("X", 0, "Open"), {}),
    ("announce open -> E-1 close", ("A", 0, "Open"), ("E", -1, "Close"), {}),
    ("announce open -> E-1 open (deployed timing)", ("A", 0, "Open"), ("E", -1, "Open"), {}),
    ("announce open -> E close (through split day)", ("A", 0, "Open"), ("E", 0, "Close"), {}),
    ("announce open -> same-day close (reaction day)", ("A", 0, "Open"), ("A", 0, "Close"), {}),
    ("announce +1 open -> E open (a session late)", ("A", 1, "Open"), ("E", 0, "Open"), {}),
    ("E-3 open -> E open", ("E", -3, "Open"), ("E", 0, "Open"), {}),
    ("E-2 open -> E open", ("E", -2, "Open"), ("E", 0, "Open"), {}),
    ("E-1 open -> E open", ("E", -1, "Open"), ("E", 0, "Open"), {}),
    ("E-1 close -> E open (overnight only)", ("E", -1, "Close"), ("E", 0, "Open"), {}),
    ("E open -> E+5 open (after the split)", ("E", 0, "Open"), ("E", 5, "Open"), {}),
]


def part2(rows):
    print("\n=== 2. CANDIDATE STRATEGIES (after assumed costs; halves split at 2024-01-01)")
    for floor in (None, 0.5, 1.0):
        print(f"\n -- price floor: {'none' if floor is None else f'${floor:.2f}'}")
        for label, entry, exit_, kw in STRATEGIES:
            out = [simulate(r, entry, exit_, **kw) for r in rows]
            out = [o for o in out if o and (floor is None or o[3] >= floor)]
            nets = [o[1] for o in out]
            h1 = [o[1] for o in out if o[4] < HALF]
            h2 = [o[1] for o in out if o[4] >= HALF]
            gross = np.mean([o[0] for o in out]) if out else float("nan")
            days = np.mean([o[2] for o in out]) if out else float("nan")
            extra = (f"  | gross {gross:+.1%} hold {days:.1f}d | 1st half {np.mean(h1):+.1%} (n={len(h1)}) "
                     f"2nd half {np.mean(h2):+.1%} (n={len(h2)})") if len(h1) >= 5 and len(h2) >= 5 else ""
            print(line(label, nets, extra))


def part3(rows):
    print("\n=== 3. WHAT KIND OF EVENT WORKS? (announce open -> E open, take-profit 20%, after costs)")
    recs = []
    for r in rows:
        o = simulate(r, ("A", 0, "Open"), ("E", 0, "Open"), target=0.20)
        if not o:
            continue
        ev = r["ev"]
        recs.append(dict(
            net=o[1], gross=o[0], px=r["px"], ratio=ev["ratio"], form=str(ev["form"])[:3],
            days_to_split=(ev["t_split"] - ev["entry_first_open"]).days,
            premarket=ev["t_ann"].hour * 60 + ev["t_ann"].minute < 570,
            dollar_vol=r["dollar_vol"], runup=r["runup"], gap=r["gap"],
            year=o[4].year, executed_on_time=r["actual"] is not None))
    d = pd.DataFrame(recs)

    def cut(title, col, bins=None, labels=None):
        print(f" {title}")
        key = pd.cut(d[col], bins=bins, labels=labels) if bins else d[col]
        for name, g in d.groupby(key, observed=True):
            print(line(f"   {name}", g["net"].to_numpy(), f"  | gross {g['gross'].mean():+.1%}"))

    cut("By entry price", "px", [0, 0.25, 0.5, 1, 2, 5, 1e9],
        ["< $0.25", "$0.25-0.50", "$0.50-1", "$1-2", "$2-5", ">= $5"])
    cut("By split ratio", "ratio", [1, 5, 10, 20, 50, 1e9], ["2-5", "6-10", "11-20", "21-50", "> 50"])
    cut("By calendar days from entry to split", "days_to_split", [0, 2, 5, 10, 30, 1e9],
        ["1-2", "3-5", "6-10", "11-30", "> 30"])
    cut("By typical daily dollar volume before the news", "dollar_vol", [0, 1e5, 5e5, 2e6, 1e7, 1e15],
        ["< $100k", "$100k-500k", "$500k-2M", "$2M-10M", "> $10M"])
    cut("By move on the announcement (prior close -> first open)", "gap", [-10, -0.10, -0.02, 0.02, 0.10, 100],
        ["down > 10%", "down 2-10%", "flat", "up 2-10%", "up > 10%"])
    cut("By 5-day move before the news", "runup", [-10, -0.15, 0, 0.15, 100],
        ["down > 15%", "down 0-15%", "up 0-15%", "up > 15%"])
    cut("By filing type", "form")
    cut("By when the filing arrived", "premarket")
    cut("By whether the split happened on/near the stated date", "executed_on_time")
    cut("By year", "year")


if __name__ == "__main__":
    data = load()
    part1(data)
    part2(data)
    part3(data)
