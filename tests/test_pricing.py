"""Sizing arithmetic: the code that decides how many shares of a real short to sell.

This block had no coverage at all. `tests/test_signals.py` exercises ranking,
allocation and business-day rolling, but never reached the pricing branch because it
sat inside `generate_signals`, behind a live yfinance call and a Mongo query.

These tests were written BEFORE the switch from yfinance to Schwab quotes, against the
extracted-but-unchanged arithmetic, so they pin today's behaviour. If swapping the
price source moves a share count or flips the $1.00 floor at the boundary, that is a
silent change to real position sizes - and it should fail here rather than show up in
a fill.
"""
from __future__ import annotations

import math

import pytest

from split_strategy.broker.quotes import Quote
from split_strategy.signals.generate import Signal, apply_pricing

STOP_LOSS = 0.40
TRADE_PCT = 0.05
ACCOUNT = 10_000.0
NO_GAP_FILTER = float("inf")


def sig(ticker: str = "TEST") -> Signal:
    return Signal(ticker=ticker, company_name=None, filing_date=None,
                  effective_date=None, ratio=None, confidence="high",
                  status="ENTER_NOW", entry_date=None)


def price(s: Signal, current, prior=None, *, account_size=ACCOUNT,
          trade_pct=TRADE_PCT, max_trade_notional=None, stop_loss=STOP_LOSS,
          max_gap_up=NO_GAP_FILTER) -> Signal:
    return apply_pricing(s, current, prior, account_size=account_size,
                         trade_pct=trade_pct,
                         max_trade_notional=max_trade_notional,
                         stop_loss=stop_loss, max_gap_up=max_gap_up)


class TestSizing:
    def test_shares_floor_the_notional(self):
        s = price(sig(), 3.0)
        assert s.notional == 500.0                 # 10_000 * 0.05
        assert s.shares == math.floor(500.0 / 3.0) == 166

    def test_max_trade_notional_caps_after_the_proportional_size(self):
        """The dollar ceiling must bind, not the 5%.

        TRADE_PCT alone is proportional, so the dollar size drifts as equity moves.
        A deliberately tiny validation position must stay tiny.
        """
        s = price(sig(), 2.0, max_trade_notional=50.0)
        assert s.notional == 50.0
        assert s.shares == 25

    def test_max_trade_notional_does_not_raise_a_smaller_size(self):
        s = price(sig(), 2.0, account_size=100.0, max_trade_notional=5_000.0)
        assert s.notional == 5.0                   # 100 * 0.05, not the ceiling

    def test_stop_and_max_loss(self):
        s = price(sig(), 2.0)
        assert s.stop_price == round(2.0 * 1.40, 4)   # a SHORT's stop is above entry
        assert s.max_loss == round(500.0 * 0.40, 2)

    def test_expensive_stock_buys_zero_shares_but_keeps_a_price(self):
        """The 'sizing choice' branch: priced, but the notional cannot buy one share.

        Must stay distinguishable from missing data - see TestMissingPrice.
        """
        s = price(sig(), 45.88, max_trade_notional=10.0)
        assert s.current_price == 45.88
        assert s.shares == 0


class TestMissingPrice:
    """A missing price must never look like a sizing decision.

    `schwab_orders.entry_block_reason` branches on `current_price` being falsy to
    choose between "missing or invalid price" and "buys 0 shares at $X". Leaving
    `shares` as None (not 0) and `current_price` as None is what keeps a data outage
    reported as a data outage.
    """

    @pytest.mark.parametrize("bad", [None, 0.0, -1.0])
    def test_unusable_price_leaves_the_signal_unsized(self, bad):
        s = price(sig(), bad)
        assert s.shares is None
        assert s.notional is None
        assert s.stop_price is None
        # The exact predicate `entry_block_reason` uses to pick the data-problem
        # message. A negative price is truthy in Python, so `not price` alone would
        # not catch it - both sides must test `price and price > 0` or they disagree.
        assert not (s.current_price and s.current_price > 0)

    def test_unusable_price_does_not_consume_exposure_budget(self):
        """allocate_capital skips signals with no shares; this guards that contract."""
        s = price(sig(), None)
        assert not s.shares


