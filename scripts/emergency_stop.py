#!/usr/bin/env python3
"""Halt or resume live trading on Modal, in one command.

    python scripts/emergency_stop.py             # HALT trading
    python scripts/emergency_stop.py --resume    # allow trading again
    python scripts/emergency_stop.py --status    # is it halted right now?

Works by putting a `STOP` marker on the Modal Volume, which `check_kill_switch()`
tests before anything else in the session. Deliberately NOT the `STOP_TRADING=1`
secret route: that means rebuilding the whole secret from `.env`, which re-uploads
every credential and is a poor thing to be doing in a hurry, at the exact moment you
most want a single reliable action.

The next scheduled run halts with exit code 3 and places no orders.

IMPORTANT: this stops the bot, it does NOT close positions. Anything already short
stays short. Resting take-profit orders remain live at the broker, but the
time-based cover will not be submitted while trading is halted - so if a position is
near its exit date, cover it yourself in the Schwab UI.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "src"))

from split_strategy import modal_cli  # noqa: E402

VOLUME = "split-strategy-data"
REMOTE = "/STOP"


def _modal(*args: str) -> tuple[int, str]:
    """Run a modal CLI command, returning (exit_code, combined output).

    Delegates to `split_strategy.modal_cli`, which decodes as UTF-8, strips glyphs a
    cp1252 console cannot print, and pins MODAL_PROFILE. This function previously did
    only the first of those, and did it wrong: it set PYTHONIOENCODING in the CHILD's
    environment but decoded with `text=True`, which uses the PARENT's locale. The
    result was that a failing halt could raise UnicodeError while printing the reason
    the halt had failed - in the one tool whose entire job is to work when nothing
    else does.
    """
    r = modal_cli.run(*args)
    return r.returncode, modal_cli.ascii_only(r.combined)


def is_halted() -> Optional[bool]:
    """True = halted, False = trading allowed, None = COULD NOT DETERMINE.

    The tri-state is the point. This used to discard the exit code and test only
    whether "STOP" appeared in the output, so a `volume ls` that failed outright -
    wrong workspace, expired Modal token, no network - contained no "STOP" and was
    reported as "ACTIVE - trading is allowed". A kill switch that answers "all clear"
    when it could not reach the switch is worse than one that errors: it is the
    reassuring version of not knowing.
    """
    code, out = _modal("volume", "ls", VOLUME)
    if code != 0:
        return None
    return "STOP" in out.split()


def halt() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        marker = Path(tmp) / "STOP"
        marker.write_text("halted by scripts/emergency_stop.py\n", encoding="utf-8")
        code, out = _modal("volume", "put", "--force", VOLUME, str(marker), REMOTE)
    if code != 0:
        print(out.strip()[-500:])
        print("\nFAILED to halt. Fall back to the Schwab UI to close positions, and "
              "check `modal app list`.")
        return 1
    print("TRADING HALTED. The next scheduled run will exit 3 and place no orders.")
    print("Positions already open are NOT closed - cover them in the Schwab UI if "
          "any are near their exit date.")
    return 0


def resume() -> int:
    code, out = _modal("volume", "rm", VOLUME, REMOTE)
    if code != 0 and "not found" not in out.lower():
        print(out.strip()[-500:])
        return 1
    print("Trading resumed. The next scheduled run will trade normally.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--resume", action="store_true", help="allow trading again")
    g.add_argument("--status", action="store_true", help="report current state only")
    args = ap.parse_args()

    if args.status:
        state = is_halted()
        if state is None:
            print("UNKNOWN - could not reach Modal to check the kill switch.")
            print("Trading may or may not be halted. Check `modal volume ls "
                  f"{VOLUME}` for a STOP entry, and treat the bot as RUNNING until "
                  "you have confirmed otherwise.")
            return 2
        print("HALTED" if state else "ACTIVE - trading is allowed")
        return 0
    if args.resume:
        return resume()
    return halt()


if __name__ == "__main__":
    sys.exit(main())
