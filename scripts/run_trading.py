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

Exit codes (Task Scheduler records these as LastTaskResult):
    0  clean run
    1  halted: auth, equity, or ledger/broker reconciliation failure
    2  refused: --live without --i-am-sure
    3  halted: STOP kill-switch file present
    4  halted: ran outside the entry window (LIVE only)
    5  halted: no live quotes, so nothing could be entered or covered
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "src"))

from split_strategy import config
from split_strategy.broker.quotes import get_quotes
from split_strategy.broker.schwab_orders import OrderManager, OrderMode, RiskLimits
from split_strategy.live import calendar as mcal
from split_strategy.live import heartbeat
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


def alert_stranded_positions(args, reason: str) -> None:
    """Page if a position is due to cover today and this run will not cover it.

    Halting a mistimed or degraded run is the safe choice for *entries*, but it also
    leaves anything past its exit date sitting open, with the resting take-profit as
    the only remaining automated protection. So this has to be loud and name names.
    """
    try:
        positions = ps.load_positions(ledger_path(args.live))
        due = ps.positions_due_for_exit(positions, mcal.today_et())
    except Exception as e:
        print(f"[stranded] could not check open positions: {str(e)[:120]}")
        return
    if not due:
        return
    tickers = ", ".join(p.get("ticker", "?") for p in due)
    msg = (f"{len(due)} position(s) due to cover were NOT covered ({reason}): "
           f"{tickers}. Cover manually.")
    print(f"[stranded] {msg}")
    if not args.no_alert:
        send_text("SplitShort: UNCOVERED", msg[:140])


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
    except Exception as e:
        # Defense in depth: get_client() now validates the token itself, so this
        # shouldn't be reachable - but a raw crash here is a bad failure mode for an
        # interactive command, so surface it cleanly instead of a traceback.
        print(f"[login] unexpected error: {e}")
        return 1


def warn_if_token_expiring(quiet: bool = False) -> None:
    """Text the day before the 7-day refresh window closes."""
    from split_strategy.broker.schwab_auth import REFRESH_TOKEN_DAYS, token_age_days

    age = token_age_days()
    if age is None:
        return
    days_left = REFRESH_TOKEN_DAYS - age
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


def _session(ctx: dict) -> int:
    """The trading session proper. `ctx` carries state out for the heartbeat."""
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
    ctx["mode"] = mode.value
    ctx["heartbeat"] = True

    if check_kill_switch():
        report.halted = True
        report.halt_reason = "STOP file present in repo root"
        print(f"HALTED: {report.halt_reason}")
        write_audit(report)
        return 3

    if mode is OrderMode.LIVE and not mcal.is_trading_day(mcal.today_et()):
        print("Market closed today (weekend or holiday). Nothing to do.")
        return 0

    # --- entry timing --------------------------------------------------------------
    # Strategy B is defined as "short at the open, cover at the open". A session that
    # fires hours late is not that trade, so LIVE halts outright rather than filling at
    # a price the backtest never measured. DRY_RUN continues (tagged) so shortability
    # ground-truth collection keeps running and the tag measures how often LIVE *would*
    # have halted.
    window_ok, mins_to_open = mcal.in_entry_window(
        before_min=config.ENTRY_WINDOW_BEFORE_MIN,
        after_min=config.ENTRY_WINDOW_AFTER_MIN,
    )
    ctx["in_window"] = window_ok
    if not window_ok:
        detail = (f"ran {mcal.describe_window(mins_to_open)}; entry window is "
                  f"{config.ENTRY_WINDOW_BEFORE_MIN:.0f} min before to "
                  f"{config.ENTRY_WINDOW_AFTER_MIN:.0f} min after")
        if mode is OrderMode.LIVE:
            report.halted = True
            report.halt_reason = f"outside entry window: {detail}"
            print(f"HALTED: {report.halt_reason}")
            if not args.no_alert:
                send_text("SplitShort: HALTED (timing)", report.halt_reason[:140])
            alert_stranded_positions(args, "run halted: outside entry window")
            write_audit(report)
            return 4
        report.notes.append(f"OUT_OF_WINDOW (would halt in live): {detail}")
        print(f"[timing] OUT_OF_WINDOW (would halt in live): {detail}")

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
        # There is work to do and no market data to do it with - usually an expired
        # Schwab login. Every entry and cover would be silently skipped, so this must
        # NOT report success: it went unnoticed for six days in August because the run
        # logged a note and still exited 0.
        report.halted = True
        report.halt_reason = (f"no live quotes for {len(tickers)} ticker(s); "
                              f"entries and covers cannot proceed (check --login)")
        print(f"HALTED: {report.halt_reason}")
        if not args.no_alert:
            send_text("SplitShort: NO QUOTES", report.halt_reason[:140])
        alert_stranded_positions(args, "run halted: no live quotes")
        ps.save_positions(path, positions)
        write_audit(report)
        return 5

    limits = RiskLimits(
        max_new_shorts_per_day=config.MAX_NEW_SHORTS_PER_DAY,
        max_daily_notional=config.MAX_DAILY_NOTIONAL,
        max_htb_rate=config.MAX_HTB_RATE,
        max_spread_pct=config.MAX_SPREAD_PCT,
    )
    manager = OrderManager(mode=mode, client=client, account_hash=account_hash, limits=limits)

    # --- exits before entries -------------------------------------------------------
    # `persist` lets the session flush the ledger before an order can reach the broker,
    # so a crash mid-run cannot leave a real position unrecorded.
    def persist() -> None:
        ps.save_positions(path, positions)

    sess.process_exits(positions, manager, quotes, report, persist=persist)
    sess.attach_take_profits(positions, manager, report, TAKE_PROFIT_PCT)
    sess.process_entries(signals, positions, manager, quotes, report, TAKE_PROFIT_PCT,
                         persist=persist)

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


def main() -> int:
    """Run the session, then record a heartbeat regardless of how it ended.

    The heartbeat is what an off-machine watchdog reads. It has to be written on the
    failure paths too - a run that halted still proves the host woke up and executed,
    which is a different problem from the host never running at all.
    """
    ctx = {"mode": "DRY_RUN", "in_window": False, "heartbeat": False}
    code = _session(ctx)
    if ctx["heartbeat"]:
        err = heartbeat.record(ctx["mode"], ctx["in_window"], code)
        if err:
            print(f"[heartbeat] not recorded: {err}")
    return code


if __name__ == "__main__":
    sys.exit(main())