class TestDollarFloorBoundary:
    """The $1.00 floor is enforced in schwab_orders, but the sub-$1 side effects here
    must agree with it exactly. A name at $0.999 vs $1.00 is a different decision.
    """

    def test_just_below_a_dollar_is_flagged_unshortable(self):
        s = price(sig(), 0.999)
        assert s.likely_shortable is False
        assert any("$1.00" in n for n in s.notes)

    def test_exactly_a_dollar_is_not_flagged(self):
        s = price(sig(), 1.00)
        assert s.likely_shortable is not False
        assert not any("$1.00" in n for n in s.notes)

    def test_just_above_a_dollar_is_not_flagged(self):
        s = price(sig(), 1.001)
        assert s.likely_shortable is not False

    def test_sub_dollar_names_buy_large_share_counts(self):
        """Why the per-SHARE fee matters: cheap names mean four-digit quantities."""
        s = price(sig(), 0.1009, max_trade_notional=195.0)
        assert s.shares == 1932


class TestEntryPrice:
    """Which price a SHORT is sized from.

    The values below were measured against the live Schwab API on the exact tickers
    this strategy had been dropping. They are not invented shapes.
    """

    def test_a_sized_bid_wins(self):
        """SCNI, the name recovered by this change."""
        q = Quote("SCNI", bid=1.95, ask=2.04, last=1.995, prev_close=2.39,
                  bid_size=500, ask_size=400)
        assert q.entry_price == 1.95
        assert q.entry_price >= 1.00        # clears the floor; this one can trade

    def test_stub_bid_with_no_size_is_ignored(self):
        """WHLRL: a hundredth of a cent bid against an $80 stock, size 0.

        Sizing off 0.0001 would buy five million shares. The floor does not save us
        in general - a $1.50 stub on an $80 name clears it - so the size gate must.
        """
        q = Quote("WHLRL", bid=0.0001, ask=2147.48, last=80.00, prev_close=81.79,
                  bid_size=0, ask_size=100)
        assert q.entry_price == 80.00

    def test_both_sides_unsized_falls_back_to_last(self):
        """GLTK: stale placeholder book, no size on either side."""
        q = Quote("GLTK", bid=0.2526, ask=1.65, last=1.35, prev_close=4.05,
                  bid_size=0, ask_size=0)
        assert q.entry_price == 1.35

    def test_missing_size_field_is_treated_as_unsized(self):
        """Absent size must be conservative, not optimistic."""
        q = Quote("X", bid=0.50, ask=0.55, last=2.00)
        assert q.entry_price == 2.00

    def test_no_bid_and_no_last_is_unpriced(self):
        assert Quote("X", prev_close=5.0).entry_price is None

    def test_unpriced_quote_leaves_the_signal_unsized(self):
        q = Quote("X", prev_close=5.0)
        s = price(sig(), q.entry_price, q.prev_close)
        assert s.shares is None
        assert not (s.current_price and s.current_price > 0)


class TestGapUpFilter:
    def test_disabled_by_default_never_vetoes(self):
        """MAX_GAP_UP_PCT defaults to inf - the walk-forward chose no filter."""
        s = price(sig(), 2.0, 1.0)               # +100%
        assert s.gap_up_pct == 100.0
        assert s.gap_up_ok is True
        assert not any("gapped up" in n for n in s.notes)

    def test_vetoes_when_a_threshold_is_set(self):
        s = price(sig(), 1.5, 1.0, max_gap_up=0.30)
        assert s.gap_up_pct == 50.0
        assert s.gap_up_ok is False
        assert any("gapped up" in n for n in s.notes)

    def test_within_threshold_passes(self):
        s = price(sig(), 1.2, 1.0, max_gap_up=0.30)
        assert s.gap_up_ok is True

    def test_no_prior_close_leaves_the_filter_unevaluated(self):
        """Unknown, not OK. A missing prior close must not read as 'did not gap'."""
        s = price(sig(), 2.0, None)
        assert s.gap_up_pct is None
        assert s.gap_up_ok is None

    def test_gap_down_passes(self):
        s = price(sig(), 0.5, 1.0, max_gap_up=0.30)
        assert s.gap_up_pct == -50.0
        assert s.gap_up_ok is True
