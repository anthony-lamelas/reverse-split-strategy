"""Tests for the point-in-time event pipeline: read a filing, date it, keep it or not.

The old backtest's central flaw was entering trades before the exit date had been
published. These tests pin the rules that prevent that from coming back.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

from split_strategy.backtest.events import executable_events
from split_strategy.edgar import scanner
from split_strategy.live import calendar as mcal

_spec = importlib.util.spec_from_file_location(
    "backfill_events", Path(__file__).resolve().parent.parent / "scripts" / "backfill_events.py")
backfill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backfill)

HEADER = ("<SEC-DOCUMENT>0001493152-26-045438.txt : 20261002\n"
          "<ACCEPTANCE-DATETIME>20261002080052\nACCESSION NUMBER: 0001493152-26-045438\n")


class TestScannerParsing:
    def test_acceptance_time_is_read_from_the_header(self):
        assert scanner.parse_acceptance(HEADER) == "2026-10-02T08:00:52"

    def test_missing_acceptance_time_is_none(self):
        assert scanner.parse_acceptance("<SEC-DOCUMENT>nothing here") is None

    def test_trading_symbol_comes_from_the_cover_page(self):
        raw = '<ix:nonNumeric name="dei:TradingSymbol" contextRef="c1">vtak</ix:nonNumeric>'
        assert scanner.parse_trading_symbol(raw) == "VTAK"

    def test_no_cover_page_symbol_is_none(self):
        assert scanner.parse_trading_symbol("<p>6-K with no cover tags</p>") is None

    def test_text_survives_markup_the_parser_rejects(self, monkeypatch):
        import bs4

        def boom(*a, **k):
            raise AssertionError("expected name token")

        monkeypatch.setattr(bs4, "BeautifulSoup", boom)
        text = scanner.filing_text("<p>a 1-for-10 <b>reverse stock split</b></p>")
        assert "reverse stock split" in text and "<" not in text

    def test_no_keyword_means_no_llm_call(self, monkeypatch):
        monkeypatch.setattr(scanner, "analyze_with_llm",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))
        assert scanner.classify("<p>quarterly dividend declared</p>", "X", "20261002", "k") is None

    def test_classify_adds_acceptance_time_and_symbol(self, monkeypatch):
        monkeypatch.setattr(scanner, "analyze_with_llm",
                            lambda *a, **k: {"is_reverse_split": True, "confidence": "High"})
        raw = HEADER + '<x name="dei:TradingSymbol">ABC</x> reverse stock split of 1-for-10'
        out = scanner.classify(raw, "X", "20261002", "k")
        assert out["accepted_at"] == "2026-10-02T08:00:52" and out["trading_symbol"] == "ABC"


class TestFirstOpenAfter:
    def test_premarket_filing_trades_the_same_day(self):
        assert mcal.first_open_after("2026-10-02T08:00:52") == pd.Timestamp("2026-10-02")

    def test_filing_during_the_session_waits_for_the_next_open(self):
        assert mcal.first_open_after("2026-10-01T10:15:00") == pd.Timestamp("2026-10-02")

    def test_after_hours_friday_filing_trades_monday(self):
        assert mcal.first_open_after("2026-10-02T17:05:00") == pd.Timestamp("2026-10-05")

    def test_holiday_is_skipped(self):
        # Accepted the Friday evening before Labor Day (2026-09-07).
        assert mcal.first_open_after("2026-09-04T18:00:00") == pd.Timestamp("2026-09-08")


def doc(**kw):
    base = dict(adsh="a-1", ticker="ABC", form="8-K", filing_date="2026-10-01",
                accepted_at="2026-10-01T17:05:00", effective_date="2026-10-09",
                ratio="1-for-10", confidence="High", is_reverse_split=True,
                is_future_split=True, is_definitive=True)
    base.update(kw)
    return base


class TestExecutableEvents:
    def test_definitive_dated_split_is_an_event(self):
        ev = executable_events([doc()])
        row = ev.iloc[0]
        assert row["t_split"] == pd.Timestamp("2026-10-09") and row["ratio"] == 10.0
        assert row["entry_first_open"] == pd.Timestamp("2026-10-02")
        assert row["entry_live"] == pd.Timestamp("2026-10-02")

    def test_premarket_filing_can_be_entered_a_day_before_live_enters(self):
        row = executable_events([doc(accepted_at="2026-10-01T08:00:00")]).iloc[0]
        assert row["entry_first_open"] == pd.Timestamp("2026-10-01")
        assert row["entry_live"] == pd.Timestamp("2026-10-02")

    def test_proposals_past_splits_and_non_splits_are_dropped(self):
        docs = [doc(adsh="a", is_definitive=False), doc(adsh="b", effective_date="2026-09-30"),
                doc(adsh="c", is_reverse_split=False), doc(adsh="d", confidence="Medium")]
        assert executable_events(docs).empty

    def test_unknown_effective_date_is_not_executable(self):
        assert executable_events([doc(effective_date="Unknown")]).empty

    def test_split_effective_by_the_first_open_leaves_nothing_to_trade(self):
        assert executable_events([doc(effective_date="2026-10-02")]).empty

    def test_unresolved_ticker_is_dropped(self):
        assert executable_events([doc(ticker="UNKNOWN")]).empty

    def test_a_split_announced_twice_keeps_the_first_filing(self):
        docs = [doc(adsh="late", accepted_at="2026-10-05T08:00:00", filing_date="2026-10-05"),
                doc(adsh="early")]
        ev = executable_events(docs)
        assert len(ev) == 1 and ev.iloc[0]["adsh"] == "early"


class TestBackfillHelpers:
    def test_display_name_with_several_tickers_takes_the_first(self):
        name, ticker = backfill.parse_display_name(
            "Wheeler Real Estate Investment Trust, Inc.  (WHLR, WHLRD, WHLRP)  (CIK 0001527541)")
        assert ticker == "WHLR" and name.startswith("Wheeler")

    def test_display_name_without_a_ticker(self):
        name, ticker = backfill.parse_display_name("Private Shell Co  (CIK 0001234567)")
        assert (name, ticker) == ("Private Shell Co", None)

    def test_hits_collapse_to_one_filing_per_accession(self):
        src = {"adsh": "0001493152-26-045438", "ciks": ["0002024258"], "form": "6-K",
               "file_date": "2026-10-02",
               "display_names": ["PHAOS TECHNOLOGY HOLDINGS (CAYMAN) Ltd  (POAS)  (CIK 0002024258)"]}
        filings = backfill.filings_from_hits([{"_source": src}, {"_source": dict(src)}])
        assert len(filings) == 1
        f = filings[0]
        assert f["listed_ticker"] == "POAS"
        assert f["filing_url"].endswith("/edgar/data/2024258/0001493152-26-045438.txt")

    def test_cover_page_symbol_beats_the_listed_one(self):
        filing = {"adsh": "a", "cik": "123", "company_name": "X", "form": "8-K",
                  "filing_date": "2026-10-02", "filing_url": "u", "listed_ticker": "NEWX"}
        d = backfill.event_doc(filing, {"trading_symbol": "OLDX", "is_reverse_split": True,
                                        "is_definitive_split_announcement": True})
        assert (d["ticker"], d["ticker_source"], d["is_definitive"]) == ("OLDX", "cover", True)
        d = backfill.event_doc(filing, {"trading_symbol": None})
        assert (d["ticker"], d["ticker_source"]) == ("NEWX", "listed")

    def test_weekdays_skip_the_weekend(self):
        from datetime import date
        days = list(backfill.weekdays(date(2026, 10, 2), date(2026, 10, 5)))
        assert days == [date(2026, 10, 2), date(2026, 10, 5)]


class TestReadablePart:
    def test_stops_before_the_first_binary_document(self):
        raw = ("<DOCUMENT>\n<TYPE>8-K\nreverse stock split\n</DOCUMENT>\n"
               "<DOCUMENT>\n<TYPE>EX-99.1\npress release\n</DOCUMENT>\n"
               "<DOCUMENT>\n<TYPE>GRAPHIC\nbegin 644 logo.jpg\nM_]C_X\n</DOCUMENT>\n")
        kept = scanner.readable_part(raw)
        assert "press release" in kept and "begin 644" not in kept
        assert kept.endswith("</DOCUMENT>\n")

    def test_all_text_submission_is_untouched(self):
        raw = "<DOCUMENT>\n<TYPE>8-K\nreverse stock split\n</DOCUMENT>\n"
        assert scanner.readable_part(raw) == raw


class TestReverseFactor:
    def test_either_orientation_gives_the_same_factor(self):
        from split_strategy.backtest.events import reverse_factor
        assert reverse_factor("1-for-30") == reverse_factor("30-for-1") == 30.0
        assert reverse_factor("160-for-1") == 160.0

    def test_unparseable_and_one_for_one_stay_nan(self):
        from split_strategy.backtest.events import reverse_factor
        assert reverse_factor("Unknown") != reverse_factor("Unknown")
        assert reverse_factor("1-for-1") != reverse_factor("1-for-1")


class TestFutureIsDecidedFromDates:
    def test_llm_past_flag_does_not_drop_a_split_that_is_still_ahead(self):
        assert len(executable_events([doc(is_future_split=False)])) == 1

    def test_scanner_trusts_dates_over_the_flag(self):
        assert scanner.split_is_ahead({"effective_date": "2022-02-04", "is_future_split": False}, "20220126")
        assert not scanner.split_is_ahead({"effective_date": "2022-01-20", "is_future_split": True}, "20220126")

    def test_scanner_falls_back_to_the_flag_without_a_date(self):
        assert not scanner.split_is_ahead({"effective_date": "Unknown", "is_future_split": False}, "20220126")
        assert scanner.split_is_ahead({"effective_date": "Unknown", "is_future_split": True}, "20220126")

    def test_placeholder_tickers_are_dropped(self):
        assert executable_events([doc(ticker="NONE")]).empty
