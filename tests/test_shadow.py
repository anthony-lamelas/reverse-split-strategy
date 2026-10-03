"""Tests for the shadow book - the record of what every candidate would have cost."""
from __future__ import annotations

import pandas as pd

from split_strategy.broker.quotes import Quote
from split_strategy.live import shadow
from split_strategy.signals.generate import Signal


def sig(ticker="ABC", status="ENTER_NOW", entry_date="2026-10-05", effective_date="2026-10-13"):
    return Signal(ticker=ticker, company_name="T", filing_date="20261002",
                  effective_date=effective_date, ratio=40.0, confidence="High",
                  status=status, entry_date=entry_date, exchange="Nasdaq")


class FakeCollection:
    """The three Mongo calls `shadow.record` makes, over a list of dicts."""

    def __init__(self):
        self.docs = []

    def find(self, query):
        return [d for d in self.docs if d.get("status") == query.get("status")]

    def update_one(self, query, update, upsert=False):
        class Result:
            modified_count = 0

        marks_filter = query.get("marks")
        for d in self.docs:
            if any(d.get(k) != v for k, v in query.items() if k != "marks"):
                continue
            if marks_filter:
                want = marks_filter["$not"]["$elemMatch"]
                if any(all(m.get(k) == v for k, v in want.items()) for m in d["marks"]):
                    return Result
            d.update(update.get("$set", {}))
            for key, value in update.get("$push", {}).items():
                d[key].append(value)
            Result.modified_count = 1
            return Result
        if upsert:
            self.docs.append({"_id": len(self.docs), **update.get("$setOnInsert", {}),
                              **update.get("$set", {})})
        return Result


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, bid=0.41, ask=0.43):
        self.bid, self.ask = bid, ask

    def get_quotes(self, tickers):
        return FakeResponse({t: {
            "quote": {"bidPrice": self.bid, "askPrice": self.ask, "lastPrice": self.bid,
                      "bidSize": 300, "askSize": 100},
            "reference": {"isShortable": True, "isHardToBorrow": True, "htbRate": -63.25},
        } for t in tickers})


def at(day, hhmm="09:25"):
    return pd.Timestamp(f"{day} {hhmm}", tz="America/New_York")


SKIPPED = [{"ticker": "ABC", "outcome": "SKIPPED", "detail": "entry price below the floor"}]


class TestCandidateDoc:
    def test_cover_date_is_the_session_before_the_split(self):
        doc = shadow.candidate_doc(sig(), SKIPPED[0])
        assert doc["cover_date"] == "2026-10-12"
        assert doc["live_outcome"] == "SKIPPED" and "floor" in doc["block_reason"]

    def test_placed_order_has_no_block_reason(self):
        doc = shadow.candidate_doc(sig(), {"outcome": "SUBMITTED", "detail": "submitted @ 0.40"})
        assert doc["live_outcome"] == "SUBMITTED" and doc["block_reason"] is None


class TestRecord:
    def test_rejected_candidate_is_opened_and_marked(self):
        coll = FakeCollection()
        n, err = shadow.record(shadow.PRE_OPEN, FakeClient(), [sig()], SKIPPED,
                               collection=coll, now=at("2026-10-05"))
        assert err is None and n == 1
        doc = coll.docs[0]
        mark = doc["marks"][0]
        assert (mark["bid"], mark["ask"], mark["htb_rate"]) == (0.41, 0.43, -63.25)
        assert mark["phase"] == "pre_open" and mark["is_shortable"] is True

    def test_holding_and_upcoming_signals_do_not_open_shadow_trades(self):
        coll = FakeCollection()
        shadow.record(shadow.PRE_OPEN, FakeClient(),
                      [sig(status="HOLDING"), sig("XYZ", status="UPCOMING")], [],
                      collection=coll, now=at("2026-10-05"))
        assert coll.docs == []

    def test_a_repeated_run_does_not_duplicate_the_mark(self):
        coll = FakeCollection()
        for _ in range(2):
            shadow.record(shadow.PRE_OPEN, FakeClient(), [sig()], SKIPPED,
                          collection=coll, now=at("2026-10-05"))
        assert len(coll.docs) == 1 and len(coll.docs[0]["marks"]) == 1

    def test_marks_accumulate_and_the_trade_closes_after_its_cover_quote(self):
        coll = FakeCollection()
        shadow.record(shadow.PRE_OPEN, FakeClient(), [sig()], SKIPPED,
                      collection=coll, now=at("2026-10-05"))
        shadow.record(shadow.POST_OPEN, FakeClient(0.40, 0.41), collection=coll,
                      now=at("2026-10-05", "09:35"))
        shadow.record(shadow.PRE_OPEN, FakeClient(0.30, 0.33), collection=coll,
                      now=at("2026-10-12"))
        doc = coll.docs[0]
        assert doc["status"] == "OPEN", "the post-open quote on the cover date is still owed"
        shadow.record(shadow.POST_OPEN, FakeClient(0.30, 0.32), collection=coll,
                      now=at("2026-10-12", "09:35"))
        assert doc["status"] == "CLOSED" and len(doc["marks"]) == 4
        # short at the entry-day post-open bid 0.40, cover at the cover-day ask 0.32
        assert shadow.shadow_return(doc) == (0.40 - 0.32) / 0.40

    def test_database_failure_is_reported_not_raised(self):
        class Broken:
            def find(self, q):
                raise RuntimeError("mongo down")

        n, err = shadow.record(shadow.PRE_OPEN, FakeClient(), collection=Broken(),
                               now=at("2026-10-05"))
        assert n == 0 and "mongo down" in err


class TestShadowReturn:
    def test_unquoted_side_gives_none(self):
        assert shadow.shadow_return({"entry_date": "2026-10-05", "cover_date": "2026-10-12",
                                     "marks": []}) is None
