"""Tests for the scanner helpers that decide WHICH symbol and WHICH days get scanned.

Both failed silently in production: one tagged a company's split with its warrant,
the other never looked at Friday's filings on a Monday.
"""
from __future__ import annotations

from datetime import date

from split_strategy.edgar.utils import primary_ticker_by_cik, scan_target_dates
from split_strategy.live import calendar as mcal


class TestPrimaryTickerByCik:
    def test_common_stock_wins_over_later_listed_securities(self):
        mapping = {"WHLR": "0001527541", "WHLRD": "0001527541",
                   "WHLRP": "0001527541", "WHLRL": "0001527541"}
        assert primary_ticker_by_cik(mapping)["0001527541"] == "WHLR"

    def test_warrant_listed_second_is_not_chosen(self):
        mapping = {"VEEA": "0001840317", "VEEAW": "0001840317"}
        assert primary_ticker_by_cik(mapping)["1840317"] == "VEEA"

    def test_keyed_both_padded_and_unpadded(self):
        out = primary_ticker_by_cik({"ABC": "0000001234"})
        assert out["0000001234"] == out["1234"] == "ABC"

    def test_single_ticker_companies_unchanged(self):
        out = primary_ticker_by_cik({"ABC": "0000000001", "XYZ": "0000000002"})
        assert out["1"] == "ABC" and out["2"] == "XYZ"


class TestScanTargetDates:
    def test_midweek_is_yesterday_and_today(self):
        assert scan_target_dates(date(2026, 9, 23), mcal.is_trading_day) == [
            date(2026, 9, 22), date(2026, 9, 23)]

    def test_monday_reaches_back_to_friday(self):
        assert scan_target_dates(date(2026, 9, 21), mcal.is_trading_day) == [
            date(2026, 9, 18), date(2026, 9, 19), date(2026, 9, 20), date(2026, 9, 21)]

    def test_tuesday_after_labor_day_reaches_back_to_friday(self):
        dates = scan_target_dates(date(2026, 9, 8), mcal.is_trading_day)
        assert dates[0] == date(2026, 9, 4) and dates[-1] == date(2026, 9, 8)

    def test_saturday_covers_friday(self):
        assert scan_target_dates(date(2026, 9, 19), mcal.is_trading_day) == [
            date(2026, 9, 18), date(2026, 9, 19)]
