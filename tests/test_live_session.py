"""Integration tests for the trading session: reconcile -> exit -> enter.

This covers the behavior that was entirely missing before: closing a position. The
most important test here is `test_failed_cancel_blocks_the_cover` — if the resting
take-profit cannot be cancelled and we send a cover anyway, both can fill and the
account ends up **long** a stock we were trying to be short.
"""
from __future__ import annotations

import pandas as pd
import pytest

from split_strategy.broker.accounts import BrokerPosition
from split_strategy.broker.quotes import Quote
from split_strategy.broker.schwab_orders import OrderManager, OrderMode, Outcome
from split_strategy.live import session as sess
from split_strategy.signals import portfolio_state as ps
from split_strategy.signals.generate import Signal

QUOTE = Quote("ABC", bid=1.00, ask=1.01, last=1.005)
QUOTES = {"ABC": QUOTE, "XYZ": Quote("XYZ", bid=2.00, ask=2.02, last=2.01)}
TODAY = pd.Timestamp("2026-08-01")


def sig(ticker="ABC", status="ENTER_NOW", **kw):
    base = dict(
        ticker=ticker, company_name="T", filing_date="20260720",
        effective_date="2026-08-15", ratio=10.0, confidence="High",
        status=status, entry_date="2026-07-31", shares=100, notional=200.0,
        current_price=1.0, gap_up_ok=True, capital_ok=True,
        likely_shortable=True, schwab_is_shortable=True, schwab_htb_rate=-8.0,
    )
    base.update(kw)
    return Signal(**base)


def open_position(ticker="ABC", exit_date="2026-08-01", tp_order_id="tp-1", shares=100):
    return dict(
        ticker=ticker, entry_date="2026-07-20", planned_exit_date=exit_date,
        notional=200.0, shares=shares, status=ps.OPEN, entry_order_id="e-1",
        client_order_id="c-1", entry_fill_price=2.0, filled_shares=shares,
        tp_order_id=tp_order_id, exit_order_id=None, exit_fill_price=None,
        opened_at="2026-07-20T09:30:00", closed_at=None,
    )


class RecordingManager(OrderManager):
    """Dry-run manager that records the ORDER of operations."""

    def __init__(self, cancel_outcome=Outcome.SUBMITTED, **kw):
        super().__init__(mode=OrderMode.DRY_RUN, **kw)
        self.calls: list[str] = []
        self._cancel_outcome = cancel_outcome

    def cancel(self, order_id, ticker=""):
        self.calls.append(f"cancel:{ticker}")
        return self._record(ticker, "CANCEL", 0, self._cancel_outcome,
                            f"forced {self._cancel_outcome.value}", order_id=order_id)

    def submit_cover(self, ticker, shares, quote):
        self.calls.append(f"cover:{ticker}")
        return super().submit_cover(ticker, shares, quote)

    def submit_entry(self, signal, quote, client_order_id=None):
        self.calls.append(f"entry:{signal.ticker}")
        return super().submit_entry(signal, quote, client_order_id=client_order_id)


class TestExitRoundTrip:
    def test_due_position_is_covered(self):
        positions = [open_position(exit_date="2026-08-01")]
        mgr = RecordingManager()
        report = sess.SessionReport(mode="DRY_RUN")
        sess.process_exits(positions, mgr, QUOTES, report, as_of=TODAY)

        assert mgr.calls == ["cancel:ABC", "cover:ABC"], "must cancel the TP before covering"
        assert report.exits and report.exits[0]["side"] == "BUY_TO_COVER"
        assert positions[0]["status"] == ps.CLOSED

    def test_take_profit_is_cancelled_before_the_cover(self):
        """Order matters: a cover sent while the TP still rests can fill twice."""
        positions = [open_position()]
        mgr = RecordingManager()
        sess.process_exits(positions, mgr, QUOTES, sess.SessionReport(mode="DRY_RUN"),
                           as_of=TODAY)
        assert mgr.calls.index("cancel:ABC") < mgr.calls.index("cover:ABC")

    @pytest.mark.parametrize("bad", [Outcome.REJECTED, Outcome.UNCERTAIN])
    def test_failed_cancel_blocks_the_cover(self, bad):
        """THE critical safety rule. If we cannot pull the resting take-profit, we must
        NOT send a cover - both could fill and flip the position long."""
        positions = [open_position()]
        mgr = RecordingManager(cancel_outcome=bad)
        report = sess.SessionReport(mode="DRY_RUN")
        sess.process_exits(positions, mgr, QUOTES, report, as_of=TODAY)

        assert "cover:ABC" not in mgr.calls
        assert positions[0]["status"] == ps.OPEN, "position must stay open for a retry"
        assert any("double-covering" in n for n in report.notes)

    def test_position_without_take_profit_covers_directly(self):
        positions = [open_position(tp_order_id=None)]
        mgr = RecordingManager()
        sess.process_exits(positions, mgr, QUOTES, sess.SessionReport(mode="DRY_RUN"),
                           as_of=TODAY)
        assert mgr.calls == ["cover:ABC"]

    def test_position_not_yet_due_is_untouched(self):
        positions = [open_position(exit_date="2026-09-01")]
        mgr = RecordingManager()
        sess.process_exits(positions, mgr, QUOTES, sess.SessionReport(mode="DRY_RUN"),
                           as_of=TODAY)
        assert mgr.calls == []
        assert positions[0]["status"] == ps.OPEN

    def test_missing_quote_skips_the_cover_safely(self):
        positions = [open_position()]
        mgr = RecordingManager()
        report = sess.SessionReport(mode="DRY_RUN")
        sess.process_exits(positions, mgr, {}, report, as_of=TODAY)
        assert report.exits[0]["outcome"] == Outcome.SKIPPED.value
        assert positions[0]["status"] == ps.OPEN, "still short; must retry next run"

    def test_unknown_share_count_is_skipped(self):
        pos = open_position()
        pos["filled_shares"] = None
        pos["shares"] = None
        positions = [pos]
        mgr = RecordingManager()
        report = sess.SessionReport(mode="DRY_RUN")
        sess.process_exits(positions, mgr, QUOTES, report, as_of=TODAY)
        assert "cover:ABC" not in mgr.calls
        assert any("share count unknown" in n for n in report.notes)


