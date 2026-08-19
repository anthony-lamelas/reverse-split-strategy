"""The trading heartbeat: proof that the host ran, readable from somewhere else.

Every alert in this system is emitted by `run_trading.py`, so the one failure it can
never report is itself not running. A laptop asleep at 9:25 ET executes no code and
sends no SMS - which is how the scheduled run silently drifted to 12:15, 17:07 and
23:32 ET for weeks in August 2026 with nothing noticing.

The fix is a record written on every run (including halted ones) and checked by
something that does not live on that machine - see `scripts/check_heartbeat.py` and
`.github/workflows/trading-watchdog.yml`.

Both the writer and the reader live here so the document shape is defined once:

    {"date": "2026-08-20",                      # ET calendar date, YYYY-MM-DD
     "ran_at": "2026-08-20T09:26:11-04:00",     # ISO-8601 with offset
     "mode": "DRY_RUN",                         # or "LIVE"
     "in_window": true,                         # near the open when it ran
     "exit_code": 0}
"""
from __future__ import annotations

from typing import Iterable, Optional

from . import calendar as mcal

COLLECTION = "trading_heartbeats"

#: Watchdog verdicts.
OK = 0
MISSED = 1
UNREACHABLE = 2


def build(mode: str, in_window: bool, exit_code: int, ts=None) -> dict:
    """The heartbeat document for a run that just finished."""
    now = mcal.now_et() if ts is None else ts
    return {
        "date": now.strftime("%Y-%m-%d"),
        "ran_at": now.isoformat(),
        "mode": mode,
        "in_window": bool(in_window),
        "exit_code": int(exit_code),
    }


def record(mode: str, in_window: bool, exit_code: int) -> Optional[str]:
    """Write a heartbeat. Returns an error string, or None on success.

    Best-effort by design: a heartbeat failure must never take down a trading run.
    The watchdog treats a missing heartbeat as a failure anyway, so the worst case is
    a false alarm, which is the safe direction to fail in.
    """
    try:
        from ..database import get_collection
        get_collection(COLLECTION).insert_one(build(mode, in_window, exit_code))
        return None
    except Exception as e:
        return str(e)[:200]


def evaluate(beats: Iterable[dict], day: str,
             allow_out_of_window: bool = False) -> tuple[int, str]:
    """Decide whether `day` looks healthy. Returns `(exit_code, message)`.

    Pure and offline so the watchdog's judgement can be tested without a database.
    This is the safety net for everything else, so it has to be the piece that is
    least likely to be quietly wrong.
    """
    if not mcal.is_trading_day(day):
        return OK, f"{day} is not a trading day; nothing expected."

    beats = list(beats)
    if not beats:
        return MISSED, (
            f"FAIL: no trading run recorded for {day}.\n"
            f"  The host did not execute. Check that the machine was awake at "
            f"9:25 ET and that the ReverseSplitDryRun task is enabled."
        )

    healthy = [b for b in beats
               if b.get("exit_code") == 0
               and (allow_out_of_window or b.get("in_window"))]
    if healthy:
        beat = healthy[-1]
        return OK, (f"OK: {len(beats)} run(s) for {day}; latest healthy at "
                    f"{beat.get('ran_at')} mode={beat.get('mode')}")

    latest = beats[-1]
    if not latest.get("in_window"):
        return MISSED, (
            f"FAIL: {day} ran at {latest.get('ran_at')} but OUTSIDE the entry window. "
            f"Strategy B enters at the open, so a late run is not that trade. The "
            f"host is probably asleep at 9:25 ET."
        )
    return MISSED, (f"FAIL: {day} ran at {latest.get('ran_at')} but exited "
                    f"{latest.get('exit_code')}. See logs/trading_audit.jsonl.")
