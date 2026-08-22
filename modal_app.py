"""Modal deployment: the live trading session, on a host that is always awake.

Why this exists
---------------
The Windows laptop could not be relied on to run the job on time. Two consecutive
mornings, with `WakeToRun=True`, `ACOnly=False`, wake timers enabled on both AC and
DC, and the machine on AC and never powered off, the 09:25 task simply did not fire.
Both times it caught up the instant the lid was opened - 11:57 on the second day.
Modern Standby (`S0 Low Power Idle`, the only sleep state that hardware offers)
ignores scheduled-task wake timers. The entry-window guard correctly refused to trade
both times, which is safe but is not a trading system.

Design notes
------------
- **State lives on a Volume, not in MongoDB.** A Mongo-backed ledger would put new
  code underneath the component that knows what we are short, days before a first
  live trade. The Volume keeps the existing, well-tested file code running unchanged.
- **`volume.commit()` is mandatory** after anything writes. Without it the ledger
  silently resets each run and the system forgets its positions - the worst available
  failure for a short book.
- **The session runs as a subprocess** so its exit codes (4 = out of window,
  5 = no quotes, ...) survive exactly as they do on the laptop.
- The GitHub Actions watchdog needs no change: it reads the MongoDB heartbeat and
  never knew which host produced it.
- **No web endpoints.** Server-side OAuth was built and removed: schwab-py refuses
  any callback whose hostname is not 127.0.0.1, and Schwab enforced the same - after
  authenticating against a public callback it returned to the login screen and never
  called the endpoint, across nine attempts. Re-auth is a local browser login plus
  `modal volume put`, once a week.

Usage
-----
    modal deploy modal_app.py             # deploy the scheduled trading run
    modal run modal_app.py::trade_once    # run once, off-schedule (expect exit 4)
    modal volume ls split-strategy-data   # inspect persisted state
"""
from __future__ import annotations

import os
import subprocess
import sys

import modal

APP_NAME = "reverse-split-strategy"
VOLUME_NAME = "split-strategy-data"
DATA_DIR = "/data"

app = modal.App(APP_NAME)

volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)

# One secret carrying everything config.py reads. Create with:
#   modal secret create split-strategy-secrets \
#       SCHWAB_APP_KEY=... SCHWAB_APP_SECRET=... MONGODB_URI=... \
#       SMTP_HOST=... SMTP_PORT=587 SMTP_USER=... SMTP_PASSWORD=... \
#       ALERT_EMAIL_TO=... SEC_USER_AGENT="Name email@example.com" \
#       SCHWAB_CALLBACK_URL=https://127.0.0.1:8182
secrets = modal.Secret.from_name("split-strategy-secrets")

# `add_local_dir` must be the LAST step on an image: Modal mounts local files at
# container start rather than baking them in, so a build step afterwards forces a full
# rebuild on every source edit.
_base = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements("requirements-runtime.txt")
)


def _with_source(img):
    return (img
            .add_local_dir("src", remote_path="/app/src")
            .add_local_dir("scripts", remote_path="/app/scripts"))


image = _with_source(_base)

# Point the relocatable state at the Volume. config.py reads both from the
# environment and defaults to the repo layout, so local runs are unaffected.
ENV = {
    "PYTHONPATH": "/app/src",
    "DATA_DIR": DATA_DIR,
    "LOG_DIR": f"{DATA_DIR}/logs",
    "SCHWAB_TOKEN_PATH": f"{DATA_DIR}/.schwab_token.json",
}


def _run_session(extra_args: list[str] | None = None) -> int:
    """Run the trading session as a subprocess, preserving its exit code."""
    env = {**os.environ, **ENV}
    cmd = [sys.executable, "/app/scripts/run_trading.py", *(extra_args or [])]
    print(f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, env=env, cwd="/app", capture_output=True, text=True)
    if proc.stdout:
        print(proc.stdout)
    if proc.stderr:
        print("STDERR:", proc.stderr, file=sys.stderr)
    print(f"[modal] session exited {proc.returncode}")
    return proc.returncode


@app.function(
    image=image,
    volumes={DATA_DIR: volume},
    secrets=[secrets],
    # 09:25 America/New_York, weekdays. Modal handles the timezone, so this tracks
    # the 09:30 open across the DST change instead of drifting an hour.
    schedule=modal.Cron("25 9 * * 1-5", timezone="America/New_York"),
    timeout=600,
    retries=0,  # a retry would re-enter the entry-window logic; the guard, not a
                # retry policy, decides whether a late run may trade.
)
def trade() -> int:
    """The scheduled session. LIVE - this places real orders.

    Guarded, in the order they are evaluated: STOP kill switch / STOP_TRADING, the
    trading-day check, the 09:15-09:45 entry window (halts rather than trading late),
    ledger/broker reconciliation, the $1 price floor, the margin budget capped by
    broker availableFunds, borrow rate and expected borrow cost, spread width,
    shortability, and MAX_NEW_SHORTS_PER_DAY / MAX_DAILY_NOTIONAL /
    MAX_TRADE_NOTIONAL from the Modal secret ($50 and 1/day as configured).

    To revert to dry-run: drop the flags below and redeploy. To stop immediately
    without a deploy: `python scripts/emergency_stop.py`.
    """
    os.makedirs(f"{DATA_DIR}/logs", exist_ok=True)
    try:
        return _run_session(["--live", "--i-am-sure"])
    finally:
        # Always commit, including on a halted or crashed run: the ledger may have
        # been written ahead of an order that did reach the broker.
        volume.commit()
        print("[modal] volume committed")


@app.function(image=image, volumes={DATA_DIR: volume}, secrets=[secrets], timeout=600)
def trade_once(live: bool = False) -> int:
    """Manual off-schedule run, for testing. Expect exit 4 outside 09:15-09:45 ET."""
    os.makedirs(f"{DATA_DIR}/logs", exist_ok=True)
    try:
        return _run_session(["--live", "--i-am-sure"] if live else [])
    finally:
        volume.commit()
