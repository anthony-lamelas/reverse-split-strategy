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
import os
import subprocess
import sys
import tempfile
from pathlib import Path

VOLUME = "split-strategy-data"
REMOTE = "/STOP"


def _modal(*args: str) -> tuple[int, str]:
    """Run a modal CLI command, returning (exit_code, combined output).

    Forces UTF-8: the Modal CLI prints box-drawing characters that crash a cp1252
    Windows console with a bare 'charmap' codec error, which looks like a Modal
    failure and is not one.
    """
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    proc = subprocess.run([sys.executable, "-m", "modal", *args],
                          capture_output=True, text=True, env=env)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def is_halted() -> bool:
    _, out = _modal("volume", "ls", VOLUME)
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
        print("HALTED" if is_halted() else "ACTIVE - trading is allowed")
        return 0
    if args.resume:
        return resume()
    return halt()


if __name__ == "__main__":
    sys.exit(main())
