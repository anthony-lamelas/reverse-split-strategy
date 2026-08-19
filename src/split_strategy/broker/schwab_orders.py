"""Order construction and submission for the reverse-split short strategy.

Covers the full round trip, which the previous version did not: this module can open a
short, attach a resting take-profit, cancel it, and cover. Previously the only
`place_order` call in the repo was `equity_sell_short_market` — the system opened
positions and had no way to close them.

Design rules enforced here:
- Every order is a **marketable limit**, never a market order (see broker/quotes.py).
- Only `ENTER_NOW` opens a position; acting on a still-actionable `HOLDING` signal is
  how the previous version stacked a fresh short on the same ticker every day.
- Shortability and borrow cost are *vetoes*, not annotations.
- DRY_RUN builds the real order payload and never calls `place_order`.
- A submit that raises is reported as UNCERTAIN, not REJECTED: the order may have
  reached Schwab, so it must be reconciled rather than assumed dead.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Optional

from .. import borrow as brw
from .. import margin as mgn
from .quotes import Quote, marketable_limit_price, spread_too_wide


class OrderMode(str, Enum):
    DRY_RUN = "DRY_RUN"
    LIVE = "LIVE"


class Outcome(str, Enum):
    WOULD_PLACE = "WOULD_PLACE"     # dry-run only
    SUBMITTED = "SUBMITTED"
    REJECTED = "REJECTED"           # broker said no
    SKIPPED = "SKIPPED"             # we chose not to send it
    UNCERTAIN = "UNCERTAIN"         # may or may not have reached the broker


@dataclass
class OrderResult:
    ticker: str
    side: str
    quantity: int
    order_type: str
    mode: str
    outcome: str
    detail: str = ""
    order_id: Optional[str] = None
    limit_price: Optional[float] = None
    client_order_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RiskLimits:
    """Circuit breakers. Exceeding one stops the order, it does not shrink it."""
    max_new_shorts_per_day: int = 5
    max_daily_notional: float = 5_000.0
    #: Refuse entries below this price. The sub-$1 bucket has no measured edge
    #: (t=0.46 over 29 trades) and a ~5.9x margin multiple. None = no floor.
    min_entry_price: Optional[float] = None
    #: Total short maintenance margin the account may carry (None = unchecked).
    #: Binds far harder than notional exposure on sub-$5 names - see margin.py.
    margin_budget: Optional[float] = None
    #: Broker house requirement as a multiple of the FINRA 4210(c) floor.
    house_margin_multiple: float = 1.0
    max_htb_rate: float = 100.0        # annualized borrow % ceiling
    #: Cap on EXPECTED borrow cost as a fraction of notional (rate x days/365).
    #: The rate ceiling alone ignores holding period: 100%/yr costs 1.4% over 5
    #: days and 41% over 150. None = only the rate ceiling applies.
    max_borrow_cost_pct: Optional[float] = None
    max_spread_pct: float = 0.05
    limit_buffer_pct: float = 0.02


def build_entry_order(ticker: str, shares: int, limit_price: float):
    """A marketable-limit SELL_SHORT."""
    from schwab.orders.equities import equity_sell_short_limit

    return equity_sell_short_limit(ticker, int(shares), _fmt(limit_price))


def build_cover_order(ticker: str, shares: int, limit_price: float):
    """A marketable-limit BUY_TO_COVER used for the time-based exit."""
    from schwab.orders.equities import equity_buy_to_cover_limit

    return equity_buy_to_cover_limit(ticker, int(shares), _fmt(limit_price))


def build_take_profit_order(ticker: str, shares: int, limit_price: float):
    """A resting GTC BUY_TO_COVER limit implementing Strategy B's 20% take-profit.

    Placing this right after the entry fills means the broker enforces the profit
    target — the bot can be offline and it still works, with no intraday polling.
    """
    from schwab.orders.common import Duration, Session
    from schwab.orders.equities import equity_buy_to_cover_limit

    order = equity_buy_to_cover_limit(ticker, int(shares), _fmt(limit_price))
    order.set_duration(Duration.GOOD_TILL_CANCEL)
    order.set_session(Session.NORMAL)
    return order


def _fmt(price: float) -> str:
    """Schwab wants limit prices as strings; sub-dollar names need 4dp."""
    price = float(price)
    return f"{price:.4f}" if price < 1 else f"{price:.2f}"


def extract_order_id(response) -> Optional[str]:
    """Schwab returns the new order id in the Location header, not the body."""
    try:
        location = response.headers.get("Location", "") or ""
        return location.rsplit("/", 1)[-1] or None
    except Exception:
        return None


class OrderManager:
    """Submits orders, or in DRY_RUN logs exactly what it would have submitted."""

    def __init__(
        self,
        mode: OrderMode = OrderMode.DRY_RUN,
        client=None,
        account_hash: Optional[str] = None,
        limits: Optional[RiskLimits] = None,
        margin_committed: float = 0.0,
    ):
        self.mode = OrderMode(mode)
        self.client = client
        self.account_hash = account_hash
        self.limits = limits or RiskLimits()
        self.results: list[OrderResult] = []
        self.new_shorts_today = 0
        self.notional_today = 0.0
        #: Maintenance margin already tied up by open shorts, plus anything this
        #: session commits. Seeded from the ledger by the caller.
        self.margin_committed = float(margin_committed or 0.0)

    # -- gating ---------------------------------------------------------------

    def entry_block_reason(self, signal, quote: Optional[Quote]) -> Optional[str]:
        """Why this entry must NOT be sent, or None if it may proceed.

        Every check here was either absent or computed-then-ignored previously.
        """
        if signal.status != "ENTER_NOW":
            return f"status is {signal.status}; only ENTER_NOW opens a position"
        if not signal.shares or signal.shares <= 0:
            # Two very different causes used to share one message. "No price" is a data
            # problem; "notional too small" is a sizing choice that silently truncates
            # the tradeable universe to cheap names - at a $10 notional every stock
            # over $10 lands here and looks identical to missing data in the log.
            price = getattr(signal, "current_price", None)
            if price and price > 0:
                return (f"${signal.notional or 0:,.2f} notional buys 0 shares at "
                        f"${price:,.4f}; raise MAX_TRADE_NOTIONAL/TRADE_PCT to trade it")
            return "no share quantity (missing or invalid price)"
        price = getattr(signal, "current_price", None)
        if self.limits.min_entry_price and price and price < self.limits.min_entry_price:
            # No measured edge below $1: 29 of 560 pooled OOS trades, mean +5.88%,
            # t=0.46, 95% CI [-20.6%, +32.3%]. Not evidence of losses - evidence of
            # nothing. These names also cost ~5.9x their notional in margin.
            return (f"entry price ${price:,.4f} below the "
                    f"${self.limits.min_entry_price:,.2f} floor (no measured edge)")
        if signal.gap_up_ok is False:
            return f"gap-up filter ({signal.gap_up_pct:.1f}%)"
        if signal.capital_ok is False:
            return "capital constrained (exposure cap reached by higher-ranked signals)"
        if signal.schwab_is_shortable is False:
            return "Schwab reports the symbol is not shortable"
        if signal.schwab_is_shortable is None and not signal.likely_shortable:
            return "no Schwab confirmation and proxy says unshortable"
        htb = signal.schwab_htb_rate
        if htb is not None:
            if abs(float(htb)) > self.limits.max_htb_rate:
                return (f"borrow cost {abs(float(htb)):.0f}% exceeds "
                        f"{self.limits.max_htb_rate:.0f}% ceiling")
            if self.limits.max_borrow_cost_pct:
                # What it actually costs to hold this to its exit date. The rate
                # ceiling alone treats a 5-day hold and a 150-day hold as equal.
                days = brw.holding_days(signal.entry_date, signal.effective_date)
                cost = brw.expected_cost_pct(htb, days)
                if cost is not None and cost > self.limits.max_borrow_cost_pct:
                    return (f"borrow {brw.describe(htb, days)}, over the "
                            f"{100 * self.limits.max_borrow_cost_pct:.0f}% cap")
        if quote is None:
            return "no live quote available"
        if spread_too_wide(quote, self.limits.max_spread_pct):
            spread = quote.spread_pct
            shown = "unquotable" if spread is None else f"{spread * 100:.1f}%"
            return f"spread {shown} exceeds {self.limits.max_spread_pct * 100:.0f}% limit"
        if self.limits.margin_budget is not None:
            # FINRA 4210(c) floors the requirement at $2.50/share below $5, so a
            # cheap stock consumes margin far out of proportion to its notional -
            # 7x at $0.34, 111x at $0.02. Without this the system would size a
            # position the account cannot carry and learn about it from a broker
            # rejection, or worse, from a margin call on the position after it.
            price = (quote.last or quote.ask or signal.current_price
                     if quote else signal.current_price)
            need = mgn.short_maintenance_requirement(
                price, signal.shares, self.limits.house_margin_multiple)
            if self.margin_committed + need > self.limits.margin_budget:
                room = self.limits.margin_budget - self.margin_committed
                return (f"margin: needs "
                        f"{mgn.describe(price, signal.shares, self.limits.house_margin_multiple)}"
                        f", only ${room:,.0f} of ${self.limits.margin_budget:,.0f} left")
        if self.new_shorts_today >= self.limits.max_new_shorts_per_day:
            return f"daily new-short limit reached ({self.limits.max_new_shorts_per_day})"
        projected = self.notional_today + (signal.notional or 0.0)
        if projected > self.limits.max_daily_notional:
            return (f"daily notional cap: ${projected:,.0f} would exceed "
                    f"${self.limits.max_daily_notional:,.0f}")
        return None

    # -- entries --------------------------------------------------------------

    def submit_entry(self, signal, quote: Optional[Quote],
                     client_order_id: Optional[str] = None) -> OrderResult:
        block = self.entry_block_reason(signal, quote)
        if block:
            return self._record(signal.ticker, "SELL_SHORT", int(signal.shares or 0),
                                Outcome.SKIPPED, block, client_order_id=client_order_id)

        limit = marketable_limit_price(quote, "SELL_SHORT", self.limits.limit_buffer_pct)
        if not limit or limit <= 0:
            return self._record(signal.ticker, "SELL_SHORT", int(signal.shares or 0),
                                Outcome.SKIPPED, "could not derive a limit price",
                                client_order_id=client_order_id)

        shares = int(signal.shares)
        if self.mode is OrderMode.DRY_RUN:
            detail = (f"would SELL_SHORT {shares} {signal.ticker} @ limit {limit} "
                      f"(bid {quote.bid}, spread {quote.spread_pct * 100:.1f}%)")
            self.new_shorts_today += 1
            self.notional_today += signal.notional or 0.0
            self._commit_margin(signal, quote)
            return self._record(signal.ticker, "SELL_SHORT", shares, Outcome.WOULD_PLACE,
                                detail, limit_price=limit, client_order_id=client_order_id)

        result = self._place(build_entry_order(signal.ticker, shares, limit),
                             signal.ticker, "SELL_SHORT", shares, limit, client_order_id)
        if result.outcome in (Outcome.SUBMITTED.value, Outcome.UNCERTAIN.value):
            self.new_shorts_today += 1
            self.notional_today += signal.notional or 0.0
            self._commit_margin(signal, quote)
        return result

    def _commit_margin(self, signal, quote) -> None:
        """Book this short's maintenance requirement against the margin budget.

        Counted for UNCERTAIN submits too: if the order may have reached the
        broker, the margin may already be committed, and under-counting would let
        the next signal through on margin that is not actually free."""
        price = (quote.last or quote.ask if quote else None) or signal.current_price
        self.margin_committed += mgn.short_maintenance_requirement(
            price, signal.shares, self.limits.house_margin_multiple)

    # -- exits ----------------------------------------------------------------

    def submit_take_profit(self, ticker: str, shares: int, entry_fill: float,
                           take_profit_pct: float) -> OrderResult:
        """Rest a GTC cover limit at entry_fill * (1 - take_profit_pct)."""
        limit = round(float(entry_fill) * (1.0 - take_profit_pct), 4)
        if limit <= 0:
            return self._record(ticker, "BUY_TO_COVER", shares, Outcome.SKIPPED,
                                "take-profit price computed <= 0")
        if self.mode is OrderMode.DRY_RUN:
            return self._record(ticker, "BUY_TO_COVER", shares, Outcome.WOULD_PLACE,
                                f"would rest GTC take-profit @ {limit}", limit_price=limit)
        return self._place(build_take_profit_order(ticker, shares, limit),
                           ticker, "BUY_TO_COVER", shares, limit)

    def submit_cover(self, ticker: str, shares: int, quote: Optional[Quote]) -> OrderResult:
        """Marketable-limit cover for the time-based exit."""
        if quote is None:
            return self._record(ticker, "BUY_TO_COVER", shares, Outcome.SKIPPED,
                                "no live quote; cannot price a cover safely")
        limit = marketable_limit_price(quote, "BUY_TO_COVER", self.limits.limit_buffer_pct)
        if not limit or limit <= 0:
            return self._record(ticker, "BUY_TO_COVER", shares, Outcome.SKIPPED,
                                "could not derive a cover limit price")
        if self.mode is OrderMode.DRY_RUN:
            return self._record(ticker, "BUY_TO_COVER", shares, Outcome.WOULD_PLACE,
                                f"would COVER {shares} {ticker} @ limit {limit}",
                                limit_price=limit)
        return self._place(build_cover_order(ticker, shares, limit),
                           ticker, "BUY_TO_COVER", shares, limit)

    def cancel(self, order_id: str, ticker: str = "") -> OrderResult:
        """Cancel a resting order — used to pull the take-profit before a time exit.

        Covering without cancelling first can fill BOTH orders and flip the position
        long, so callers must treat a failed cancel as fatal for that position.
        """
        if self.mode is OrderMode.DRY_RUN:
            return self._record(ticker, "CANCEL", 0, Outcome.WOULD_PLACE,
                                f"would cancel order {order_id}", order_id=order_id)
        try:
            resp = self.client.cancel_order(order_id, self.account_hash)
        except Exception as e:
            return self._record(ticker, "CANCEL", 0, Outcome.UNCERTAIN,
                                f"cancel raised: {e}", order_id=order_id)

        if resp.status_code >= 400:
            # An order that is already filled or gone cannot be cancelled; that is a
            # normal outcome here, not a failure.
            if resp.status_code in (400, 404):
                return self._record(ticker, "CANCEL", 0, Outcome.SUBMITTED,
                                    f"order {order_id} not cancellable "
                                    f"(HTTP {resp.status_code}) - treating as already gone",
                                    order_id=order_id)
            return self._record(ticker, "CANCEL", 0, Outcome.REJECTED,
                                f"cancel failed: HTTP {resp.status_code} {resp.text[:120]}",
                                order_id=order_id)
        return self._record(ticker, "CANCEL", 0, Outcome.SUBMITTED,
                            f"cancelled {order_id}", order_id=order_id)

    # -- plumbing -------------------------------------------------------------

    def _place(self, order, ticker, side, shares, limit, client_order_id=None) -> OrderResult:
        if self.client is None or self.account_hash is None:
            return self._record(ticker, side, shares, Outcome.REJECTED,
                                "no authenticated client/account", limit_price=limit,
                                client_order_id=client_order_id)
        try:
            resp = self.client.place_order(self.account_hash, order)
        except Exception as e:
            # The request may have reached Schwab before the failure. Calling this
            # REJECTED (the old behavior) could leave a real, untracked short open.
            return self._record(ticker, side, shares, Outcome.UNCERTAIN,
                                f"submit raised ({e}) - reconcile before retrying",
                                limit_price=limit, client_order_id=client_order_id)

        if resp.status_code >= 400:
            return self._record(ticker, side, shares, Outcome.REJECTED,
                                f"broker rejected (HTTP {resp.status_code}): {resp.text[:200]}",
                                limit_price=limit, client_order_id=client_order_id)
        return self._record(ticker, side, shares, Outcome.SUBMITTED,
                            f"submitted @ limit {limit} (HTTP {resp.status_code})",
                            order_id=extract_order_id(resp), limit_price=limit,
                            client_order_id=client_order_id)

    def _record(self, ticker, side, quantity, outcome: Outcome, detail,
                order_id=None, limit_price=None, client_order_id=None) -> OrderResult:
        result = OrderResult(
            ticker=ticker, side=side, quantity=int(quantity or 0),
            order_type="CANCEL" if side == "CANCEL" else "LIMIT",
            mode=self.mode.value, outcome=outcome.value, detail=detail,
            order_id=order_id, limit_price=limit_price, client_order_id=client_order_id,
        )
        self.results.append(result)
        return result

    def summary(self) -> dict:
        from collections import Counter

        counts = Counter(r.outcome for r in self.results)
        return dict(total=len(self.results), **counts)
