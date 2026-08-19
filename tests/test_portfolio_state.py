"""Tests for the position ledger and broker reconciliation.

The ledger is the only thing standing between "we hold a short" and "we forgot we hold
a short". The old version marked a position closed once its planned exit *date* passed
— but a date passing does not cover anything. These tests pin the rule that only a
confirmed cover releases a position.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from split_strategy.broker.accounts import (
    BrokerPosition,
    reconcile,
    summarize_order,
)
from split_strategy.signals import portfolio_state as ps


def make_pos(ticker="ABC", status=ps.OPEN, exit_date="2026-08-01", notional=200.0, shares=100):
    return dict(
        ticker=ticker, entry_date="2026-07-27", planned_exit_date=exit_date,
        notional=notional, shares=shares, status=status,
        entry_order_id="o1", client_order_id="c1", entry_fill_price=2.0,
        filled_shares=shares, tp_order_id=None, exit_order_id=None,
        exit_fill_price=None, opened_at="2026-07-27T09:30:00", closed_at=None,
    )


class TestPersistence:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "ledger.json"
        ps.save_positions(path, [make_pos()])
        assert len(ps.load_positions(path)) == 1

    def test_missing_file_is_empty(self, tmp_path):
        assert ps.load_positions(tmp_path / "nope.json") == []

    def test_corrupt_ledger_raises_rather_than_returning_empty(self, tmp_path):
        """Silently treating a corrupt ledger as empty would double-commit capital and
        re-short names we already hold."""
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(RuntimeError, match="unreadable"):
            ps.load_positions(path)

    def test_write_is_atomic(self, tmp_path):
        """A crash mid-write must not truncate an existing ledger."""
        path = tmp_path / "ledger.json"
        ps.save_positions(path, [make_pos()])
        ps.save_positions(path, [make_pos("A"), make_pos("B")])
        assert len(json.loads(path.read_text())) == 2
        assert not (tmp_path / "ledger.json.tmp").exists()

    def test_old_records_get_migrated(self, tmp_path):
        """Ledgers written before lifecycle fields existed must still load."""
        path = tmp_path / "old.json"
        path.write_text(json.dumps([
            {"ticker": "ABC", "entry_date": "2026-07-01",
             "planned_exit_date": "2026-08-01", "notional": 500.0}
        ]), encoding="utf-8")
        loaded = ps.load_positions(path)
        assert loaded[0]["status"] == ps.OPEN
        assert loaded[0]["tp_order_id"] is None


class TestCapitalAccounting:
    def test_committed_counts_all_live_states(self):
        positions = [
            make_pos("A", status=ps.PENDING_ENTRY, notional=100.0),
            make_pos("B", status=ps.OPEN, notional=200.0),
            make_pos("C", status=ps.PENDING_EXIT, notional=300.0),
        ]
        assert ps.committed_capital(positions) == pytest.approx(600.0)

    def test_closed_positions_release_capital(self):
        positions = [make_pos("A", status=ps.CLOSED, notional=0.0),
                     make_pos("B", status=ps.OPEN, notional=200.0)]
        assert ps.committed_capital(positions) == pytest.approx(200.0)

    def test_fill_resizes_notional_to_actual(self):
        """Committed capital should reflect what filled, not what we intended."""
        pos = make_pos(notional=200.0, shares=100)
        ps.mark_entry_filled(pos, fill_price=1.50, filled_shares=80)
        assert pos["notional"] == pytest.approx(120.0)
        assert pos["status"] == ps.OPEN

    def test_rejected_entry_releases_capital(self):
        positions = [make_pos(status=ps.PENDING_ENTRY, notional=200.0)]
        ps.mark_entry_rejected(positions[0], positions, reason="no borrow")
        assert ps.committed_capital(positions) == 0.0


class TestDuplicateGuard:
    def test_finds_existing_live_position(self):
        positions = [make_pos("ABC", status=ps.OPEN)]
        assert ps.find_live_position(positions, "ABC") is not None

    def test_is_case_insensitive(self):
        positions = [make_pos("ABC")]
        assert ps.find_live_position(positions, "abc") is not None

    def test_closed_position_does_not_block_reentry(self):
        positions = [make_pos("ABC", status=ps.CLOSED)]
        assert ps.find_live_position(positions, "ABC") is None

    def test_pending_entry_blocks_reentry(self):
        """Submitted-but-unfilled still means don't send another."""
        positions = [make_pos("ABC", status=ps.PENDING_ENTRY)]
        assert ps.find_live_position(positions, "ABC") is not None


