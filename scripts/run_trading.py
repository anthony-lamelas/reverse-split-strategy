#!/usr/bin/env python3
"""Live trading entrypoint: reconcile -> exit -> enter.

    python scripts/run_trading.py                # DRY-RUN (default, never contacts Schwab for orders)
    python scripts/run_trading.py --login        # weekly Schwab OAuth login
    python scripts/run_trading.py --live --i-am-sure   # REAL ORDERS

Intended to run once per weekday shortly before the open (~9:25am ET) from a machine
that stays awake — see docs/LIVE_DEPLOYMENT.md. It cannot run unattended in CI because
Schwab refresh tokens expire every 7 days and require an interactive browser login.

Going live requires BOTH `--live` and `--i-am-sure`, so a stray flag in a scheduled
task can never place real orders.
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "src"))

from split_strategy import config
from split_strategy.broker.quotes import get_quotes
from split_strategy.broker.schwab_orders import OrderManager, OrderMode, RiskLimits
from split_strategy.live import calendar as mcal
from split_strategy.live import session as sess
from split_strategy.signals import portfolio_state as ps
from split_strategy.signals.generate import generate_signals

TAKE_PROFIT_PCT = 0.20  # Strategy B


def ledger_path(live: bool) -> Path:
    return ROOT / "DATA" / f"open_positions_{'live' if live else 'dryrun'}.json"


def audit_path() -> Path:
    return ROOT / "logs" / "trading_audit.jsonl"


def write_audit(report: sess.SessionReport) -> None:
    path = audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = dict(timestamp=mcal.now_et().isoformat(), **vars(report))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, default=str) + "\n")


def send_text(subject: str, body: str) -> bool:
    """One short SMS per event (carrier gateway truncates long messages)."""
    if not (config.SMTP_HOST and config.SMTP_USER and config.SMTP_PASSWORD
            and config.ALERT_EMAIL_TO):
        return False
    import smtplib
    from email.mime.text import MIMEText

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = config.SMTP_USER
    msg["To"] = config.ALERT_EMAIL_TO
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
            server.starttls()
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"[alert] send failed: {e}")
        return False


def check_kill_switch() -> bool:
    """A STOP file in the repo root halts trading without touching code or config."""
    return (ROOT / "STOP").exists()


def do_login() -> int:
    from split_strategy.broker.schwab_auth import SchwabAuthError, get_client

    try:
        client = get_client(interactive=True)
        resp = client.get_account_numbers()
        print(f"Login OK. Token cached at {config.SCHWAB_TOKEN_PATH}. "
              f"Account call: HTTP {resp.status_code}")
        print("Schwab refresh tokens last ~7 days; re-run --login weekly.")
        return 0 if resp.status_code < 400 else 1
    except SchwabAuthError as e:
        print(f"[login] {e}")
        return 1


def token_age_days() -> float | None:
    path = Path(config.SCHWAB_TOKEN_PATH)
    if not path.exists():
        return None
    age = pd.Timestamp.now() - pd.Timestamp(path.stat().st_mtime, unit="s")
    return age.total_seconds() / 86400.0


def warn_if_token_expiring(quiet: bool = False) -> None:
    """Text the day before the 7-day refresh window closes."""
    age = token_age_days()
    if age is None:
        return
    days_left = 7.0 - age
    if 0 < days_left <= 2:
        msg = f"Schwab login expires in ~{days_left:.1f}d. Run: run_trading.py --login"
        print(f"[token] {msg}")
        if not quiet:
            send_text("SplitShort: re-auth needed", msg)
    elif days_left <= 0:
        msg = "Schwab login EXPIRED. Trading paused until you run --login"
        print(f"[token] {msg}")
        if not quiet:
            send_text("SplitShort: login expired", msg)


def main() -> int:
    ap = argparse.ArgumentParser(description="Reverse-split short trading session")
    ap.add_argument("--login", action="store_true", help="run the weekly Schwab OAuth login and exit")
    ap.add_argument("--live", action="store_true", help="enable REAL order submission")
    ap.add_argument("--i-am-sure", action="store_true",
                    help="required alongside --live; prevents accidental live trading")
    ap.add_argument("--account", type=float, default=None,
                    help="override account equity instead of reading it from Schwab")
    ap.add_argument("--min-confidence", default="High", choices=["High", "Medium", "Low"])
    ap.add_argument("--no-alert", action="store_true", help="suppress text messages")
    args = ap.parse_args()

    if args.login:
        return do_login()

    if args.live and not args.i_am_sure:
        print("Refusing to trade live without --i-am-sure. No orders were placed.")
        return 2

    mode = OrderMode.LIVE if args.live else OrderMode.DRY_RUN
    report = sess.SessionReport(mode=mode.value)

    if check_kill_switch():
        report.halted = True
        report.halt_reason = "STOP file present in repo root"
        print(f"HALTED: {report.halt_reason}")
        write_audit(report)
        return 3

    if mode is OrderMode.LIVE and not mcal.is_trading_day(mcal.today_et()):
        print("Market closed today (weekend or holiday). Nothing to do.")
        return 0

    # --- auth -------------------------------------------------------------------
    client = account_hash = None
    if mode is OrderMode.LIVE:
        from split_strategy.broker.schwab_auth import (SchwabAuthError, get_client,
                                                       resolve_account_hash)
        try:
            client = get_client(interactive=False)
            account_hash = resolve_account_hash(client)
        except SchwabAuthError as e:
            report.halted = True
            report.halt_reason = f"auth failed: {e}"
            print(f"HALTED: {report.halt_reason}")
            if not args.no_alert:
                send_text("SplitShort: login expired", "Schwab auth failed. Run --login.")
            write_audit(report)
            return 1
    else:
        # Dry-run still uses a cached token opportunistically for real quotes and
        # shortability, but never for orders.
        try:
            from split_strategy.broker.schwab_auth import get_client
            client = get_client(interactive=False)
            account_hash = None
        except Exception:
            client = None

    warn_if_token_expiring(quiet=args.no_alert)

    # --- equity -----------------------------------------------------------------
    if args.account is not None:
        equity = args.account
    elif mode is OrderMode.LIVE:
        from split_strategy.broker import accounts as acct
        equity = acct.get_account_equity(client, account_hash)
        if equity is None:
            report.halted = True
            report.halt_reason = "could not read account equity from Schwab"
            print(f"HALTED: {report.halt_reason}")
            write_audit(report)
            return 1
    else:
        equity = config.DEFAULT_ACCOUNT_SIZE
    report.account_equity = equity

    # --- ledger + reconciliation -------------------------------------------------
    path = ledger_path(args.live)
    positions = ps.load_positions(path)

    if not sess.reconcile_and_sync(positions, client, account_hash, mode, report):
        ps.save_positions(path, positions)
        print(f"HALTED: {report.halt_reason}")
        for note in report.notes:
            print(f"  {note}")
        if not args.no_alert:
            send_text("SplitShort: HALTED", report.halt_reason[:140])
        write_audit(report)
        return 1

    committed = ps.committed_capital(positions)
    print(f"Equity ${equity:,.0f} | committed ${committed:,.0f} "
          f"across {len(ps.live_positions(positions))} position(s) | mode={mode.value}")

    # --- signals ------------------------------------------------------------------
    signals = generate_signals(
        account_size=equity,
        min_confidence=args.min_confidence,
        existing_committed=committed,
        price_check=True,
    )

    if client is not None:
        from split_strategy.signals.generate import (enrich_with_schwab_shortability,
                                                     log_shortability_ground_truth)
        enrich_with_schwab_shortability(signals, client)
        log_shortability_ground_truth(signals, ROOT / "DATA" / "shortability_ground_truth.csv")

    # --- quotes -------------------------------------------------------------------
    tickers = sess.collect_quote_tickers(signals, positions)
    quotes = get_quotes(client, tickers) if client is not None else {}
    if tickers and not quotes:
        report.notes.append("no live quotes available; entries and covers will be skipped")

    limits = RiskLimits(
        max_new_shorts_per_day=config.MAX_NEW_SHORTS_PER_DAY,
        max_daily_notional=config.MAX_DAILY_NOTIONAL,
        max_htb_rate=config.MAX_HTB_RATE,
        max_spread_pct=config.MAX_SPREAD_PCT,
    )
    manager = OrderManager(mode=mode, client=client, account_hash=account_hash, limits=limits)

    # --- exits before entries -------------------------------------------------------
    sess.process_exits(positions, manager, quotes, report)
    sess.attach_take_profits(positions, manager, report, TAKE_PROFIT_PCT)
    sess.process_entries(signals, positions, manager, quotes, report, TAKE_PROFIT_PCT)

    positions = ps.prune_archive(positions)
    ps.save_positions(path, positions)
    write_audit(report)

    # --- report -----------------------------------------------------------------
    print(f"\n{report.summary_line()}")
    for result in report.exits:
        print(f"  [EXIT  {result['outcome']}] {result['ticker']}: {result['detail']}")
    for result in report.entries:
        print(f"  [ENTRY {result['outcome']}] {result['ticker']}: {result['detail']}")
    for note in report.notes:
        print(f"  - {note}")

    if not args.no_alert:
        placed = [r for r in report.entries
                  if r["outcome"] in ("SUBMITTED", "WOULD_PLACE")]
        for r in placed:
            send_text(f"SplitShort: {r['ticker']}",
                      f"{r['ticker']}: short {r['quantity']} @ limit {r['limit_price']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
