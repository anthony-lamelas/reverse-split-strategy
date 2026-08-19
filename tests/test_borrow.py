"""Tests for borrow cost: what staying short actually costs.

Two defects being pinned here:

  1. The veto capped the annualized RATE and ignored holding period, so a
     100%/yr name held 5 days (1.4% of notional) and one held 150 days (41%)
     passed identically.
  2. It was checked once at entry and never again, so a mid-hold spike - the
     squeeze signature, arriving while the position is already going wrong -
     was invisible.
"""
from __future__ import annotations

import pandas as pd
import pytest

from split_strategy import borrow as brw


class TestHoldingDays:
    def test_counts_calendar_days_to_the_exit(self):
        assert brw.holding_days("2026-08-20", "2026-09-03") == 14

    def test_same_day_is_zero(self):
        assert brw.holding_days("2026-08-20", "2026-08-20") == 0

    def test_accepts_timestamps(self):
        assert brw.holding_days(pd.Timestamp("2026-08-20"),
                                pd.Timestamp("2026-08-27")) == 7

    @pytest.mark.parametrize("bad", ["Unknown", None, "", "not-a-date"])
    def test_unusable_dates_return_none_rather_than_guessing(self, bad):
        assert brw.holding_days(bad, "2026-09-03") is None
        assert brw.holding_days("2026-08-20", bad) is None

    def test_reversed_dates_return_none(self):
        assert brw.holding_days("2026-09-03", "2026-08-20") is None


class TestExpectedCost:
    def test_the_case_the_old_veto_could_not_see(self):
        """Same 100%/yr rate, wildly different cost - and both used to pass."""
        assert brw.expected_cost_pct(100, 5) == pytest.approx(0.0137, abs=1e-4)
        assert brw.expected_cost_pct(100, 150) == pytest.approx(0.4110, abs=1e-4)

    def test_schwab_reports_rates_negative_and_the_sign_is_dropped(self):
        assert brw.expected_cost_pct(-63.2, 148) == brw.expected_cost_pct(63.2, 148)

    def test_typical_trade_costs_almost_nothing(self):
        """Median 7.5%/yr over the median 11-day hold."""
        assert brw.expected_cost_pct(7.5, 11) < 0.003

    def test_zero_rate_is_free(self):
        assert brw.expected_cost_pct(0, 100) == 0.0

    def test_unknown_holding_period_is_none_not_zero(self):
        """Zero would read as 'free', which is the dangerous default."""
        assert brw.expected_cost_pct(100, None) is None


class TestDescribe:
    def test_states_rate_days_and_cost(self):
        text = brw.describe(-63.2, 148)
        assert "63%/yr" in text and "148d" in text and "25.6%" in text

    def test_unknown_period_says_so(self):
        assert "unknown holding period" in brw.describe(50, None)


class TestSpikeDetection:
    def test_breaching_the_absolute_ceiling_trips(self):
        assert brw.has_spiked(entry_rate=8.0, current_rate=113.0)

    def test_a_large_multiple_trips(self):
        assert brw.has_spiked(entry_rate=15.0, current_rate=60.0)

    def test_a_stable_rate_does_not_trip(self):
        assert not brw.has_spiked(entry_rate=8.0, current_rate=9.0)

    def test_a_big_multiple_on_a_trivial_rate_does_not_page(self):
        """0.5% -> 2% is 4x but costs nothing worth waking up for."""
        assert not brw.has_spiked(entry_rate=0.5, current_rate=2.0)

    def test_free_at_entry_and_expensive_now_trips(self):
        """The common case: 0% borrow at entry, materially expensive later."""
        assert brw.has_spiked(entry_rate=0.0, current_rate=45.0)

    def test_free_at_entry_and_still_cheap_does_not_trip(self):
        assert not brw.has_spiked(entry_rate=0.0, current_rate=3.0)

    def test_missing_current_rate_never_gives_a_false_alarm(self):
        assert not brw.has_spiked(entry_rate=8.0, current_rate=None)

    def test_unknown_entry_rate_still_catches_the_ceiling(self):
        """No baseline to compare against, but 150%/yr is alarming regardless."""
        assert brw.has_spiked(entry_rate=None, current_rate=150.0)
        assert not brw.has_spiked(entry_rate=None, current_rate=20.0)

    def test_thresholds_are_configurable(self):
        assert not brw.has_spiked(8.0, 30.0, absolute_ceiling=100, multiple=5.0)
        assert brw.has_spiked(8.0, 30.0, absolute_ceiling=100, multiple=3.0)