class TestExitScheduling:
    AS_OF = pd.Timestamp("2026-08-01")

    def test_open_position_due_today_is_returned(self):
        positions = [make_pos(exit_date="2026-08-01", status=ps.OPEN)]
        assert len(ps.positions_due_for_exit(positions, self.AS_OF)) == 1

    def test_future_exit_is_not_due(self):
        positions = [make_pos(exit_date="2026-09-01", status=ps.OPEN)]
        assert ps.positions_due_for_exit(positions, self.AS_OF) == []

    def test_pending_entry_is_not_exited(self):
        """We don't know we own it yet; covering could open a long."""
        positions = [make_pos(exit_date="2026-08-01", status=ps.PENDING_ENTRY)]
        assert ps.positions_due_for_exit(positions, self.AS_OF) == []

    def test_pending_exit_is_not_resubmitted(self):
        """A working cover must not get a second cover on top of it."""
        positions = [make_pos(exit_date="2026-08-01", status=ps.PENDING_EXIT)]
        assert ps.positions_due_for_exit(positions, self.AS_OF) == []

    def test_overdue_position_is_still_returned(self):
        """A missed exit day must keep trying, not be silently abandoned."""
        positions = [make_pos(exit_date="2026-07-01", status=ps.OPEN)]
        assert len(ps.positions_due_for_exit(positions, self.AS_OF)) == 1

    def test_malformed_exit_date_is_surfaced(self):
        positions = [make_pos(exit_date="Unknown", status=ps.OPEN)]
        assert ps.positions_due_for_exit(positions, self.AS_OF) == []
        assert len(ps.malformed_positions(positions)) == 1


class TestLifecycleTransitions:
    def test_full_round_trip(self):
        positions = []
        pos = ps.add_position(positions, "ABC", "2026-07-27", "2026-08-01",
                              notional=200.0, shares=100)
        assert pos["status"] == ps.PENDING_ENTRY
        assert pos["client_order_id"].startswith("rss-ABC-")

        ps.mark_entry_filled(pos, fill_price=2.0, filled_shares=100)
        assert pos["status"] == ps.OPEN

        ps.attach_take_profit(pos, "tp-1")
        assert pos["tp_order_id"] == "tp-1"

        ps.mark_exit_submitted(pos, "exit-1")
        assert pos["status"] == ps.PENDING_EXIT
        assert ps.committed_capital(positions) == pytest.approx(200.0)

        ps.mark_closed(pos, fill_price=1.6)
        assert pos["status"] == ps.CLOSED
        assert ps.committed_capital(positions) == 0.0

    def test_client_order_id_is_stable_per_ticker_and_day(self):
        """An idempotency key must repeat, or it cannot identify a repeated submit.

        The scheduled task now fires every few minutes across the entry window, so two
        runs on the same day submitting the same ticker have to produce the same key.
        """
        ids = {ps.new_client_order_id("ABC", as_of="2026-08-20") for _ in range(50)}
        assert ids == {"rss-ABC-20260820"}

    def test_client_order_id_differs_by_ticker_and_by_day(self):
        same_day = pd.Timestamp("2026-08-20")
        assert (ps.new_client_order_id("ABC", as_of=same_day)
                != ps.new_client_order_id("XYZ", as_of=same_day))
        assert (ps.new_client_order_id("ABC", as_of="2026-08-20")
                != ps.new_client_order_id("ABC", as_of="2026-08-21"))

    def test_simulate_lifecycle_only_advances_dry_run(self):
        positions = [make_pos("A", status=ps.PENDING_ENTRY, exit_date="2026-09-01"),
                     make_pos("B", status=ps.OPEN, exit_date="2026-07-01")]
        _, filled, closed = ps.simulate_lifecycle(positions, pd.Timestamp("2026-08-01"))
        assert filled == 1
        assert closed == 1
        assert positions[0]["status"] == ps.OPEN
        assert positions[1]["status"] == ps.CLOSED

    def test_archive_pruning_keeps_all_live(self):
        positions = [make_pos(f"C{i}", status=ps.CLOSED) for i in range(300)]
        positions.append(make_pos("LIVE", status=ps.OPEN))
        pruned = ps.prune_archive(positions, keep_closed=10)
        assert len([p for p in pruned if p["status"] == ps.OPEN]) == 1
        assert len(pruned) == 11


