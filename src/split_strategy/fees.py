"""Regulatory fees on a short entry: small per trade, not small on these trades.

Schwab charges no commission on online equity orders, so it is tempting to treat
execution as free. On this strategy it is not, for one reason: the two regulatory
fees that do apply are charged on the SELL side, and a short's sell is its ENTRY.
Every position this system opens pays them; none of them appear in the ledger.

Why the shape of the fee matters more than its size
---------------------------------------------------
FINRA's TAF is charged per SHARE, and this strategy shorts sub-dollar stocks in
four-digit share counts. A $195 position in a $0.10 stock is 1,932 shares, so the
per-share fee is roughly sixty times the notional-based SEC fee on the same trade.
A flat per-trade estimate would therefore misprice precisely the trades that are
taken most often, and would flatter the cheapest-looking names the most.

Expressed against notional, the per-share component scales as 1/price: at $0.10 a
share it costs about 0.17% of notional, at $1.00 about 0.017%. That is an order of
magnitude, and it runs in the same direction as the borrow cost - both punish the
low-priced end of the candidate list. See `borrow.py` for the other half.

This is deliberately not `backtest/slippage.py`. That module estimates a
proportional round-trip cost from OHLC bars to simulate trades that never
happened; it takes its fixed-cost term as a caller-supplied constant and knows
nothing about share counts. This one prices a fill that actually occurred.

Rates
-----
Both rates are set by regulators and change - the SEC's Section 31 rate is reset
at least annually and has moved by more than 5x within a single decade. They are
therefore config values, not constants, and the defaults are PLACEHOLDERS that
must be checked against Schwab's current fee schedule before any P&L number
computed here is trusted. `verify_rates_note()` exists so callers can say so on
screen rather than presenting an unverified figure as fact.
"""
from __future__ import annotations

from typing import Optional

from . import config


def entry_fees(shares: Optional[float], price: Optional[float]) -> Optional[float]:
    """Total regulatory fees in dollars for opening a short of `shares` at `price`.

    Returns None when either input is missing, so a partially-filled or unpriced
    row propagates "unknown" rather than a confidently wrong zero.
    """
    if shares is None or price is None:
        return None
    try:
        n = float(shares)
        px = float(price)
    except (TypeError, ValueError):
        return None
    if n <= 0 or px <= 0:
        return None

    # Per-share (FINRA TAF), capped per trade as the published schedule caps it.
    per_share = min(n * config.FEE_PER_SHARE, config.FEE_PER_SHARE_CAP)
    # Per-dollar of proceeds (SEC Section 31).
    per_notional = n * px * config.FEE_PER_NOTIONAL
    return per_share + per_notional


def entry_fees_pct(shares: Optional[float], price: Optional[float]) -> Optional[float]:
    """Entry fees as a fraction of notional, for comparing against borrow cost."""
    fees = entry_fees(shares, price)
    if fees is None:
        return None
    notional = float(shares) * float(price)
    if notional <= 0:
        return None
    return fees / notional


def verify_rates_note() -> str:
    """One line naming the rates in force, so an unverified default is visible."""
    note = (f"fees: ${config.FEE_PER_SHARE:.6f}/share "
            f"(cap ${config.FEE_PER_SHARE_CAP:.2f}) + "
            f"${config.FEE_PER_NOTIONAL * 1e6:.2f}/M notional")
    if not config.FEE_RATES_VERIFIED:
        note += "  [UNVERIFIED DEFAULTS]"
    return note
