"""Tests for live signal ranking and capital allocation.

These two functions decide which real orders get placed and how much money each one
consumes, so they're the highest-consequence pure logic in the live path.
"""
from __future__ import annotations

import pandas as pd
import pytest

from split_strategy.signals.generate import (
    Signal,
    _next_business_day,
    allocate_capital,
    rank_signals,
)


def sig(ticker="ABC", status="ENTER_NOW", confidence="High", effective_date="2026-08-01",
        shares=100, notional=200.0, gap_up_ok=True):
    return Signal(
        ticker=ticker, company_name="Test Co", filing_date="20260720",
        effective_date=effective_date, ratio=10.0, confidence=confidence,
        status=status, entry_date="2026-07-27", shares=shares, notional=notional,
        current_price=2.0, gap_up_ok=gap_up_ok,
    )


class TestNextBusinessDay:
    def test_weekday_rolls_to_next_day(self):
        assert _next_business_day(pd.Timestamp("2026-07-27")) == pd.Timestamp("2026-07-28")

    def test_friday_rolls_to_monday(self):
        assert _next_business_day(pd.Timestamp("2026-07-24")) == pd.Timestamp("2026-07-27")

    def test_saturday_rolls_to_monday(self):
        assert _next_business_day(pd.Timestamp("2026-07-25")) == pd.Timestamp("2026-07-27")


class TestRankSignals:
    def test_enter_now_outranks_holding_and_upcoming(self):
        signals = [sig("C", status="UPCOMING"), sig("B", status="HOLDING"),
                   sig("A", status="ENTER_NOW")]
        assert [s.ticker for s in rank_signals(signals)] == ["A", "B", "C"]

    def test_higher_confidence_wins_within_status(self):
        signals = [sig("LOW", confidence="Low"), sig("HIGH", confidence="High"),
                   sig("MED", confidence="Medium")]
        assert [s.ticker for s in rank_signals(signals)] == ["HIGH", "MED", "LOW"]

    def test_soonest_execution_breaks_ties(self):
        signals = [sig("LATE", effective_date="2026-09-01"),
                   sig("SOON", effective_date="2026-08-01")]
        assert [s.ticker for s in rank_signals(signals)] == ["SOON", "LATE"]

    def test_missing_effective_date_sorts_last(self):
        signals = [sig("NODATE", effective_date=None), sig("DATED", effective_date="2026-08-01")]
        assert [s.ticker for s in rank_signals(signals)] == ["DATED", "NODATE"]


class TestAllocateCapital:
    def test_all_fit_under_cap(self):
        signals = [sig(f"T{i}", notional=200.0) for i in range(3)]
        committed = allocate_capital(signals, account_size=10_000, max_exposure=1.0)
        assert all(s.capital_ok for s in signals)
        assert committed == pytest.approx(600.0)

    def test_stops_at_the_cap(self):
        """Cap of $500 with $200 bets funds exactly two."""
        signals = [sig(f"T{i}", notional=200.0) for i in range(4)]
        allocate_capital(signals, account_size=1_000, max_exposure=0.5)
        assert [bool(s.capital_ok) for s in signals] == [True, True, False, False]

    def test_existing_committed_reduces_budget(self):
        """Capital already tied up in open positions must count against the cap."""
        signals = [sig(f"T{i}", notional=200.0) for i in range(3)]
        allocate_capital(signals, account_size=10_000, max_exposure=0.05,
                         existing_committed=300.0)
        # Cap is $500; $300 already committed leaves room for exactly one $200 bet.
        assert [bool(s.capital_ok) for s in signals] == [True, False, False]

    def test_fully_committed_account_rejects_all(self):
        signals = [sig("T1", notional=200.0)]
        allocate_capital(signals, account_size=10_000, max_exposure=0.05,
                         existing_committed=500.0)
        assert not bool(signals[0].capital_ok)

    def test_skipped_signal_does_not_consume_budget(self):
        """A gap-up-rejected signal must not crowd out a tradeable one."""
        rejected = sig("REJECT", gap_up_ok=False, notional=200.0)
        good = sig("GOOD", notional=200.0)
        allocate_capital([rejected, good], account_size=1_000, max_exposure=0.2)
        assert rejected.capital_ok is None, "filtered signal should not be allocated"
        assert bool(good.capital_ok)

    def test_zero_share_signal_does_not_consume_budget(self):
        no_shares = sig("NOSHARES", shares=0, notional=200.0)
        good = sig("GOOD", notional=200.0)
        allocate_capital([no_shares, good], account_size=1_000, max_exposure=0.2)
        assert no_shares.capital_ok is None
        assert bool(good.capital_ok)

    def test_upcoming_signals_are_not_allocated(self):
        upcoming = sig("LATER", status="UPCOMING")
        allocate_capital([upcoming], account_size=10_000, max_exposure=1.0)
        assert upcoming.capital_ok is None

    def test_rejected_signal_gets_an_explanatory_note(self):
        signals = [sig("FIRST", notional=200.0), sig("SECOND", notional=200.0)]
        allocate_capital(signals, account_size=1_000, max_exposure=0.2)
        assert any("capital constrained" in n for n in signals[1].notes)

    def test_ranking_then_allocation_favors_enter_now(self):
        """The two functions compose: scarce capital goes to actionable signals."""
        holding = sig("HOLD", status="HOLDING", notional=200.0)
        enter = sig("ENTER", status="ENTER_NOW", notional=200.0)
        signals = [holding, enter]
        rank_signals(signals)
        allocate_capital(signals, account_size=1_000, max_exposure=0.2)
        assert bool(enter.capital_ok)
        assert not bool(holding.capital_ok)

    def test_no_exposure_budget_rejects_everything(self):
        signals = [sig("T1", notional=200.0)]
        allocate_capital(signals, account_size=10_000, max_exposure=0.0)
        assert not bool(signals[0].capital_ok)
