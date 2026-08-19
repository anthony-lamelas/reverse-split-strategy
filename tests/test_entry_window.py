"""Tests for the entry-timing window.

Strategy B enters at the open. Scheduled runs were observed firing at 12:15, 17:07 and
23:32 ET because the host slept through its 9:25 trigger and Task Scheduler's
StartWhenAvailable caught up whenever the machine next woke. Filling those entries
would not be the trade the backtest measured, so live sessions outside this window
halt instead.

The load-bearing case is `test_on_time_run_at_0925_is_in_window`: 9:25 is *before* the
9:30 open, so a naive `is_market_hours()` check would have rejected exactly the run
that is on time and accepted every late one.
"""
from __future__ import annotations

import pandas as pd
import pytest

from split_strategy.live import calendar as mcal

ET = "America/New_York"

# A normal Thursday.
DAY = "2026-08-20"


def et(hhmm: str) -> pd.Timestamp:
    return pd.Timestamp(f"{DAY} {hhmm}", tz=ET)


class TestEntryWindow:
    def test_on_time_run_at_0925_is_in_window(self):
        ok, mins = mcal.in_entry_window(et("09:25"))
        assert ok
        assert mins == pytest.approx(5.0)

    def test_the_open_itself_is_in_window(self):
        ok, mins = mcal.in_entry_window(et("09:30"))
        assert ok
        assert mins == pytest.approx(0.0)

    def test_too_early_is_out_of_window(self):
        ok, mins = mcal.in_entry_window(et("09:14"))
        assert not ok
        assert mins == pytest.approx(16.0)

    def test_too_late_is_out_of_window(self):
        ok, mins = mcal.in_entry_window(et("09:46"))
        assert not ok
        assert mins == pytest.approx(-16.0)

    def test_window_edges_are_inclusive(self):
        assert mcal.in_entry_window(et("09:15"))[0]
        assert mcal.in_entry_window(et("09:45"))[0]

    @pytest.mark.parametrize("when", ["12:15", "17:07", "23:32", "13:53", "22:34"])
    def test_the_observed_late_runs_are_all_rejected(self, when):
        """The actual mistimed run times taken from logs/trading_audit.jsonl."""
        ok, _ = mcal.in_entry_window(et(when))
        assert not ok

    def test_window_is_configurable(self):
        late = et("10:00")  # 30 min after the open
        assert not mcal.in_entry_window(late)[0]
        assert mcal.in_entry_window(late, after_min=45)[0]

    def test_naive_timestamps_are_treated_as_eastern(self):
        """A naive stamp must not be read as UTC, or 'today' shifts by a session."""
        ok, mins = mcal.in_entry_window(pd.Timestamp(f"{DAY} 09:25"))
        assert ok
        assert mins == pytest.approx(5.0)


class TestDescribeWindow:
    def test_before_and_after_read_correctly(self):
        assert mcal.describe_window(5.0) == "5 min before the open"
        assert mcal.describe_window(-165.0) == "165 min after the open"

    def test_zero_reads_as_before(self):
        assert mcal.describe_window(0.0) == "0 min before the open"
