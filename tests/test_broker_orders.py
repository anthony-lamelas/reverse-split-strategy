"""Tests for order gating, pricing, and the exit round trip.

Every check in `entry_block_reason` corresponds to a way the previous implementation
could have placed a bad order. These tests are the guarantee those vetoes stay wired
to something.
"""
from __future__ import annotations

import pytest

from split_strategy.broker.quotes import (
    Quote,
    marketable_limit_price,
    round_to_tick,
    spread_too_wide,
)
from split_strategy.broker.schwab_orders import (
    OrderManager,
    OrderMode,
    Outcome,
    RiskLimits,
    extract_order_id,
)
from split_strategy.signals.generate import Signal

TIGHT = Quote("ABC", bid=1.00, ask=1.01, last=1.005)      # ~1% spread
WIDE = Quote("PMI", bid=0.05, ask=0.09, last=0.07)        # ~57% spread
UNQUOTED = Quote("XXX", bid=None, ask=None, last=None)


def sig(**kw):
    base = dict(
        ticker="ABC", company_name="Test", filing_date="20260720",
        effective_date="2026-08-01", ratio=10.0, confidence="High",
        status="ENTER_NOW", entry_date="2026-07-27", shares=100, notional=200.0,
        current_price=1.0, gap_up_ok=True, capital_ok=True,
        likely_shortable=True, schwab_is_shortable=True, schwab_htb_rate=-8.0,
    )
    base.update(kw)
    return Signal(**base)


class TestQuotePricing:
    def test_sell_short_limit_sits_below_bid(self):
        """Marketable: crosses to fill, but floors how low we'll sell."""
        price = marketable_limit_price(TIGHT, "SELL_SHORT", buffer_pct=0.02)
        assert price < TIGHT.bid

    def test_cover_limit_sits_above_ask(self):
        price = marketable_limit_price(TIGHT, "BUY_TO_COVER", buffer_pct=0.02)
        assert price > TIGHT.ask

    def test_falls_back_to_last_when_one_sided(self):
        q = Quote("ABC", bid=None, ask=None, last=2.00)
        assert marketable_limit_price(q, "SELL_SHORT") is not None

    def test_no_price_available_returns_none(self):
        assert marketable_limit_price(UNQUOTED, "SELL_SHORT") is None

    @pytest.mark.parametrize("price,side,expected", [
        (1.234, "SELL", 1.23),      # sells round down (stay marketable)
        (1.234, "BUY", 1.24),       # buys round up
        (0.08765, "SELL", 0.0876),  # sub-$1 uses 0.0001 ticks
        (0.08765, "BUY", 0.0877),
    ])
    def test_tick_rounding(self, price, side, expected):
        assert round_to_tick(price, side) == pytest.approx(expected)

    def test_spread_pct(self):
        assert TIGHT.spread_pct == pytest.approx(0.01 / 1.005, rel=1e-3)

    def test_crossed_book_is_untradeable(self):
        crossed = Quote("ABC", bid=1.10, ask=1.00)
        assert crossed.spread_pct is None
        assert spread_too_wide(crossed, 0.99) is True

    def test_unquotable_is_treated_as_too_wide(self):
        """We cannot bound the fill, so we must not trade it."""
        assert spread_too_wide(UNQUOTED, 0.99) is True


class TestEntryGating:
    def setup_method(self):
        self.mgr = OrderManager(mode=OrderMode.DRY_RUN)

    def test_clean_signal_passes(self):
        assert self.mgr.entry_block_reason(sig(), TIGHT) is None

    def test_holding_status_is_blocked(self):
        """The duplicate-shorting bug: HOLDING persisted for days and re-fired daily."""
        reason = self.mgr.entry_block_reason(sig(status="HOLDING"), TIGHT)
        assert "only ENTER_NOW" in reason

    def test_upcoming_status_is_blocked(self):
        assert self.mgr.entry_block_reason(sig(status="UPCOMING"), TIGHT) is not None

    def test_schwab_says_not_shortable_is_blocked(self):
        reason = self.mgr.entry_block_reason(sig(schwab_is_shortable=False), TIGHT)
        assert "not shortable" in reason

    def test_unconfirmed_and_proxy_negative_is_blocked(self):
        reason = self.mgr.entry_block_reason(
            sig(schwab_is_shortable=None, likely_shortable=False), TIGHT)
        assert "proxy says unshortable" in reason

    def test_schwab_confirmation_overrides_negative_proxy(self):
        """Live data beats the proxy - it was wrong on 3 of 5 real checks."""
        assert self.mgr.entry_block_reason(
            sig(schwab_is_shortable=True, likely_shortable=False), TIGHT) is None

    def test_excessive_borrow_rate_is_blocked(self):
        reason = self.mgr.entry_block_reason(sig(schwab_htb_rate=-250.0), TIGHT)
        assert "borrow cost" in reason

    def test_wide_spread_is_blocked(self):
        reason = self.mgr.entry_block_reason(sig(), WIDE)
        assert "spread" in reason

    def test_missing_quote_is_blocked(self):
        assert "no live quote" in self.mgr.entry_block_reason(sig(), None)

    def test_gap_up_rejection_is_blocked(self):
        assert "gap-up" in self.mgr.entry_block_reason(sig(gap_up_ok=False, gap_up_pct=44.0), TIGHT)

    def test_capital_constrained_is_blocked(self):
        assert "capital constrained" in self.mgr.entry_block_reason(sig(capital_ok=False), TIGHT)

    def test_zero_shares_is_blocked(self):
        assert self.mgr.entry_block_reason(sig(shares=0), TIGHT) is not None

    def test_no_price_reads_as_a_data_problem(self):
        reason = self.mgr.entry_block_reason(sig(shares=0, current_price=None), TIGHT)
        assert "missing or invalid price" in reason

    def test_notional_too_small_says_so_instead_of_blaming_the_data(self):
        """A sizing choice and missing data used to produce the same message.

        At a small notional every stock priced above it buys zero shares and is
        skipped - silently truncating the tradeable universe to cheap names while the
        log claimed the price was missing. Sizing skips must be self-explanatory.
        """
        reason = self.mgr.entry_block_reason(
            sig(shares=0, current_price=45.88, notional=10.0), TIGHT)

        assert "buys 0 shares" in reason
        assert "45.88" in reason
        assert "missing or invalid price" not in reason


