"""Tests for event parsing — ratio and date normalization across messy sources.

Ratios arrive in at least four formats from three scrapers plus an LLM. Getting one
backwards silently flips a reverse split into a forward split and corrupts the ratio
bucket filters.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from split_strategy.backtest.events import _to_ts, parse_ratio


class TestParseRatio:
    @pytest.mark.parametrize("text,expected", [
        ("1 : 25", 25.0),
        ("1:25", 25.0),
        ("1-for-8", 8.0),
        ("1 for 10", 10.0),
        ("1:2.00", 2.0),
        ("1-for-200", 200.0),
        ("1-for-32000", 32000.0),
        ("1 : 20.00", 20.0),
    ])
    def test_parses_known_formats(self, text, expected):
        assert parse_ratio(text) == pytest.approx(expected)

    def test_case_insensitive(self):
        assert parse_ratio("1-FOR-10") == pytest.approx(10.0)

    def test_range_takes_first_parsed_factor(self):
        assert parse_ratio("1-for-10 to 1-for-500") == pytest.approx(10.0)

    @pytest.mark.parametrize("text", ["10-for-1", "30-for-1", "2:1"])
    def test_forward_splits_are_rejected(self, text):
        """A forward split is out of scope; must be NaN, never a fraction that would
        silently pass a ratio-bucket filter."""
        assert math.isnan(parse_ratio(text))

    @pytest.mark.parametrize("text", [None, "", "unknown", "no digits here", "0:10"])
    def test_unparseable_is_nan(self, text):
        assert math.isnan(parse_ratio(text))

    def test_one_to_one_is_rejected(self):
        """1:1 is not a split; factor of exactly 1 must not pass."""
        assert math.isnan(parse_ratio("1:1"))


class TestToTs:
    def test_compact_yyyymmdd(self):
        assert _to_ts("20260715") == pd.Timestamp("2026-07-15")

    def test_iso_date(self):
        assert _to_ts("2026-07-15") == pd.Timestamp("2026-07-15")

    def test_datetime_passthrough(self):
        import datetime as dt
        assert _to_ts(dt.datetime(2026, 7, 15)) == pd.Timestamp("2026-07-15")

    def test_strips_timezone(self):
        """Naive timestamps throughout; a tz-aware value would break comparisons."""
        out = _to_ts(pd.Timestamp("2026-07-15", tz="America/New_York"))
        assert out.tzinfo is None

    @pytest.mark.parametrize("bad", [None, "", "Unknown", "not-a-date"])
    def test_unparseable_is_nat(self, bad):
        assert pd.isna(_to_ts(bad))
