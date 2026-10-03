"""US market calendar and Eastern-time helpers.

Two bugs this addresses:

1. `_next_business_day` skipped weekends only, so an entry landing on Thanksgiving or
   July 4th produced an ENTER_NOW signal for a closed market.
2. All date logic used naive `pd.Timestamp.now()`. On a UTC machine (a CI runner, or a
   scheduled task run before 5am ET) "today" resolves to tomorrow's date in ET terms,
   silently shifting ENTER_NOW to the wrong day.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable, Optional

import pandas as pd

ET = "America/New_York"

# NYSE/Nasdaq full-day closures. Extend annually; a missing future year degrades to
# "weekday = open", which is the same behavior as before, not worse.
MARKET_HOLIDAYS: set[date] = {
    # 2025
    date(2025, 1, 1), date(2025, 1, 9), date(2025, 1, 20), date(2025, 2, 17),
    date(2025, 4, 18), date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4),
    date(2025, 9, 1), date(2025, 11, 27), date(2025, 12, 25),
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    # 2027
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
}


def now_et() -> pd.Timestamp:
    """Current time in Eastern, tz-aware. All trading decisions key off this."""
    return pd.Timestamp.now(tz=ET)


def today_et() -> pd.Timestamp:
    """Today's ET calendar date as a tz-naive midnight timestamp.

    tz-naive on purpose: it is compared against tz-naive dates parsed from Mongo and
    the ledger, and mixing the two raises.
    """
    return pd.Timestamp(now_et().date())


def is_trading_day(ts) -> bool:
    ts = pd.Timestamp(ts)
    if ts.weekday() >= 5:
        return False
    return ts.date() not in MARKET_HOLIDAYS


def next_trading_day(ts) -> pd.Timestamp:
    """The next session strictly after `ts`, skipping weekends and holidays."""
    nxt = pd.Timestamp(ts).normalize() + pd.Timedelta(days=1)
    # Bounded so an unexpected holiday run can't spin forever.
    for _ in range(15):
        if is_trading_day(nxt):
            return nxt
        nxt += pd.Timedelta(days=1)
    return nxt


def prev_trading_day(ts) -> pd.Timestamp:
    """The last session strictly before `ts`, skipping weekends and holidays."""
    prv = pd.Timestamp(ts).normalize() - pd.Timedelta(days=1)
    for _ in range(15):
        if is_trading_day(prv):
            return prv
        prv -= pd.Timedelta(days=1)
    return prv


def first_open_after(ts) -> pd.Timestamp:
    """The session whose 09:30 open is the first one after `ts` (Eastern, naive).

    A filing accepted at 08:00 on a trading day can be traded at that day's open; one
    accepted at 09:30 or later, or on a closed day, waits for the next session.
    Returns the session date at midnight.
    """
    ts = pd.Timestamp(ts)
    day = ts.normalize()
    if is_trading_day(day) and ts < day + pd.Timedelta(hours=9, minutes=30):
        return day
    return next_trading_day(day)


def market_open_et(ts=None) -> pd.Timestamp:
    ts = now_et() if ts is None else pd.Timestamp(ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize(ET)
    return ts.normalize() + pd.Timedelta(hours=9, minutes=30)


def minutes_until_open(ts=None) -> float:
    ts = now_et() if ts is None else pd.Timestamp(ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize(ET)
    return (market_open_et(ts) - ts).total_seconds() / 60.0


def in_entry_window(ts=None, before_min: float = 15.0,
                    after_min: float = 15.0) -> tuple[bool, float]:
    """Is `ts` close enough to the open to place the day's entries?

    Returns `(ok, minutes_until_open)`, where positive minutes mean "before the open".

    Deliberately built on `minutes_until_open()` rather than `is_market_hours()`: the
    scheduled run fires at 9:25, five minutes *before* the 9:30 open, so an
    is-market-hours test would reject the on-time case and accept every late one.

    Strategy B enters at the open, so a session that fires hours late is not the trade
    that was backtested - runs were observed at 12:15, 17:07 and 23:32 ET when the host
    slept through its trigger and the scheduler caught up on wake.
    """
    mins = minutes_until_open(ts)
    return (-after_min <= mins <= before_min), mins


def describe_window(minutes_until: float) -> str:
    """Human-readable position relative to the open, for logs and alerts."""
    if minutes_until >= 0:
        return f"{minutes_until:.0f} min before the open"
    return f"{abs(minutes_until):.0f} min after the open"