class TestEntries:
    def test_enter_now_opens_a_position(self):
        positions = []
        mgr = RecordingManager()
        report = sess.SessionReport(mode="DRY_RUN")
        sess.process_entries([sig()], positions, mgr, QUOTES, report, take_profit_pct=0.20)

        assert len(positions) == 1
        assert positions[0]["ticker"] == "ABC"
        # Dry-run simulates the fill so the take-profit leg gets exercised too.
        assert positions[0]["status"] == ps.OPEN

    def test_existing_position_is_not_re_shorted(self):
        """The duplicate-shorting bug: one position per ticker, always."""
        positions = [open_position("ABC", exit_date="2026-09-01")]
        mgr = RecordingManager()
        report = sess.SessionReport(mode="DRY_RUN")
        sess.process_entries([sig("ABC")], positions, mgr, QUOTES, report, take_profit_pct=0.20)

        assert mgr.calls == []
        assert len(positions) == 1
        assert any("not re-shorting" in n for n in report.notes)

    def test_holding_signals_never_enter(self):
        positions = []
        mgr = RecordingManager()
        sess.process_entries([sig(status="HOLDING")], positions, mgr, QUOTES,
                             sess.SessionReport(mode="DRY_RUN"), take_profit_pct=0.20)
        assert mgr.calls == []
        assert positions == []

    def test_blocked_entry_creates_no_position(self):
        positions = []
        mgr = RecordingManager()
        report = sess.SessionReport(mode="DRY_RUN")
        sess.process_entries([sig(schwab_is_shortable=False)], positions, mgr, QUOTES,
                             report, take_profit_pct=0.20)
        assert positions == []
        assert report.entries[0]["outcome"] == Outcome.SKIPPED.value

    def test_take_profit_price_is_twenty_percent_below_fill(self):
        positions = []
        mgr = RecordingManager()
        sess.process_entries([sig()], positions, mgr, QUOTES,
                             sess.SessionReport(mode="DRY_RUN"), take_profit_pct=0.20)
        tp_orders = [r for r in mgr.results if r.side == "BUY_TO_COVER"]
        assert tp_orders
        entry_fill = positions[0]["entry_fill_price"]
        assert tp_orders[0].limit_price == pytest.approx(entry_fill * 0.80, rel=1e-3)


class TestReconciliation:
    def test_dry_run_advances_simulated_lifecycle(self):
        positions = [open_position(exit_date="2026-07-01")]
        positions[0]["status"] = ps.PENDING_ENTRY
        report = sess.SessionReport(mode="DRY_RUN")
        ok = sess.reconcile_and_sync(positions, None, None, OrderMode.DRY_RUN, report)
        assert ok is True

    def test_live_halts_when_positions_unreadable(self, monkeypatch):
        from split_strategy.broker import accounts as acct
        monkeypatch.setattr(acct, "get_broker_positions", lambda c, a: None)
        report = sess.SessionReport(mode="LIVE")
        ok = sess.reconcile_and_sync([], object(), "HASH", OrderMode.LIVE, report)
        assert ok is False and report.halted

    def test_live_halts_on_untracked_short(self, monkeypatch):
        """A short Schwab reports that we have no record of - stop and get a human."""
        from split_strategy.broker import accounts as acct
        monkeypatch.setattr(acct, "get_broker_positions",
                            lambda c, a: {"ZZZ": BrokerPosition("ZZZ", short_shares=10)})
        report = sess.SessionReport(mode="LIVE")
        ok = sess.reconcile_and_sync([], object(), "HASH", OrderMode.LIVE, report)
        assert ok is False
        assert "discrepancy" in report.halt_reason

    def test_live_halts_on_unschedulable_exit_date(self, monkeypatch):
        from split_strategy.broker import accounts as acct
        pos = open_position(exit_date="Unknown")
        monkeypatch.setattr(acct, "get_broker_positions",
                            lambda c, a: {"ABC": BrokerPosition("ABC", short_shares=100)})
        report = sess.SessionReport(mode="LIVE")
        ok = sess.reconcile_and_sync([pos], object(), "HASH", OrderMode.LIVE, report)
        assert ok is False
        assert "unusable exit date" in report.halt_reason

    def test_live_proceeds_when_everything_matches(self, monkeypatch):
        from split_strategy.broker import accounts as acct
        pos = open_position(exit_date="2026-09-01")
        monkeypatch.setattr(acct, "get_broker_positions",
                            lambda c, a: {"ABC": BrokerPosition("ABC", short_shares=100)})
        report = sess.SessionReport(mode="LIVE")
        assert sess.reconcile_and_sync([pos], object(), "HASH", OrderMode.LIVE, report) is True


class TestQuoteCollection:
    def test_collects_entries_and_open_positions(self):
        positions = [open_position("XYZ", exit_date="2026-09-01")]
        tickers = sess.collect_quote_tickers([sig("ABC")], positions)
        assert tickers == ["ABC", "XYZ"]

    def test_ignores_non_actionable_signals(self):
        assert sess.collect_quote_tickers([sig("ABC", status="UPCOMING")], []) == []
