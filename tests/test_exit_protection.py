"""Tests for how a live short gets out: the early cover, the stop, and booking fills.

Each of these was a way for the first real position to go wrong unattended: covering
on the split day (the ledger no longer matches the broker), holding with no stop, and
a take-profit that fills and leaves the ledger claiming a position Schwab does not have.
"""
from __future__ import annotations

import pandas as pd
import pytest

from split_strategy.broker import accounts as acct
from split_strategy.broker.accounts import BrokerPosition, summarize_resting_exit
from split_strategy.broker.schwab_orders import (OrderManager, OrderMode, Outcome,
                                                 build_exit_bracket_order)
from split_strategy.live import calendar as mcal
from split_strategy.live import session as sess
from split_strategy.signals import portfolio_state as ps


def open_position(ticker="ABC", exit_date="2026-10-13", tp_order_id=None, shares=40):
    return dict(
        ticker=ticker, entry_date="2026-10-05", planned_exit_date=exit_date,
        notional=50.0, shares=shares, status=ps.OPEN, entry_order_id="e-1",
        client_order_id="c-1", entry_fill_price=1.25, filled_shares=shares,
        tp_order_id=tp_order_id, exit_order_id=None, exit_fill_price=None,
        opened_at="2026-10-05T09:30:00", closed_at=None,
    )


class FakeResponse:
    def __init__(self, status_code=201, headers=None, text=""):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text


