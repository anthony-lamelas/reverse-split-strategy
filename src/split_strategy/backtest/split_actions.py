"""Recover the price actually quoted on each bar, from the provider's split table.

Why this exists
---------------
`prices.py` fetches with `auto_adjust=False` and its docstring calls the result
"raw/unadjusted". That is wrong, and the error propagated into every price-based
filter in the project. Yahoo's chart endpoint returns prices that are ALWAYS
split-adjusted; `auto_adjust` only controls the *dividend* adjustment (see
`yfinance/utils.py::auto_adjust`, which just rescales OHLC by `Adj Close / Close`).

The control is easy to run and unambiguous: NVDA split 10-for-1 on 2024-06-10 and
traded near $1,150 the week before. With `auto_adjust=False`, yfinance returns
$115.00 for 2024-06-03. The series is split-adjusted.

So for a 1-for-N reverse split the panel MULTIPLIES every pre-split bar by N, and

    quoted(d) = panel(d) * prod( f_i : split_date_i > d )

where `f_i` is the provider's own split factor (new/old shares; 0.1 for a 1-for-10
reverse split). Note the product: a company that reverse-splits three times has its
early bars scaled by the product of all three, which is why the panel contains entry
prices up to $791,000. Dividing by the *event's* ratio alone - what
`price_basis._quoted` does - only removes one factor of several.

Why not just use the event's declared ratio
-------------------------------------------
Two reasons, both measured on the current data set:

1. 265 of 658 tickers reverse-split more than once inside the 2024-2026 window, so
   one ratio is the wrong correction for 45% of events.
2. The declared ratio cannot tell you whether the provider applied an adjustment at
   all. The split table can: 908 of the 913 splits dated before the panel's final
   month were absorbed, and 11 of the 16 that were not are in the last 30 days, i.e.
   provider ingestion lag rather than anything about the company.

That second point also resolves an ambiguity this project had recorded as
unresolvable - "a continuous series means either the provider adjusted it or the
split never executed, and prices alone cannot separate those". True of prices alone.
The provider publishes the split it applied, so the question is answerable: fetch the
actions.

Nothing here touches the live trading path. This is research tooling.
"""
from __future__ import annotations

from typing import Iterable, Mapping, Optional

import numpy as np
import pandas as pd

#: Where `scripts/price_basis_audit.py` caches the fetched action table.
DEFAULT_ACTIONS_PATH = "DATA/yahoo_split_actions.pkl"
#: Which of the requested tickers the provider still serves, recorded at fetch time.
DEFAULT_COVERAGE_PATH = "DATA/yahoo_ticker_coverage.csv"

#: A split is treated as absent from the panel when the series jumps by at least this
#: fraction of the mechanically expected jump. Real trading moves alongside a split,
#: so the band is deliberately wide; the classification only has to separate "jumped
#: by roughly N" from "did not jump at all".
_JUMP_TOLERANCE = 0.5
#: Below this, `1/factor` is too close to 1 for a jump to be distinguishable from an
#: ordinary day's move, so the split is assumed applied rather than guessed at.
_MIN_DETECTABLE_JUMP = 1.5


def fetch_split_actions(tickers: Iterable[str], start="2024-01-01", end=None,
                        chunk_size: int = 40) -> tuple[pd.DataFrame, list[str]]:
    """Download the provider's split-action table for `tickers`.

    Returns ``(actions, served)``:

      actions  columns ``ticker``, ``split_date`` (tz-naive) and ``factor``
               (new/old shares: 0.1 for a 1-for-10 reverse split)
      served   the tickers that returned any price history at all

    `served` is the survivorship measurement and must be collected here rather than
    inferred from `actions`: a ticker that is alive but never split has no action
    rows, and reading its absence as a delisting would invent a hole.
    """
    import yfinance as yf

    tickers = sorted({str(t).upper() for t in tickers if t and str(t) != "nan"})
    end = pd.Timestamp.now().normalize() + pd.Timedelta(days=1) if end is None else end
    rows: list[dict] = []
    served: set[str] = set()
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i + chunk_size]
        raw = yf.download(chunk, start=str(pd.Timestamp(start).date()),
                          end=str(pd.Timestamp(end).date()), auto_adjust=False,
                          actions=True, group_by="ticker", progress=False, threads=True)
        if raw is None or raw.empty:
            continue
        try:
            level0 = set(raw.columns.get_level_values(0))
        except AttributeError:
            continue
        for t in chunk:
            if t not in level0:
                continue
            sub = raw[t].dropna(how="all")
            if sub.empty:
                continue
            served.add(t)
            if "Stock Splits" not in sub.columns:
                continue
            s = sub["Stock Splits"].dropna()
            for d, v in s[s != 0].items():
                d = pd.Timestamp(d)
                rows.append(dict(ticker=t,
                                 split_date=d.tz_localize(None) if d.tzinfo else d,
                                 factor=float(v)))
    return (pd.DataFrame(rows, columns=["ticker", "split_date", "factor"]),
            sorted(served))


