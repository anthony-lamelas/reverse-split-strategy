#!/usr/bin/env python3
"""Watchdog: did the trading host actually run today?

This is deliberately NOT run on the trading machine. Every other alert in this system
is emitted by `run_trading.py`, which means the one failure it can never report is
itself not running - a laptop asleep at 9:25 executes no code and sends no SMS. That
is exactly what happened through August 2026: the job silently drifted to 12:15,
17:07, even 23:32 ET, and nothing noticed for weeks.

So `run_trading.py` writes a heartbeat to MongoDB on every run, including halted ones,
and this script - run from GitHub Actions, on GitHub's infrastructure - fails the
workflow when today's heartbeat is missing, late, or unhealthy. GitHub emails on
workflow failure by default, so no SMTP secrets are needed here.

Exit codes:
    0  a healthy in-window run was recorded today (or the market is closed)
    1  no run recorded, or the run was out-of-window / halted
    2  could not reach MongoDB to check
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "src"))

from split_strategy.live import calendar as mcal  # noqa: E402
from split_strategy.live import heartbeat  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Check that the trading host ran today")
    ap.add_argument("--date", default=None,
                    help="ET date to check as YYYY-MM-DD (default: today in ET)")
    ap.add_argument("--allow-out-of-window", action="store_true",
                    help="treat a late run as healthy (it ran, just not near the open)")
    args = ap.parse_args()

    day = args.date or mcal.today_et().strftime("%Y-%m-%d")

    # Skip the database round trip entirely on weekends and exchange holidays.
    if not mcal.is_trading_day(day):
        code, message = heartbeat.evaluate([], day)
        print(f"[watchdog] {message}")
        return code

    try:
        from split_strategy.database import get_collection
        beats = list(get_collection(heartbeat.COLLECTION).find({"date": day}))
    except Exception as e:
        # An infrastructure problem is not the same as a missed run. Exit 2 so the two
        # are distinguishable, but still fail loudly rather than assume health.
        print(f"[watchdog] could not read heartbeats: {str(e)[:200]}")
        return heartbeat.UNREACHABLE

    code, message = heartbeat.evaluate(beats, day, args.allow_out_of_window)
    print(f"[watchdog] {message}")
    return code


if __name__ == "__main__":
    sys.exit(main())