class FakeClient:
    """Answers place_order with a queue of responses, one per call."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.placed = []

    def place_order(self, account_hash, order):
        self.placed.append(order.build())
        return self.responses.pop(0)


class TestPrevTradingDay:
    def test_midweek(self):
        assert mcal.prev_trading_day("2026-10-08") == pd.Timestamp("2026-10-07")

    def test_monday_goes_back_to_friday(self):
        assert mcal.prev_trading_day("2026-10-12") == pd.Timestamp("2026-10-09")

    def test_skips_a_holiday(self):
        # 2026-09-07 is Labor Day.
        assert mcal.prev_trading_day("2026-09-08") == pd.Timestamp("2026-09-04")


class TestCoverDate:
    def test_cover_is_the_session_before_the_split(self):
        assert ps.cover_date("2026-10-13") == pd.Timestamp("2026-10-12")

    def test_monday_split_covers_on_friday(self):
        assert ps.cover_date("2026-10-12") == pd.Timestamp("2026-10-09")

    def test_zero_sessions_restores_the_split_day_exit(self):
        assert ps.cover_date("2026-10-13", sessions_before=0) == pd.Timestamp("2026-10-13")

    def test_unusable_date_raises(self):
        with pytest.raises(Exception):
            ps.cover_date("Unknown")

    def test_position_is_due_the_day_before_its_split(self):
        positions = [open_position(exit_date="2026-10-13")]
        assert ps.positions_due_for_exit(positions, pd.Timestamp("2026-10-09")) == []
        assert len(ps.positions_due_for_exit(positions, pd.Timestamp("2026-10-12"))) == 1


class TestExitBracketOrder:
    def test_bracket_is_one_cancels_other_with_both_legs(self):
        spec = build_exit_bracket_order("ABC", 40, 1.00, 1.75).build()
        assert spec["orderStrategyType"] == "OCO"
        tp, stop = spec["childOrderStrategies"]
        assert (tp["orderType"], tp["price"], tp["duration"]) == ("LIMIT", "1.00", "GOOD_TILL_CANCEL")
        assert (stop["orderType"], stop["stopPrice"], stop["duration"]) == ("STOP", "1.75", "GOOD_TILL_CANCEL")
        for leg in (tp, stop):
            assert leg["orderLegCollection"][0]["instruction"] == "BUY_TO_COVER"
            assert leg["orderLegCollection"][0]["quantity"] == 40

    def test_prices_are_20_below_and_40_above_the_fill(self):
        mgr = OrderManager(mode=OrderMode.DRY_RUN)
        result = mgr.submit_exit_bracket("ABC", 40, entry_fill=1.25,
                                         take_profit_pct=0.20, stop_loss_pct=0.40)
        assert result.outcome == Outcome.WOULD_PLACE.value
        assert result.limit_price == pytest.approx(1.00)
        assert "stop @ 1.75" in result.detail
        assert result.order_type == "OCO"

    def test_live_submit_returns_the_parent_order_id(self):
        client = FakeClient(FakeResponse(201, {"Location": "https://api/orders/777"}))
        mgr = OrderManager(mode=OrderMode.LIVE, client=client, account_hash="HASH")
        result = mgr.submit_exit_bracket("ABC", 40, 1.25, 0.20, 0.40)
        assert result.outcome == Outcome.SUBMITTED.value and result.order_id == "777"
        assert client.placed[0]["orderStrategyType"] == "OCO"


class TestAttachProtection:
    def _attach(self, client, stop=0.40):
        positions = [open_position()]
        mgr = OrderManager(mode=OrderMode.LIVE, client=client, account_hash="HASH")
        report = sess.SessionReport(mode="LIVE")
        sess.attach_take_profits(positions, mgr, report, 0.20, stop)
        return positions[0], report

    def test_open_position_gets_the_bracket(self):
        client = FakeClient(FakeResponse(201, {"Location": "https://api/orders/777"}))
        pos, report = self._attach(client)
        assert pos["tp_order_id"] == "777"
        assert len(client.placed) == 1 and client.placed[0]["orderStrategyType"] == "OCO"

    def test_refused_bracket_falls_back_to_take_profit_and_says_so(self):
        client = FakeClient(FakeResponse(400, text="OCO not allowed"),
                            FakeResponse(201, {"Location": "https://api/orders/888"}))
        pos, report = self._attach(client)
        assert pos["tp_order_id"] == "888"
        assert client.placed[1]["orderType"] == "LIMIT"
        assert any("NO STOP" in n for n in report.notes)

    def test_uncertain_bracket_places_nothing_else(self):
        class Raising(FakeClient):
            def place_order(self, account_hash, order):
                self.placed.append(order.build())
                raise TimeoutError("read timed out")

        client = Raising()
        pos, report = self._attach(client)
        assert len(client.placed) == 1, "a second cover beside a maybe-resting one could double-fill"
        assert pos["tp_order_id"] is None
        assert any("uncertain" in n for n in report.notes)

    def test_no_stop_pct_keeps_the_plain_take_profit(self):
        client = FakeClient(FakeResponse(201, {"Location": "https://api/orders/999"}))
        pos, _ = self._attach(client, stop=None)
        assert client.placed[0]["orderType"] == "LIMIT" and pos["tp_order_id"] == "999"


def _leg(order_type, status, price=None, filled=0):
    leg = {"orderType": order_type, "status": status, "filledQuantity": filled}
    if price is not None:
        leg["orderActivityCollection"] = [
            {"executionLegs": [{"quantity": filled, "price": price}]}]
    return leg


class TestRestingExitStatus:
    def test_take_profit_leg_filled(self):
        info = summarize_resting_exit({"orderStrategyType": "OCO", "childOrderStrategies": [
            _leg("LIMIT", "FILLED", 1.00, 40), _leg("STOP", "CANCELED")]})
        assert info["is_filled"] and info["kind"] == "take_profit"
        assert info["avg_fill_price"] == pytest.approx(1.00)

    def test_stop_leg_filled(self):
        info = summarize_resting_exit({"childOrderStrategies": [
            _leg("LIMIT", "CANCELED"), _leg("STOP", "FILLED", 1.80, 40)]})
        assert info["is_filled"] and info["kind"] == "stop_loss"

    def test_both_legs_working(self):
        info = summarize_resting_exit({"childOrderStrategies": [
            _leg("LIMIT", "WORKING"), _leg("STOP", "WORKING")]})
        assert not info["is_filled"] and not info["is_dead"]

    def test_both_legs_cancelled_is_dead(self):
        info = summarize_resting_exit({"childOrderStrategies": [
            _leg("LIMIT", "CANCELED"), _leg("STOP", "CANCELED")]})
        assert info["is_dead"] and not info["is_filled"]

    def test_plain_take_profit_without_children(self):
        info = summarize_resting_exit(_leg("LIMIT", "FILLED", 1.00, 40))
        assert info["is_filled"] and info["kind"] == "take_profit"


class TestFilledExitIsBooked:
    def _reconcile(self, monkeypatch, pos, exit_info, broker):
        monkeypatch.setattr(acct, "get_broker_positions", lambda c, a: broker)
        monkeypatch.setattr(acct, "get_resting_exit_status", lambda c, a, oid: exit_info)
        report = sess.SessionReport(mode="LIVE")
        ok = sess.reconcile_and_sync([pos], object(), "HASH", OrderMode.LIVE, report)
        return ok, report

    def test_filled_take_profit_closes_the_position_instead_of_halting(self, monkeypatch):
        pos = open_position(tp_order_id="777")
        ok, report = self._reconcile(
            monkeypatch, pos,
            {"is_filled": True, "is_dead": False, "kind": "take_profit", "avg_fill_price": 1.0},
            broker={})  # Schwab is flat: the take-profit covered it
        assert ok is True and not report.halted
        assert pos["status"] == ps.CLOSED and pos["close_reason"] == "take_profit"
        assert pos["exit_fill_price"] == 1.0

    def test_filled_stop_is_booked_as_a_stop(self, monkeypatch):
        pos = open_position(tp_order_id="777")
        ok, _ = self._reconcile(
            monkeypatch, pos,
            {"is_filled": True, "is_dead": False, "kind": "stop_loss", "avg_fill_price": 1.8},
            broker={})
        assert ok is True and pos["close_reason"] == "stop_loss"

    def test_cancelled_exit_is_forgotten_so_it_gets_replaced(self, monkeypatch):
        pos = open_position(tp_order_id="777")
        ok, report = self._reconcile(
            monkeypatch, pos,
            {"is_filled": False, "is_dead": True, "kind": None, "avg_fill_price": None},
            broker={"ABC": BrokerPosition("ABC", short_shares=40)})
        assert ok is True and pos["status"] == ps.OPEN and pos["tp_order_id"] is None

    def test_working_exit_changes_nothing(self, monkeypatch):
        pos = open_position(tp_order_id="777")
        ok, _ = self._reconcile(
            monkeypatch, pos,
            {"is_filled": False, "is_dead": False, "kind": None, "avg_fill_price": None},
            broker={"ABC": BrokerPosition("ABC", short_shares=40)})
        assert ok is True and pos["status"] == ps.OPEN and pos["tp_order_id"] == "777"
