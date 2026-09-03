"""Live quote handling and marketable-limit pricing.

Why this exists: the strategy trades sub-$1 micro-caps where the bid-ask spread can be
10%+ of the price. A market order on a $0.09 stock with a one-cent spread loses ~11%
instantly on entry and again on exit — far more than the 1.5% the backtest assumes for
all costs combined. Every order is therefore priced as a *marketable limit*: aggressive
enough to fill immediately in normal conditions, but with a hard bound on how bad the
fill can be. Names whose spread is too wide to trade sanely are skipped outright.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

# US equities quote in $0.01 increments at or above $1.00, and $0.0001 below it.
TICK_ABOVE_DOLLAR = 0.01
TICK_BELOW_DOLLAR = 0.0001


@dataclass
class Quote:
    ticker: str
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    #: Previous session's close, for the gap-up filter. Schwab calls this `closePrice`
    #: (verified against a live payload: AAPL closePrice 324.96 vs lastPrice 327.98).
    prev_close: Optional[float] = None
    #: Displayed size behind each side. Zero means the price is a placeholder, not a
    #: quote anyone will honour - see `entry_price`.
    bid_size: Optional[float] = None
    ask_size: Optional[float] = None

    @property
    def mid(self) -> Optional[float]:
        if self.bid and self.ask and self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2.0
        return self.last

    @property
    def entry_price(self) -> Optional[float]:
        """The price to size a SHORT entry from, and to test the $1.00 floor against.

        A short sells, and a sell fills at the bid - so the bid is what we actually
        receive, and sizing off the mid or the ask overstates the proceeds by up to
        half a spread on names where the filter tolerates 5%.

        **The bid only counts when something is behind it.** Thin names quote garbage
        outside regular hours, and this runs at 09:25, before the open. Measured live
        on the exact tickers this strategy was dropping:

            WHLRL   bid 0.0001  (size 0)   ask 2147.48  last 80.00
            GLTK    bid 0.2526  (size 0)   ask    1.65  last  1.35
            SCNI    bid 1.9500  (size 500) ask    2.04  last  1.995

        WHLRL's bid is a hundredth of a cent against an eighty-dollar stock, and its
        ask is a sentinel. Sizing off that bid buys five million shares. The $1.00
        floor happens to catch this one, but a stub bid of $1.50 on an $80 name would
        clear the floor and size fifty times too large - so the floor is not the
        guard. `bid_size > 0` is: a price with no size behind it is not a quote.

        Falls back to `last`, then to None. None marks the signal unpriced, which is
        what routes it to "missing or invalid price" rather than mis-sizing it.
        """
        if self.bid and self.bid > 0 and self.bid_size and self.bid_size > 0:
            return self.bid
        return self.last if (self.last and self.last > 0) else None

    @property
    def spread_pct(self) -> Optional[float]:
        """Bid-ask spread as a fraction of the mid. None when unquotable."""
        if not (self.bid and self.ask) or self.bid <= 0 or self.ask <= 0:
            return None
        if self.ask < self.bid:
            return None  # crossed/locked book - treat as unusable
        mid = (self.bid + self.ask) / 2.0
        if mid <= 0:
            return None
        return (self.ask - self.bid) / mid

    @property
    def is_tradeable(self) -> bool:
        return self.spread_pct is not None


def round_to_tick(price: float, side: str) -> float:
    """Round a limit price to a valid tick, always in the *aggressive* direction.

    Rounding a sell limit up (or a buy limit down) could make the order non-marketable
    and leave it unfilled, so sells round down and buys round up. `steps` is rounded to
    9dp first so float noise (0.09/0.0001 = 900.0000000000001) can't shift the result
    by a whole tick.
    """
    if price <= 0:
        return price
    tick = TICK_ABOVE_DOLLAR if price >= 1.0 else TICK_BELOW_DOLLAR
    steps = round(price / tick, 9)
    if side.upper() in ("SELL", "SELL_SHORT"):
        rounded = math.floor(steps) * tick
    else:
        rounded = math.ceil(steps) * tick
    return round(rounded, 6)


def parse_quote(ticker: str, payload: dict) -> Quote:
    """Extract bid/ask/last from a Schwab /quotes entry."""
    q = (payload or {}).get("quote", {}) or {}
    return Quote(
        ticker=ticker.upper(),
        bid=_positive(q.get("bidPrice")),
        ask=_positive(q.get("askPrice")),
        last=_positive(q.get("lastPrice")),
        prev_close=_positive(q.get("closePrice")),
        # `_positive` maps a zero size to None, which `entry_price` treats the same as
        # absent - both mean "nothing is behind this price". A missing size field is
        # therefore conservative by default: the bid is not trusted.
        bid_size=_positive(q.get("bidSize")),
        ask_size=_positive(q.get("askSize")),
    )


def _positive(value) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def get_quotes(client, tickers: list[str]) -> dict[str, Quote]:
    """Fetch quotes for several tickers. Missing/failed symbols are simply absent."""
    if not tickers:
        return {}
    out: dict[str, Quote] = {}
    try:
        resp = client.get_quotes(tickers)
        if resp.status_code < 400:
            data = resp.json()
            for t in tickers:
                entry = data.get(t.upper())
                if entry:
                    out[t.upper()] = parse_quote(t, entry)
    except Exception:
        pass
    return out


def marketable_limit_price(quote: Quote, side: str, buffer_pct: float = 0.02) -> Optional[float]:
    """Price a limit order that should fill immediately but bounds the worst fill.

    SELL_SHORT: limit sits `buffer_pct` *below* the bid — it crosses the spread and
    fills, but we will never sell lower than that floor.
    BUY_TO_COVER: limit sits `buffer_pct` *above* the ask — same idea, capped ceiling.
    """
    side_u = side.upper()
    if side_u in ("SELL", "SELL_SHORT"):
        anchor = quote.bid or quote.last
        if not anchor:
            return None
        return round_to_tick(anchor * (1.0 - buffer_pct), "SELL")
    anchor = quote.ask or quote.last
    if not anchor:
        return None
    return round_to_tick(anchor * (1.0 + buffer_pct), "BUY")


def spread_too_wide(quote: Quote, max_spread_pct: float) -> bool:
    """True when the name should be skipped on execution-cost grounds.

    An unquotable book (no two-sided quote, or crossed) counts as too wide: we cannot
    bound the fill, so we do not trade it.
    """
    spread = quote.spread_pct
    if spread is None:
        return True
    return spread > max_spread_pct
