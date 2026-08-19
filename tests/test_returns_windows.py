"""Tests for the split-relative return windows.

Two display-layer bugs, both from docs/VALIDATION_REPORT.md. This module is imported
only by ui/dashboard.py and never by the backtest engine, so neither bug touched the
published strategy numbers - but both made the dashboard quietly wrong:

  2.2  Windows were counted in CALENDAR days and then snapped to the nearest bar, so
       every window was shorter than its label and adjacent windows could collapse
       onto the same bar around a weekend.
  2.3  A tz-aware yfinance index was compared against naive datetimes, raising
       TypeError into a broad `except` that returned `None, None` - indistinguishable
       from "this ticker has no data", and reached only on the sparse tickers where
       the fallback path runs.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from split_strategy.analysis import returns as rets

ET = "America/New_York"


def make_hist(dates, closes, tz=ET) -> pd.DataFrame:
    idx = pd.DatetimeIndex(pd.to_datetime(dates))
    if tz:
        idx = idx.tz_localize(tz)
    return pd.DataFrame({"Close": [float(c) for c in closes]}, index=idx)


class FakeTicker:
    """Stands in for yf.Ticker. `ranged` is what history(start=,end=) returns."""

    def __init__(self, ranged=None, period=None):
        self._ranged = ranged if ranged is not None else pd.DataFrame()
        self._period = period if period is not None else pd.DataFrame()

    def history(self, start=None, end=None, period=None):
        return self._period if period is not None else self._ranged


@pytest.fixture
def patch_yf(monkeypatch):
    def install(ticker_obj):
        monkeypatch.setattr(rets.yf, "Ticker", lambda symbol: ticker_obj)
    return install


# Ten consecutive sessions: Mon 3 Aug .. Fri 14 Aug 2026, skipping both weekends.
SESSIONS = ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07",
            "2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14"]
# Priced so each session is a clean, distinguishable level.
CLOSES = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19]


class TestTradingDayWindows:
    def test_adjacent_windows_do_not_collapse_across_a_weekend(self, patch_yf):
        """The 2.2 regression: a Monday split made 1d_before and 3d_before identical.

        Split anchored on Mon 10 Aug. In calendar days, -1 lands on Sunday and -3 on
        Friday, and both snap back to Friday 7 Aug - one bar, two labels.
        """
        patch_yf(FakeTicker(ranged=make_hist(SESSIONS, CLOSES)))

        _, out = rets.get_stock_price_data_around_split("ABC", datetime(2026, 8, 10))

        assert out["1d_before"] != out["3d_before"]

    def test_offsets_are_counted_in_sessions(self, patch_yf):
        patch_yf(FakeTicker(ranged=make_hist(SESSIONS, CLOSES)))

        _, out = rets.get_stock_price_data_around_split("ABC", datetime(2026, 8, 13))

        # Anchor is 13 Aug (close 18). One session back is 12 Aug (17), three back is
        # 10 Aug (15), five back is 6 Aug (13).
        assert out["1d_before"] == pytest.approx((18 / 17 - 1) * 100, abs=0.01)
        assert out["3d_before"] == pytest.approx((18 / 15 - 1) * 100, abs=0.01)
        assert out["5d_before"] == pytest.approx((18 / 13 - 1) * 100, abs=0.01)

    def test_forward_windows_are_also_sessions(self, patch_yf):
        patch_yf(FakeTicker(ranged=make_hist(SESSIONS, CLOSES)))

        _, out = rets.get_stock_price_data_around_split("ABC", datetime(2026, 8, 7))

        # Anchor 7 Aug (14); +1 session is 10 Aug (15), +3 is 12 Aug (17).
        assert out["1d_after"] == pytest.approx((15 / 14 - 1) * 100, abs=0.01)
        assert out["3d_after"] == pytest.approx((17 / 14 - 1) * 100, abs=0.01)

    def test_windows_beyond_available_history_are_none_not_clamped(self, patch_yf):
        """Silently snapping to the nearest bar reported a 20d return from 4 sessions."""
        patch_yf(FakeTicker(ranged=make_hist(SESSIONS, CLOSES)))

        _, out = rets.get_stock_price_data_around_split("ABC", datetime(2026, 8, 5))

        assert out["20d_before"] is None
        assert out["10d_before"] is None
        assert out["1d_before"] is not None


class TestTimezoneHandling:
    def test_tz_aware_fallback_path_returns_data(self, patch_yf):
        """The 2.3 regression: this raised TypeError and looked like 'no data'."""
        patch_yf(FakeTicker(ranged=pd.DataFrame(),
                            period=make_hist(SESSIONS, CLOSES, tz=ET)))

        chart, out = rets.get_stock_price_data_around_split("ABC", datetime(2026, 8, 13))

        assert chart is not None and out is not None
        assert out["1d_before"] is not None

    def test_naive_index_fallback_still_works(self, patch_yf):
        patch_yf(FakeTicker(ranged=pd.DataFrame(),
                            period=make_hist(SESSIONS, CLOSES, tz=None)))

        chart, out = rets.get_stock_price_data_around_split("ABC", datetime(2026, 8, 13))

        assert chart is not None and out is not None

    def test_genuinely_empty_history_still_returns_none(self, patch_yf):
        patch_yf(FakeTicker(ranged=pd.DataFrame(), period=pd.DataFrame()))

        assert rets.get_stock_price_data_around_split("ABC", datetime(2026, 8, 13)) == (None, None)
