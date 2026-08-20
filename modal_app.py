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

Usage
-----
    modal deploy modal_app.py             # deploy the cron + auth endpoints
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
#       SCHWAB_CALLBACK_URL=<the deployed auth_callback URL> \
#       SCHWAB_AUTH_SECRET=<random string guarding the auth endpoints>
secrets = modal.Secret.from_name("split-strategy-secrets")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements("requirements-runtime.txt")
    .add_local_dir("src", remote_path="/app/src")
    .add_local_dir("scripts", remote_path="/app/scripts")
)

# The auth endpoints need FastAPI; the trading cron does not. Layering it in a second
# image keeps the scheduled path lean - the whole point of requirements-runtime.txt -
# while still satisfying @modal.fastapi_endpoint. Modal reuses the base layers, so
# this costs one extra pip install, not a second full build.
web_image = image.pip_install("fastapi[standard]")

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
    """The scheduled session. Dry-run until `--live --i-am-sure` is added here."""
    os.makedirs(f"{DATA_DIR}/logs", exist_ok=True)
    try:
        return _run_session()
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


# ---------------------------------------------------------------------------------
# Weekly re-auth, entirely from a phone
# ---------------------------------------------------------------------------------
# Schwab refresh tokens expire every 7 days and renewal needs an interactive login.
# The callback URL does NOT have to be localhost, so pointing it at these endpoints
# means the whole flow happens in a phone browser: the expiry alert texts a link, you
# tap it, log in, and the token lands on the Volume. No terminal, no pasting.

def _auth_collection():
    sys.path.insert(0, "/app/src")
    from split_strategy.database import get_collection
    return get_collection("auth_flows")


@app.function(image=web_image, volumes={DATA_DIR: volume}, secrets=[secrets])
@modal.fastapi_endpoint(method="GET")
def auth_start(k: str = ""):
    """Begin the OAuth flow and redirect the caller to Schwab."""
    from fastapi.responses import JSONResponse, RedirectResponse

    if not k or k != os.environ.get("SCHWAB_AUTH_SECRET"):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    sys.path.insert(0, "/app/src")
    from schwab.auth import get_auth_context

    ctx = get_auth_context(os.environ["SCHWAB_APP_KEY"],
                           os.environ["SCHWAB_CALLBACK_URL"])
    # The callback runs in a different container, so the state has to be shared.
    # Mongo rather than the Volume: no reload/commit semantics to get wrong for a
    # value that lives for ninety seconds.
    import datetime as dt
    _auth_collection().insert_one({
        "state": ctx.state,
        "callback_url": ctx.callback_url,
        "created_at": dt.datetime.now(dt.timezone.utc),
    })
    return RedirectResponse(ctx.authorization_url, status_code=302)


@app.function(image=web_image, volumes={DATA_DIR: volume}, secrets=[secrets])
@modal.fastapi_endpoint(method="GET")
def auth_callback(request):
    """Complete the flow: verify state, exchange the code, write the token."""
    from fastapi.responses import HTMLResponse

    sys.path.insert(0, "/app/src")
    import json
    from schwab.auth import AuthContext, client_from_received_url

    received_url = str(request.url)
    state = request.query_params.get("state")

    # Without this check, anyone who hits this endpoint with THEIR Schwab code
    # writes THEIR token onto our Volume, and the next run trades their account.
    flow = _auth_collection().find_one_and_delete({"state": state}) if state else None
    if not flow:
        return HTMLResponse("<h1>Rejected</h1><p>Unknown or expired auth state.</p>",
                            status_code=400)

    token_path = ENV["SCHWAB_TOKEN_PATH"]

    def write_token(token, *args, **kwargs):
        with open(token_path, "w", encoding="utf-8") as fh:
            json.dump(token, fh)

    ctx = AuthContext(callback_url=flow["callback_url"],
                      authorization_url=None, state=flow["state"])
    try:
        client_from_received_url(
            os.environ["SCHWAB_APP_KEY"], os.environ["SCHWAB_APP_SECRET"],
            ctx, received_url, write_token,
        )
    except Exception as e:
        return HTMLResponse(f"<h1>Failed</h1><pre>{str(e)[:400]}</pre>", status_code=400)

    volume.commit()
    return HTMLResponse(
        "<h1>Re-authenticated</h1>"
        "<p>Schwab token refreshed and saved. Good for 7 days.</p>"
    )