class TestReconcile:
    def test_matching_state_has_no_issues(self):
        ledger = [make_pos("ABC", status=ps.OPEN, shares=100)]
        broker = {"ABC": BrokerPosition("ABC", short_shares=100)}
        assert reconcile(ledger, broker) == []

    def test_missing_at_broker_is_flagged(self):
        """Ledger thinks we're short but Schwab shows nothing - do not keep trading."""
        ledger = [make_pos("ABC", status=ps.OPEN)]
        issues = reconcile(ledger, {})
        assert issues and issues[0].kind == "missing_at_broker"

    def test_pending_entry_absent_at_broker_is_normal(self):
        ledger = [make_pos("ABC", status=ps.PENDING_ENTRY)]
        assert reconcile(ledger, {}) == []

    def test_untracked_short_is_flagged(self):
        """A short we have no record of - manual trade, or a submit we wrote off."""
        issues = reconcile([], {"ZZZ": BrokerPosition("ZZZ", short_shares=50)})
        assert issues and issues[0].kind == "untracked_short"

    def test_quantity_mismatch_is_flagged(self):
        ledger = [make_pos("ABC", status=ps.OPEN, shares=100)]
        broker = {"ABC": BrokerPosition("ABC", short_shares=60)}
        issues = reconcile(ledger, broker)
        assert issues and issues[0].kind == "quantity_mismatch"

    def test_long_position_does_not_count_as_our_short(self):
        ledger = []
        broker = {"ABC": BrokerPosition("ABC", long_shares=100)}
        assert reconcile(ledger, broker) == []

    def test_closed_ledger_entries_are_ignored(self):
        ledger = [make_pos("ABC", status=ps.CLOSED)]
        assert reconcile(ledger, {}) == []


class TestSummarizeOrder:
    def test_filled_order_with_execution_legs(self):
        payload = {
            "status": "FILLED",
            "filledQuantity": 100,
            "orderActivityCollection": [
                {"executionLegs": [{"quantity": 60, "price": 1.00},
                                   {"quantity": 40, "price": 1.10}]}
            ],
        }
        out = summarize_order(payload)
        assert out["is_filled"] is True
        assert out["filled_quantity"] == pytest.approx(100)
        assert out["avg_fill_price"] == pytest.approx(1.04)

    def test_working_order(self):
        out = summarize_order({"status": "WORKING", "filledQuantity": 0})
        assert out["is_working"] is True
        assert out["is_filled"] is False

    def test_rejected_order_is_dead(self):
        assert summarize_order({"status": "REJECTED"})["is_dead"] is True

    def test_partial_fill_is_not_filled(self):
        out = summarize_order({"status": "WORKING", "filledQuantity": 40})
        assert out["is_filled"] is False
        assert out["filled_quantity"] == pytest.approx(40)