class TestDailyLimits:
    def test_daily_short_count_is_enforced(self):
        mgr = OrderManager(mode=OrderMode.DRY_RUN,
                           limits=RiskLimits(max_new_shorts_per_day=2,
                                             max_daily_notional=1e9))
        for i in range(3):
            mgr.submit_entry(sig(ticker=f"T{i}"), TIGHT)
        outcomes = [r.outcome for r in mgr.results]
        assert outcomes[:2] == [Outcome.WOULD_PLACE.value] * 2
        assert outcomes[2] == Outcome.SKIPPED.value
        assert "daily new-short limit" in mgr.results[2].detail

    def test_daily_notional_cap_is_enforced(self):
        mgr = OrderManager(mode=OrderMode.DRY_RUN,
                           limits=RiskLimits(max_new_shorts_per_day=99,
                                             max_daily_notional=500.0))
        for i in range(4):
            mgr.submit_entry(sig(ticker=f"T{i}", notional=200.0), TIGHT)
        skipped = [r for r in mgr.results if r.outcome == Outcome.SKIPPED.value]
        assert skipped and "notional cap" in skipped[0].detail


class TestDryRunNeverSubmits:
    def test_entry_does_not_call_place_order(self):
        class Boom:
            def place_order(self, *a, **k):
                raise AssertionError("DRY_RUN must never place an order")

        mgr = OrderManager(mode=OrderMode.DRY_RUN, client=Boom(), account_hash="X")
        result = mgr.submit_entry(sig(), TIGHT)
        assert result.outcome == Outcome.WOULD_PLACE.value
        assert result.limit_price is not None

    def test_cover_and_cancel_do_not_submit(self):
        class Boom:
            def place_order(self, *a, **k):
                raise AssertionError("no")

            def cancel_order(self, *a, **k):
                raise AssertionError("no")

        mgr = OrderManager(mode=OrderMode.DRY_RUN, client=Boom(), account_hash="X")
        assert mgr.submit_cover("ABC", 100, TIGHT).outcome == Outcome.WOULD_PLACE.value
        assert mgr.cancel("order-1", "ABC").outcome == Outcome.WOULD_PLACE.value


class FakeResponse:
    def __init__(self, status_code=201, headers=None, text=""):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text


class FakeClient:
    def __init__(self, response=None, raises=None):
        self.response = response or FakeResponse()
        self.raises = raises
        self.placed = []
        self.cancelled = []

    def place_order(self, account_hash, order):
        if self.raises:
            raise self.raises
        self.placed.append(order)
        return self.response

    def cancel_order(self, order_id, account_hash):
        if self.raises:
            raise self.raises
        self.cancelled.append(order_id)
        return self.response


