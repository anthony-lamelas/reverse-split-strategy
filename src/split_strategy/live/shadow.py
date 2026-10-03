"""The shadow book: every candidate, quoted daily, whether or not it was traded.

The backtest cannot answer the two questions that decide whether this strategy is
tradeable, because neither is in daily bars: what does it cost to borrow these names,
and how wide is the spread when you actually cross it. The live system sees both every
morning and, until now, threw them away - the audit log kept the reason a candidate
was rejected and nothing about the candidate.

So each ENTER_NOW candidate gets one document here on its entry day, placed or not,
and a quote "mark" is appended twice a day until its cover date:

    {"ticker": "ABC", "entry_date": "2026-10-05",
     "effective_date": "2026-10-13", "cover_date": "2026-10-12",
     "ratio": 40.0, "confidence": "High",
     "live_outcome": "SKIPPED", "block_reason": "entry price $0.41 below the ...",
     "status": "OPEN",                        # CLOSED once the cover date has passed
     "marks": [{"date": "2026-10-05", "phase": "pre_open",   # 09:25, before the open
                "at": "2026-10-05T09:25:11-04:00",
                "bid": 0.41, "ask": 0.43, "bid_size": 300, "ask_size": 100,
                "last": 0.42, "spread_pct": 0.047,
                "is_shortable": true, "is_hard_to_borrow": true, "htb_rate": -63.25},
               {"date": "2026-10-05", "phase": "post_open", ...}]}   # 09:35

That is a paper trade of the names the filters reject - sub-$1 above all - priced at
quotes that existed, with the borrow rate that was on offer.

Everything here is best-effort and never raises into the trading run: losing a day
of measurements is a nuisance, halting a session over them would be a defect.
"""
from __future__ import annotations

from typing import Iterable, Mapping, Optional

from ..signals.portfolio_state import cover_date
from . import calendar as mcal

COLLECTION = "shadow_trades"

PRE_OPEN = "pre_open"
POST_OPEN = "post_open"


def candidate_doc(signal, entry_result: Optional[Mapping] = None) -> dict:
    """The document created on a candidate's entry day (no marks yet)."""
    entry_result = entry_result or {}
    outcome = entry_result.get("outcome")
    return {
        "ticker": (signal.ticker or "").upper(),
        "entry_date": signal.entry_date,
        "effective_date": signal.effective_date,
        "cover_date": cover_date(signal.effective_date).strftime("%Y-%m-%d"),
        "ratio": signal.ratio,
        "confidence": signal.confidence,
        "exchange": signal.exchange,
        "live_outcome": outcome,
        # Only a skip has a reason; for a placed order `detail` is the order text.
        "block_reason": entry_result.get("detail") if outcome == "SKIPPED" else None,
        "status": "OPEN",
        "marks": [],
    }


def build_mark(quote, shortability: Optional[Mapping], phase: str, now=None) -> dict:
    """One quote observation. `quote` or `shortability` may be None (recorded as such:
    "Schwab had no quote" is itself a measurement)."""
    now = mcal.now_et() if now is None else now
    short = shortability or {}
    return {
        "date": now.strftime("%Y-%m-%d"),
        "phase": phase,
        "at": now.isoformat(),
        "bid": getattr(quote, "bid", None),
        "ask": getattr(quote, "ask", None),
        "bid_size": getattr(quote, "bid_size", None),
        "ask_size": getattr(quote, "ask_size", None),
        "last": getattr(quote, "last", None),
        "spread_pct": getattr(quote, "spread_pct", None),
        "is_shortable": short.get("is_shortable"),
        "is_hard_to_borrow": short.get("is_hard_to_borrow"),
        "htb_rate": short.get("htb_rate"),
    }


def is_finished(doc: Mapping, today: str, phase: str) -> bool:
    """Has this shadow trade had its last mark?

    The cover is modelled at the open of `cover_date`, so the post-open mark on that
    day is the exit quote. A pre-open run on a later day closes anything the post-open
    run missed.
    """
    cover = doc.get("cover_date") or ""
    return today > cover or (today == cover and phase == POST_OPEN)


def shadow_return(doc: Mapping) -> Optional[float]:
    """Paper return of shorting at the entry-day bid and covering at the last ask.

    Prefers post-open quotes (the open is when the strategy trades) and falls back to
    pre-open. None when either side was never quoted. Before borrow cost and fees, and
    ignoring the take-profit and stop, which a twice-daily mark cannot see.
    """
    marks = doc.get("marks") or []

    def pick(day: str, side: str) -> Optional[float]:
        for phase in (POST_OPEN, PRE_OPEN):
            for m in marks:
                if m.get("date") == day and m.get("phase") == phase and m.get(side):
                    return float(m[side])
        return None

    entry = pick(doc.get("entry_date") or "", "bid")
    exit_ = pick(doc.get("cover_date") or "", "ask")
    if not entry or not exit_:
        return None
    return (entry - exit_) / entry


def record(phase: str, client, signals: Iterable = (), entry_results: Iterable[Mapping] = (),
           collection=None, now=None) -> tuple[int, Optional[str]]:
    """Open shadow trades for today's candidates, then mark every open one.

    Returns `(marks_written, error)`. Never raises.
    """
    try:
        from ..broker.quotes import get_quotes
        from ..broker.schwab_market_data import get_shortability_batch

        if collection is None:
            from ..database import get_collection
            collection = get_collection(COLLECTION)
        now = mcal.now_et() if now is None else now
        today = now.strftime("%Y-%m-%d")

        results = {(r.get("ticker") or "").upper(): r for r in entry_results}
        for s in signals:
            if s.status != "ENTER_NOW" or not s.ticker:
                continue
            doc = candidate_doc(s, results.get(s.ticker.upper()))
            # $setOnInsert: a repeat run must not wipe the marks or reopen a closed
            # trade. The outcome is $set so a later run's verdict replaces an earlier.
            outcome = {k: doc.pop(k) for k in ("live_outcome", "block_reason")}
            collection.update_one(
                {"ticker": doc["ticker"], "entry_date": doc["entry_date"]},
                {"$setOnInsert": doc, "$set": outcome}, upsert=True)

        open_docs = list(collection.find({"status": "OPEN"}))
        tickers = sorted({d["ticker"] for d in open_docs if d.get("ticker")})
        if not tickers:
            return 0, None
        quotes = get_quotes(client, tickers)
        short = get_shortability_batch(client, tickers)

        written = 0
        for d in open_docs:
            mark = build_mark(quotes.get(d["ticker"]), short.get(d["ticker"]), phase, now)
            # One mark per (date, phase): the filter makes a repeated run a no-op.
            res = collection.update_one(
                {"_id": d["_id"],
                 "marks": {"$not": {"$elemMatch": {"date": today, "phase": phase}}}},
                {"$push": {"marks": mark}})
            written += getattr(res, "modified_count", 0) or 0
            if is_finished(d, today, phase):
                collection.update_one({"_id": d["_id"]},
                                      {"$set": {"status": "CLOSED", "closed_on": today}})
        return written, None
    except Exception as e:
        return 0, str(e)[:200]
