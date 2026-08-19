"""Margin requirements for short equity positions (FINRA Rule 4210(c)).

The rule, verbatim:

    Short, below $5.00/share:   "$2.50 per share or 100 percent of the current
                                 market value, whichever amount is greater"
    Short, at/above $5.00:      "$5.00 per share or 30 percent of the current
                                 market value, whichever amount is greater"

    https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210

Why this module exists
----------------------
The $2.50-per-share floor INVERTS the economics of shorting cheap stocks: the
lower the price, the more margin a given dollar of notional consumes. At $0.34 a
$50 short needs ~$368 of margin (7.3x notional); at $0.0224 it needs ~$5,580
(111x). Roughly 71% of this strategy's signals price under $1.00, so the binding
constraint on concurrency is margin, not capital.

Nothing else in the system modelled this. `MAX_EXPOSURE` caps committed
*notional*, and the live-expectations study models spread, borrow, sizing and
shortability - none of which see margin. The result was a system that would
happily size a position the account cannot margin and discover it via a broker
rejection.

This is a regulatory FLOOR. A broker may impose more (Schwab reserves the right
to raise requirements on low-priced, thinly traded or volatile securities), never
less - hence `house_multiple`.
"""
from __future__ import annotations

from typing import Iterable, Mapping, Optional

#: Price boundary between the two FINRA tiers.
LOW_PRICE_THRESHOLD = 5.00
#: Per-share floor and market-value fraction for shorts under $5.
LOW_PRICE_PER_SHARE = 2.50
LOW_PRICE_PCT = 1.00
#: Per-share floor and market-value fraction for shorts at or above $5.
HIGH_PRICE_PER_SHARE = 5.00
HIGH_PRICE_PCT = 0.30


def short_maintenance_requirement(price: float, shares: int,
                                  house_multiple: float = 1.0) -> float:
    """Maintenance margin required to hold a short of `shares` at `price`.

    `house_multiple` scales the regulatory floor for broker house requirements
    (1.0 = the FINRA minimum). Returns 0.0 for a non-position.
    """
    price = float(price or 0.0)
    shares = int(shares or 0)
    if price <= 0 or shares <= 0:
        return 0.0
    if price < LOW_PRICE_THRESHOLD:
        base = max(LOW_PRICE_PER_SHARE * shares, LOW_PRICE_PCT * price * shares)
    else:
        base = max(HIGH_PRICE_PER_SHARE * shares, HIGH_PRICE_PCT * price * shares)
    return base * float(house_multiple or 1.0)


def margin_multiple(price: float, house_multiple: float = 1.0) -> float:
    """Margin required per $1 of notional at `price`.

    This is the number that decides whether the strategy is implementable: it is
    independent of account size, so a bigger account does not escape it.
    """
    price = float(price or 0.0)
    if price <= 0:
        return 0.0
    # Requirement for one share divided by that share's market value.
    return short_maintenance_requirement(price, 1, house_multiple) / price


def max_shares_for_margin(price: float, available: float,
                          house_multiple: float = 1.0) -> int:
    """Largest short, in shares, that `available` margin dollars will support."""
    price = float(price or 0.0)
    available = float(available or 0.0)
    if price <= 0 or available <= 0:
        return 0
    per_share = short_maintenance_requirement(price, 1, house_multiple)
    if per_share <= 0:
        return 0
    return int(available // per_share)


def portfolio_margin_requirement(
    positions: Iterable[Mapping],
    quotes: Optional[Mapping] = None,
    house_multiple: float = 1.0,
) -> float:
    """Total maintenance margin tied up by the open shorts in `positions`.

    Requirements are marked against CURRENT market value, so a live quote is used
    when one is available and the entry fill price only as a fallback. Marking a
    fallen stock at its entry price would understate the requirement, which is
    the wrong direction to be wrong in.
    """
    quotes = quotes or {}
    total = 0.0
    for pos in positions:
        shares = int(pos.get("filled_shares") or pos.get("shares") or 0)
        if shares <= 0:
            continue
        ticker = (pos.get("ticker") or "").upper()
        quote = quotes.get(ticker)
        price = None
        if quote is not None:
            price = getattr(quote, "last", None) or getattr(quote, "ask", None)
        if not price:
            price = pos.get("entry_fill_price") or 0.0
        if not price and pos.get("notional") and shares:
            price = float(pos["notional"]) / shares
        total += short_maintenance_requirement(price, shares, house_multiple)
    return total


def describe(price: float, shares: int, house_multiple: float = 1.0) -> str:
    """One-line explanation for logs and skip reasons."""
    req = short_maintenance_requirement(price, shares, house_multiple)
    notional = float(price or 0.0) * int(shares or 0)
    if notional <= 0:
        return "no position"
    return (f"${req:,.0f} margin for ${notional:,.0f} notional "
            f"({req / notional:.1f}x) at ${price:,.4f}/share")
