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
    modal deploy modal_app.py             # deploy the scheduled scan + trading run
    modal run modal_app.py::scan          # run the EDGAR scan once (never trades)
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
#       SCHWAB_CALLBACK_URL=https://127.0.0.1:8182 OPENAI_API_KEY=...
# OPENAI_API_KEY is only read by `scan` (the LLM filing classifier).
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


# The scanner needs three packages the trading path does not. They go in their own
# image so the one that places real orders at 09:25 stays as small as it was.
scan_image = _with_source(_base.pip_install(
    "requests>=2.31.0", "beautifulsoup4>=4.12.0", "openai>=1.52.0"))


@app.function(
    image=scan_image,
    secrets=[secrets],
    # 08:15 ET, 70 minutes ahead of the trading run. A signal can only be entered on
    # the session after its filing, so the scan has to land BEFORE 09:25 - and the
    # GitHub Actions cron that used to be the only scanner started 4-8 hours late
    # through September 2026, after the run it feeds. Every signal that arrived late
    # was HOLDING by the next morning and could never be traded. GitHub cron has no
    # start-time guarantee; this does.
    schedule=modal.Cron("15 8 * * 1-5", timezone="America/New_York"),
    timeout=1800,
    retries=1,  # idempotent: hits are upserted by filing URL
)
def scan() -> int:
    """Scan EDGAR for definitive reverse-split filings and write them to MongoDB.

    Never trades and never touches the Volume. Needs OPENAI_API_KEY, MONGODB_URI and
    SEC_USER_AGENT in the secret; without the first it exits 1 and says so.
    """
    env = {**os.environ, "PYTHONPATH": "/app/src"}
    cmd = [sys.executable, "/app/scripts/scan_early_edgar.py"]
    print(f"$ {' '.join(cmd)}")
    code = subprocess.run(cmd, env=env, cwd="/app").returncode
    print(f"[modal] scan exited {code}")
    if code:
        # Raise so Modal marks the run failed (and retries once) instead of
        # recording a success that wrote nothing.
        raise RuntimeError(f"early EDGAR scan exited {code}")
    return code


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


@app.function(
    image=image,
    volumes={DATA_DIR: volume},
    secrets=[secrets],
    # 09:35 ET: after the opening cross has filled the 09:25 entries, and still inside
    # the 09:15-09:45 entry window the session enforces.
    schedule=modal.Cron("35 9 * * 1-5", timezone="America/New_York"),
    timeout=600,
    retries=0,
)
def post_open() -> int:
    """Post-open follow-up. LIVE - rests real protective orders, never enters or covers.

    Books the morning's entry fills, rests the take-profit/stop pair on each, and
    records post-open quotes for the shadow book. Without it a new short would sit
    unprotected until the next morning's run.
    """
    os.makedirs(f"{DATA_DIR}/logs", exist_ok=True)
    try:
        return _run_session(["--live", "--i-am-sure", "--followup"])
    finally:
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


