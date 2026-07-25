"""Build normalized event frames from MongoDB for the backtest.

An "event frame" has columns: ticker, t_ann (announcement Timestamp),
t_split (execution Timestamp), ratio (float, reverse-split factor, e.g. 10.0 for 1:10).

Two sources:
- `build_events_from_early_edgar`: the forward-looking scanner collection
  (early_edgar_splits). filing_date -> t_ann, effective_date -> t_split. This is the
  collection the live signal generator also reads, so it is the most faithful source
  for "how would the automated strategy have performed recently".
- `build_events_from_tier_ab`: the original grid-search join (reverse_splits x
  reverse_splits_edgar, tier A/B). Kept for parity with the published backtest.
"""
from __future__ import annotations

import re
from typing import Optional

import pandas as pd
from bson.objectid import ObjectId

from ..database import (
    get_collection,
    REVERSE_SPLITS_COLLECTION,
    EDGAR_COLLECTION,
    EARLY_WARNINGS_COLLECTION,
)


def parse_ratio(ratio_str) -> float:
    """Parse a reverse-split ratio string to a single float factor.

    Handles the formats seen across sources:
      "1 : 25"   -> 25.0
      "1-for-8"  -> 8.0
      "1 for 10" -> 10.0
      "1:2.00"   -> 2.0
    Ranges like "1-for-10 to 1-for-500" -> the first parsed factor (10.0).
    Forward splits (factor < 1, e.g. "10-for-1") -> NaN (out of strategy scope).
    Unparseable -> NaN.
    """
    if ratio_str is None:
        return float("nan")
    s = str(ratio_str).strip().lower()
    # Normalize separators: "for" and "-for-" -> ":"
    s = s.replace("-for-", ":").replace(" for ", ":").replace("for", ":")
    m = re.search(r"(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", s)
    if not m:
        return float("nan")
    num, den = float(m.group(1)), float(m.group(2))
    if num <= 0 or den <= 0:
        return float("nan")
    factor = den / num  # "1:25" -> 25 ; "10:1" (forward) -> 0.1
    if factor < 1:
        return float("nan")  # forward split, not in scope
    return factor


def _to_ts(value) -> pd.Timestamp:
    """Parse a date that may be 'YYYYMMDD', 'YYYY-MM-DD', or a datetime. tz-naive."""
    if value is None:
        return pd.NaT
    if isinstance(value, str) and re.fullmatch(r"\d{8}", value.strip()):
        return pd.to_datetime(value.strip(), format="%Y%m%d", errors="coerce")
    ts = pd.to_datetime(value, errors="coerce")
    if isinstance(ts, pd.Timestamp) and ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    return ts


def build_events_from_early_edgar(
    min_confidence: Optional[str] = None,
    require_executed: bool = True,
    as_of: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    """Build events from the early_edgar_splits collection.

    Args:
        min_confidence: if set to 'High' or 'Medium', keep only rows at/above that
            confidence (order: High > Medium > Low). None keeps all.
        require_executed: keep only events whose t_split <= as_of (completed trades).
        as_of: reference "today" (defaults to now).
    """
    as_of = pd.Timestamp.now().normalize() if as_of is None else as_of
    conf_rank = {"low": 0, "medium": 1, "high": 2}
    min_rank = conf_rank.get((min_confidence or "").lower(), -1)

    docs = list(get_collection(EARLY_WARNINGS_COLLECTION).find({}))
    rows = []
    for d in docs:
        ticker = d.get("ticker")
        if not ticker or ticker == "UNKNOWN":
            continue
        if min_rank >= 0 and conf_rank.get(str(d.get("confidence", "")).lower(), -1) < min_rank:
            continue
        t_ann = _to_ts(d.get("filing_date"))
        t_split = _to_ts(d.get("effective_date"))
        if pd.isna(t_ann) or pd.isna(t_split):
            continue
        if t_ann >= t_split:  # need announcement strictly before execution
            continue
        if require_executed and t_split > as_of:
            continue
        rows.append(
            dict(
                ticker=ticker,
                t_ann=t_ann,
                t_split=t_split,
                ratio=parse_ratio(d.get("ratio")),
                confidence=d.get("confidence"),
                company_name=d.get("company_name"),
            )
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # De-duplicate: keep the earliest announcement per (ticker, t_split)
    df = df.sort_values("t_ann").drop_duplicates(subset=["ticker", "t_split"], keep="first")
    return df.reset_index(drop=True)


def build_events_from_tier_ab() -> pd.DataFrame:
    """Build events via the original grid-search join (reverse_splits x edgar tier A/B)."""
    edg = get_collection(EDGAR_COLLECTION)
    rev = get_collection(REVERSE_SPLITS_COLLECTION)

    edgar_filings = list(edg.find({"tier": {"$in": ["A", "B"]}}))
    split_ids = {str(f["reverse_splits_id"]) for f in edgar_filings if f.get("reverse_splits_id")}
    object_ids = []
    for sid in split_ids:
        try:
            object_ids.append(ObjectId(sid))
        except Exception:
            pass
    splits = {str(s["_id"]): s for s in rev.find({"_id": {"$in": object_ids}})}

    rows = []
    for sid in split_ids:
        sp = splits.get(sid)
        if not sp:
            continue
        ticker = sp.get("Symbol")
        if not ticker:
            continue
        filings = [f for f in edgar_filings if str(f.get("reverse_splits_id")) == sid]
        fdates = pd.to_datetime([f.get("filing_date") for f in filings], errors="coerce")
        fdates = fdates[~fdates.isna()]
        if len(fdates) == 0:
            continue
        rows.append(
            dict(
                ticker=ticker,
                t_ann=fdates.min(),
                t_split=_to_ts(sp.get("Date")),
                ratio=parse_ratio(sp.get("Split Ratio")),
            )
        )
    df = pd.DataFrame(rows).dropna(subset=["t_ann", "t_split"])
    return df.sort_values("t_ann").reset_index(drop=True)