def classify_applied(prices: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame:
    """Decide, per split, whether the panel already absorbed it.

    Returns `actions` plus a ``verdict`` column:

        applied        the panel is continuous across the split date
        not_applied    the panel jumps by roughly 1/factor there
        outside_panel  the panel has no bars on one side of the date

    Never assume "applied" for a split we could not inspect: that is how the earlier
    audit turned 43 raw series into 151 and flattered the claim it was testing.
    """
    if actions is None or actions.empty:
        return pd.DataFrame(columns=list(getattr(actions, "columns", [])) + ["verdict", "jump"])
    try:
        available = set(prices.columns.levels[0])
    except AttributeError:
        available = set(prices.columns.get_level_values(0))

    verdicts, jumps = [], []
    cache: dict[str, pd.DataFrame] = {}
    for _, r in actions.iterrows():
        t = str(r["ticker"]).upper()
        factor = float(r["factor"]) if pd.notna(r["factor"]) else 0.0
        if t not in available or factor <= 0:
            verdicts.append("outside_panel"); jumps.append(np.nan); continue
        td = cache.get(t)
        if td is None:
            td = prices[t].dropna(how="all")
            cache[t] = td
        d = pd.Timestamp(r["split_date"])
        before = td[td.index < d]["Close"].dropna()
        after = td[td.index >= d]["Open"].dropna()
        if before.empty or after.empty or float(before.iloc[-1]) <= 0:
            verdicts.append("outside_panel"); jumps.append(np.nan); continue
        jump = float(after.iloc[0]) / float(before.iloc[-1])
        expected = 1.0 / factor  # 10.0 for a 1-for-10 reverse split
        if expected > _MIN_DETECTABLE_JUMP and jump >= expected * _JUMP_TOLERANCE:
            verdicts.append("not_applied")
        else:
            verdicts.append("applied")
        jumps.append(jump)

    out = actions.copy()
    out["verdict"] = verdicts
    out["jump"] = jumps
    return out


def quoted_factors(prices: pd.DataFrame, classified: pd.DataFrame) -> dict:
    """Per ticker, the multiplier that turns a panel bar back into a quoted price.

    ``quoted(d) = panel(d) * factors[ticker][d]``. Only splits the panel actually
    absorbed are undone; a split the panel never applied is already in real dollars.
    """
    idx = prices.index
    applied = classified[classified["verdict"] == "applied"] if len(classified) else classified
    out: dict[str, pd.Series] = {}
    for t, g in applied.groupby("ticker"):
        f = pd.Series(1.0, index=idx)
        for _, r in g.iterrows():
            f.loc[idx < pd.Timestamp(r["split_date"])] *= float(r["factor"])
        out[str(t).upper()] = f
    return out


def quoted_entry_prices(events: pd.DataFrame, prices: pd.DataFrame,
                        factors: Mapping[str, pd.Series],
                        entry_offset: int = 1,
                        classified: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Entry price on both bases, on the bar the engine actually trades.

    `entry_offset=1` selects the second session on or after `t_ann`, matching
    `engine.backtest_mega`. That matters: `scripts/price_floor_walkforward.py`
    picks the first session strictly after `t_ann` instead, which is a different
    bar for 21 of 901 priced events.

    Columns: ``panel_price``, ``quoted_price``, ``split_multiplier``,
    ``later_splits`` (splits absorbed after the entry bar - the number that makes a
    panel price look large, and which is not knowable at entry).
    """
    try:
        available = set(prices.columns.levels[0])
    except AttributeError:
        available = set(prices.columns.get_level_values(0))

    by_ticker: dict[str, pd.Series] = {}
    if classified is not None and len(classified):
        ap = classified[classified["verdict"] == "applied"]
        for t, g in ap.groupby("ticker"):
            by_ticker[str(t).upper()] = pd.to_datetime(g["split_date"]).sort_values()

    rows = []
    cache: dict[str, pd.DataFrame] = {}
    for _, ev in events.iterrows():
        t = str(ev["ticker"]).upper()
        rec = dict(panel_price=np.nan, quoted_price=np.nan,
                   split_multiplier=np.nan, later_splits=0)
        if t in available and pd.notna(ev["t_ann"]):
            td = cache.get(t)
            if td is None:
                td = prices[t].dropna(how="all")
                cache[t] = td
            after = td[td.index >= ev["t_ann"]]
            if len(after) > entry_offset:
                d = after.index[entry_offset]
                v = after.iloc[entry_offset]["Open"]
                if pd.notna(v) and v > 0:
                    f = factors.get(t)
                    mult = float(f.loc[d]) if f is not None and d in f.index else 1.0
                    rec["panel_price"] = float(v)
                    rec["split_multiplier"] = mult
                    rec["quoted_price"] = float(v) * mult
                    dates = by_ticker.get(t)
                    if dates is not None:
                        rec["later_splits"] = int((dates > d).sum())
        rows.append(rec)
    out = pd.DataFrame(rows, index=events.index)
    return pd.concat([events, out], axis=1)


def count_later_splits(classified: pd.DataFrame, ticker: str, after) -> int:
    """How many absorbed splits fall strictly after `after` for `ticker`."""
    if classified is None or classified.empty:
        return 0
    g = classified[(classified["ticker"].str.upper() == str(ticker).upper())
                   & (classified["verdict"] == "applied")]
    return int((pd.to_datetime(g["split_date"]) > pd.Timestamp(after)).sum())