@app.function(image=image, volumes={DATA_DIR: volume}, secrets=[secrets], timeout=120)
def account_snapshot(audit_tail: int = 400) -> dict:
    """Read-only state for `scripts/dashboard.py`. Places no orders, writes nothing.

    Why this runs on Modal instead of on the laptop
    -----------------------------------------------
    The dashboard wants live equity and current marks, which means a Schwab client,
    which means the OAuth token. There are two copies of that token - the canonical
    one on this Volume and a local one in the repo - and schwab-py rewrites the token
    file whenever it refreshes. Two copies of one grant, refreshed independently by
    two hosts, is the shape that produces an `invalid_grant` halt at 09:25 on a
    morning you were not expecting one. So the dashboard never authenticates: it asks
    this function, which runs on the host that already owns the token, and gets JSON
    back. Exactly one host ever refreshes.

    Deliberately no `volume.commit()`. Nothing here writes, and committing would risk
    persisting a token rewrite from a read-only call.

    Every section degrades independently into `errors` rather than raising: a dead
    Schwab session must still leave you able to see the ledger and today's audit,
    which is often exactly when you most want to look at them.
    """
    import json
    from datetime import datetime, timezone

    volume.reload()  # the container may hold a stale view of the Volume

    for key, value in ENV.items():
        os.environ.setdefault(key, value)
    sys.path.insert(0, "/app/src")

    out: dict = {"errors": [], "generated_at": datetime.now(timezone.utc).isoformat()}

    # --- ledger ------------------------------------------------------------------
    try:
        with open(f"{DATA_DIR}/open_positions_live.json", encoding="utf-8") as fh:
            out["ledger"] = json.load(fh)
    except FileNotFoundError:
        # Not an error: it means no live run has opened a position yet.
        out["ledger"] = []
    except Exception as e:
        out["ledger"] = []
        out["errors"].append(f"ledger unreadable: {str(e)[:200]}")

    # --- audit tail --------------------------------------------------------------
    out["audit"] = []
    try:
        with open(f"{DATA_DIR}/logs/trading_audit.jsonl", encoding="utf-8") as fh:
            lines = fh.readlines()[-audit_tail:]
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                out["audit"].append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a torn final line is normal if a run died mid-write
    except FileNotFoundError:
        pass
    except Exception as e:
        out["errors"].append(f"audit unreadable: {str(e)[:200]}")

    # --- kill switch -------------------------------------------------------------
    out["stop_present"] = os.path.exists(f"{DATA_DIR}/STOP")
    out["stop_trading_env"] = os.environ.get("STOP_TRADING", "").strip().lower() in (
        "1", "true", "yes")

    # --- token age ---------------------------------------------------------------
    # The refresh token is a HARD 7-day expiry that only an interactive login resets,
    # so its age is the best available predictor of the next auth halt. mtime tracks
    # the last REFRESH rather than the last login, so it is an upper bound on
    # remaining life - the login time is recorded nowhere. It errs toward warning
    # early, which is the safe direction.
    token_file = os.environ.get("SCHWAB_TOKEN_PATH", f"{DATA_DIR}/.schwab_token.json")
    out["token"] = {"path": token_file, "exists": os.path.exists(token_file)}
    if out["token"]["exists"]:
        try:
            out["token"]["mtime"] = datetime.fromtimestamp(
                os.path.getmtime(token_file), timezone.utc).isoformat()
        except Exception as e:
            out["errors"].append(f"token mtime unreadable: {str(e)[:120]}")

    # --- broker ------------------------------------------------------------------
    try:
        from split_strategy.broker.schwab_auth import get_client, resolve_account_hash
        from split_strategy.broker import accounts as acct
        from split_strategy.broker.quotes import get_quotes

        client = get_client(interactive=False)
        account_hash = resolve_account_hash(client)

        out["account"] = {
            "equity": acct.get_account_equity(client, account_hash),
            "available_funds": acct.get_available_funds(client, account_hash),
        }

        broker_positions = acct.get_broker_positions(client, account_hash) or {}
        out["broker_positions"] = {
            t: {"short_shares": p.short_shares, "long_shares": p.long_shares,
                "is_short": p.is_short}
            for t, p in broker_positions.items()
        }

        live_states = ("PENDING_ENTRY", "OPEN", "PENDING_EXIT")
        tickers = sorted({p.get("ticker") for p in out["ledger"]
                          if p.get("status") in live_states and p.get("ticker")})
        out["quotes"] = {}
        if tickers:
            for ticker, q in get_quotes(client, tickers).items():
                out["quotes"][ticker] = {"bid": q.bid, "ask": q.ask, "last": q.last,
                                         "mid": q.mid, "spread_pct": q.spread_pct}

        ledger_live = [p for p in out["ledger"] if p.get("status") in live_states]
        out["discrepancies"] = [
            {"ticker": d.ticker, "kind": d.kind, "detail": d.detail}
            for d in acct.reconcile(ledger_live, broker_positions)
        ]
    except Exception as e:
        # Includes SchwabAuthError, which is the expected state once a week.
        out["account"] = None
        out["broker_positions"] = {}
        out["quotes"] = {}
        out["discrepancies"] = []
        out["errors"].append(f"schwab unavailable: {str(e)[:300]}")

    # `modal run` does not surface a function's return value, so the caller reads
    # this line off stdout. Emitted as ONE line, because scripts/dashboard.py scans
    # stdout line by line to pick the payload out of Modal's own progress output.
    # The dict is still returned so a future `.remote()` caller gets it directly.
    print(json.dumps(out, default=str))
    return out