class TestLiveSubmission:
    def test_successful_submit_extracts_order_id(self):
        client = FakeClient(FakeResponse(201, {"Location": "https://api/orders/12345"}))
        mgr = OrderManager(mode=OrderMode.LIVE, client=client, account_hash="HASH")
        result = mgr.submit_entry(sig(), TIGHT)
        assert result.outcome == Outcome.SUBMITTED.value
        assert result.order_id == "12345"
        assert len(client.placed) == 1

    def test_broker_rejection_is_recorded_as_rejected(self):
        client = FakeClient(FakeResponse(400, text="not shortable"))
        mgr = OrderManager(mode=OrderMode.LIVE, client=client, account_hash="HASH")
        result = mgr.submit_entry(sig(), TIGHT)
        assert result.outcome == Outcome.REJECTED.value

    def test_network_failure_is_uncertain_not_rejected(self):
        """An order that may have reached Schwab must not be written off as dead -
        that is how an untracked live short gets created."""
        client = FakeClient(raises=TimeoutError("connection reset"))
        mgr = OrderManager(mode=OrderMode.LIVE, client=client, account_hash="HASH")
        result = mgr.submit_entry(sig(), TIGHT)
        assert result.outcome == Outcome.UNCERTAIN.value
        assert "reconcile" in result.detail

    def test_missing_client_is_rejected_not_crashed(self):
        mgr = OrderManager(mode=OrderMode.LIVE, client=None, account_hash=None)
        assert mgr.submit_entry(sig(), TIGHT).outcome == Outcome.REJECTED.value

    def test_cancel_of_already_filled_order_is_not_an_error(self):
        client = FakeClient(FakeResponse(400, text="order already filled"))
        mgr = OrderManager(mode=OrderMode.LIVE, client=client, account_hash="HASH")
        result = mgr.cancel("order-1", "ABC")
        assert result.outcome == Outcome.SUBMITTED.value
        assert "already gone" in result.detail

    def test_cancel_network_failure_is_uncertain(self):
        client = FakeClient(raises=TimeoutError("boom"))
        mgr = OrderManager(mode=OrderMode.LIVE, client=client, account_hash="HASH")
        assert mgr.cancel("order-1", "ABC").outcome == Outcome.UNCERTAIN.value

    def test_extract_order_id_handles_missing_header(self):
        assert extract_order_id(FakeResponse(201, {})) is None


class TestTakeProfit:
    def test_price_is_entry_minus_target(self):
        mgr = OrderManager(mode=OrderMode.DRY_RUN)
        result = mgr.submit_take_profit("ABC", 100, entry_fill=1.00, take_profit_pct=0.20)
        assert result.limit_price == pytest.approx(0.80)

    def test_uses_actual_fill_not_intended_price(self):
        """Sizing the target off the real fill keeps the 20% honest when the entry
        slipped."""
        mgr = OrderManager(mode=OrderMode.DRY_RUN)
        result = mgr.submit_take_profit("ABC", 100, entry_fill=0.90, take_profit_pct=0.20)
        assert result.limit_price == pytest.approx(0.72)


class TestMarginVeto:
    """Margin, not capital, is what caps concurrency on sub-$5 names.

    FINRA 4210(c) floors a short's requirement at $2.50/share below $5, so a
    cheap stock consumes margin far out of proportion to its notional. Without
    this veto the system sizes on notional and finds out from a broker rejection
    - or worse, from a margin call on the position after it.
    """

    def _mgr(self, budget, committed=0.0, house=1.0):
        return OrderManager(
            mode=OrderMode.DRY_RUN,
            limits=RiskLimits(margin_budget=budget, house_margin_multiple=house,
                              max_new_shorts_per_day=99, max_daily_notional=1e9),
            margin_committed=committed,
        )

    def test_no_budget_configured_means_no_margin_check(self):
        """Backwards compatible: margin_budget=None leaves behaviour unchanged."""
        mgr = self._mgr(budget=None)
        assert mgr.entry_block_reason(sig(shares=100), TIGHT) is None

    def test_a_cheap_short_is_blocked_when_it_exceeds_the_budget(self):
        # 100 shares at $1.00 -> $2.50 floor -> $250 required.
        mgr = self._mgr(budget=100.0)
        reason = mgr.entry_block_reason(sig(shares=100), TIGHT)
        assert reason is not None and reason.startswith("margin:")
        assert "$250 margin" in reason

    def test_the_same_short_passes_with_enough_budget(self):
        assert self._mgr(budget=1000.0).entry_block_reason(sig(shares=100), TIGHT) is None

    def test_already_committed_margin_counts_against_the_budget(self):
        """Open positions consume the budget before today's signals see it."""
        assert self._mgr(budget=1000.0, committed=900.0)\
            .entry_block_reason(sig(shares=100), TIGHT) is not None

    def test_house_multiple_tightens_the_veto(self):
        assert self._mgr(budget=300.0).entry_block_reason(sig(shares=100), TIGHT) is None
        assert self._mgr(budget=300.0, house=1.5)\
            .entry_block_reason(sig(shares=100), TIGHT) is not None

    def test_submitting_consumes_the_budget_for_the_next_signal(self):
        """Two identical shorts, budget for one - the second must be refused."""
        mgr = self._mgr(budget=300.0)
        first = mgr.submit_entry(sig(ticker="ABC", shares=100), TIGHT)
        assert first.outcome == Outcome.WOULD_PLACE.value
        assert mgr.margin_committed == pytest.approx(250.0)

        second = mgr.entry_block_reason(sig(ticker="XYZ", shares=100), TIGHT)
        assert second is not None and second.startswith("margin:")

    def test_the_reason_explains_the_ratio_not_just_the_refusal(self):
        reason = self._mgr(budget=10.0).entry_block_reason(sig(shares=100), TIGHT)
        assert "x)" in reason and "left" in reason
