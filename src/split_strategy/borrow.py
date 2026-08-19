"""Borrow cost: what it actually costs to stay short, not just the sticker rate.

Two defects in the original veto, both fixed here.

1. It capped the annualized RATE and ignored holding period. A 100%/yr name held
   5 days costs 1.4% of notional; held 150 days it costs 41%. Those were treated
   identically, even though the expected holding period is known at entry time -
   it is the gap between the entry date and the split's execution date.

2. It was evaluated once, at entry, and never again. Borrow rates on micro-caps
   can spike hard mid-hold, and that spike happens precisely when a squeeze is
   developing and the position is already moving against you. Nothing re-checked
   it, so the system was hoping rather than watching.

Observed rates on this strategy's signals, for calibration:

    under $1   n=40   median  7.5%/yr   mean 22.6%   max 113.0%
    $1 and up  n=10   median  0.0%/yr   mean  8.3%   max  54.2%

Combined with a median traded holding period of 11 days, borrow is a rounding
error on the typical trade and a real threat only in the tail where a high rate
meets a long hold.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

DAYS_PER_YEAR = 365.0


def holding_days(entry_date, exit_date) -> Optional[int]:
    """Expected days held, or None if either date is unusable.

    Returns None rather than a guess: a wrong holding period silently mis-prices
    the veto, and refusing to estimate is safer than estimating badly.
    """
    try:
        start = pd.Timestamp(entry_date)
        end = pd.Timestamp(exit_date)
    except Exception:
        return None
    if pd.isna(start) or pd.isna(end):
        return None
    days = (end.normalize() - start.normalize()).days
    return days if days >= 0 else None


def expected_cost_pct(annual_rate_pct: float, days: Optional[int]) -> Optional[float]:
    """Borrow cost over `days`, as a fraction of notional.

    `annual_rate_pct` is the annualized percentage (Schwab reports it negative;
    the sign is dropped). Returns None when the holding period is unknown.
    """
    if days is None:
        return None
    rate = abs(float(annual_rate_pct or 0.0)) / 100.0
    return rate * (float(days) / DAYS_PER_YEAR)


def describe(annual_rate_pct: float, days: Optional[int]) -> str:
    """One-line explanation for logs and skip reasons."""
    cost = expected_cost_pct(annual_rate_pct, days)
    rate = abs(float(annual_rate_pct or 0.0))
    if cost is None:
        return f"{rate:.0f}%/yr over an unknown holding period"
    return f"{rate:.0f}%/yr over ~{days}d costs {100 * cost:.1f}% of notional"


def has_spiked(entry_rate: Optional[float], current_rate: Optional[float],
               absolute_ceiling: float = 100.0,
               multiple: float = 3.0,
               floor_pct: float = 10.0) -> bool:
    """Has borrow become alarming since we shorted this name?

    True when the current rate breaches `absolute_ceiling`, or has risen to
    `multiple` times what it was at entry. `floor_pct` stops trivial moves from
    paging: 0.5% -> 2% is a 4x rise but costs nothing worth waking up for.
    """
    if current_rate is None:
        return False
    current = abs(float(current_rate))
    if current >= absolute_ceiling:
        return True
    if entry_rate is None or current < floor_pct:
        return False
    entry = abs(float(entry_rate))
    if entry <= 0:
        # Free at entry and materially expensive now is exactly the case to catch.
        return current >= floor_pct
    return current >= entry * multiple
