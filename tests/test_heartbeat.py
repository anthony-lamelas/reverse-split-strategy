"""Tests for the watchdog's judgement.

The heartbeat is the safety net under every other guard in the live path: it is the
only mechanism that can report "the trading host never ran at all", because the host
itself is asleep and running no code when that happens. So its verdict has to be the
piece least likely to be quietly wrong, and `evaluate()` is deliberately pure so it
can be tested with no database.
"""
from __future__ import annotations

import pandas as pd

from split_strategy.live import heartbeat as hb

TRADING_DAY = "2026-08-20"      # Thursday
WEEKEND = "2026-08-22"          # Saturday
HOLIDAY = "2026-11-26"          # Thanksgiving


def beat(in_window=True, exit_code=0, ran_at="2026-08-20T09:26:11-04:00",
         mode="DRY_RUN") -> dict:
    return {"date": TRADING_DAY, "ran_at": ran_at, "mode": mode,
            "in_window": in_window, "exit_code": exit_code}


class TestEvaluate:
    def test_a_healthy_in_window_run_passes(self):
        code, msg = hb.evaluate([beat()], TRADING_DAY)
        assert code == hb.OK
        assert "OK" in msg

    def test_no_heartbeat_on_a_trading_day_fails(self):
        """The case that went unnoticed for weeks: the host simply never ran."""
        code, msg = hb.evaluate([], TRADING_DAY)
        assert code == hb.MISSED
        assert "no trading run recorded" in msg

    def test_a_late_run_fails_even_though_it_ran(self):
        """It executed, but hours after the open - not the trade that was backtested."""
        code, msg = hb.evaluate([beat(in_window=False,
                                      ran_at="2026-08-20T17:07:00-04:00")], TRADING_DAY)
        assert code == hb.MISSED
        assert "OUTSIDE the entry window" in msg

    def test_an_in_window_run_that_halted_fails(self):
        code, msg = hb.evaluate([beat(exit_code=5)], TRADING_DAY)
        assert code == hb.MISSED
        assert "exited 5" in msg

    def test_one_healthy_run_among_repeats_is_enough(self):
        """The trigger repeats across the window; later repeats legitimately no-op."""
        beats = [beat(exit_code=5), beat(), beat(exit_code=5)]
        assert hb.evaluate(beats, TRADING_DAY)[0] == hb.OK

    def test_late_runs_can_be_accepted_explicitly(self):
        beats = [beat(in_window=False)]
        assert hb.evaluate(beats, TRADING_DAY)[0] == hb.MISSED
        assert hb.evaluate(beats, TRADING_DAY, allow_out_of_window=True)[0] == hb.OK

    def test_weekends_and_holidays_expect_nothing(self):
        for day in (WEEKEND, HOLIDAY):
            code, msg = hb.evaluate([], day)
            assert code == hb.OK
            assert "not a trading day" in msg


class TestBuild:
    def test_document_shape_matches_what_the_watchdog_queries(self):
        ts = pd.Timestamp("2026-08-20 09:26:11", tz="America/New_York")
        doc = hb.build("LIVE", True, 0, ts=ts)

        assert doc["date"] == TRADING_DAY, "watchdog queries on this exact field"
        assert doc["ran_at"].startswith("2026-08-20T09:26:11")
        assert doc["mode"] == "LIVE"
        assert doc["in_window"] is True
        assert doc["exit_code"] == 0

    def test_a_built_document_round_trips_through_evaluate(self):
        ts = pd.Timestamp("2026-08-20 09:26:11", tz="America/New_York")
        doc = hb.build("DRY_RUN", True, 0, ts=ts)
        assert hb.evaluate([doc], doc["date"])[0] == hb.OK

    def test_a_halted_run_still_produces_a_heartbeat(self):
        """A run that halted proves the host woke up - a different problem entirely."""
        ts = pd.Timestamp("2026-08-20 09:26:11", tz="America/New_York")
        doc = hb.build("LIVE", False, 4, ts=ts)
        assert doc["exit_code"] == 4 and doc["in_window"] is False
