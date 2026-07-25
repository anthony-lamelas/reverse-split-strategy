#!/usr/bin/env python3
"""Daily signal run for the reverse-split short strategy.

Modes:
  python scripts/run_signals.py                 # DRY-RUN: generate signals + log would-be orders
  python scripts/run_signals.py --login         # one-time (weekly) Schwab OAuth login
  python scripts/run_signals.py --live          # LIVE: submit real short orders (gated; use with care)

DRY-RUN needs only MongoDB (for signals) and internet (for the gap-up price check).
It never contacts Schwab. --login and --live require Schwab credentials in .env
(see docs/SCHWAB_SETUP.md). Because Schwab refresh tokens expire every 7 days, --login
must be re-run about weekly; fully unattended live trading is not possible on Schwab.
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "src"))

from split_strategy import config
from split_strategy.signals.generate import generate_signals
from split_strategy.broker.schwab_orders import OrderManager, OrderMode


def _print_table(signals):
    if not signals:
        print("No actionable signals today.")
        return
    cols = ["status", "ticker", "conf", "entry", "exit", "price", "gap%", "shares", "stop", "shortable", "notes"]
    print(f"\n{'STATUS':<10}{'TKR':<8}{'CONF':<7}{'ENTRY':<12}{'EXIT':<12}{'PRICE':>8}{'GAP%':>7}{'SHARES':>8}{'STOP':>9}  SHORTABLE  NOTES")
    print("-" * 120)
    for s in signals:
        price = f"{s.current_price:.2f}" if s.current_price else "-"
        gap = f"{s.gap_up_pct:+.1f}" if s.gap_up_pct is not None else "-"
        shares = str(s.shares) if s.shares else "-"
        stop = f"{s.stop_price:.2f}" if s.stop_price else "-"
        short = "yes" if s.likely_shortable else "NO"
        note = s.notes[0] if s.notes else ""
        print(f"{s.status:<10}{s.ticker:<8}{(s.confidence or '')[:4]:<7}{s.entry_date or '-':<12}"
              f"{s.effective_date or '-':<12}{price:>8}{gap:>7}{shares:>8}{stop:>9}  {short:<9}  {note}")


def _write_log(signals, order_results, mode):
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = log_dir / f"signals_{stamp}.json"
    payload = {
        "generated_at": datetime.now().isoformat(),
        "mode": mode,
        "signals": [s.to_dict() for s in signals],
        "orders": [r.to_dict() for r in order_results],
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def _maybe_email(subject, body):
    if not (config.SMTP_HOST and config.SMTP_USER and config.SMTP_PASSWORD and config.ALERT_EMAIL_TO):
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
        print(f"[alert] email failed: {e}")
        return False


def do_login():
    from split_strategy.broker.schwab_auth import get_client, SchwabAuthError
    try:
        client = get_client(interactive=True)
        # sanity check
        resp = client.get_account_numbers()
        ok = resp.status_code < 400
        print(f"Login OK. Token cached at {config.SCHWAB_TOKEN_PATH}. Account call: HTTP {resp.status_code}")
        print("Note: this token's refresh window lasts ~7 days; re-run --login weekly.")
        return 0 if ok else 1
    except SchwabAuthError as e:
        print(f"[login] {e}")
        return 1


def main():
    ap = argparse.ArgumentParser(description="Daily reverse-split short signal run")
    ap.add_argument("--login", action="store_true", help="run the one-time/weekly Schwab OAuth login and exit")
    ap.add_argument("--live", action="store_true", help="submit REAL short orders (default is dry-run)")
    ap.add_argument("--account", type=float, default=None, help="account size for position sizing")
    ap.add_argument("--min-confidence", default="High", choices=["High", "Medium", "Low"])
    ap.add_argument("--lookback", type=int, default=7, help="max days since filing to still act on")
    ap.add_argument("--no-price-check", action="store_true", help="skip live price fetch (no gap-up filter/sizing)")
    args = ap.parse_args()

    if args.login:
        sys.exit(do_login())

    signals = generate_signals(
        account_size=args.account,
        min_confidence=args.min_confidence,
        lookback_days=args.lookback,
        price_check=not args.no_price_check,
    )
    _print_table(signals)

    # Order construction
    mode = OrderMode.LIVE if args.live else OrderMode.DRY_RUN
    client = account_hash = None
    if mode == OrderMode.LIVE:
        from split_strategy.broker.schwab_auth import get_client, resolve_account_hash, SchwabAuthError
        try:
            client = get_client(interactive=False)
            account_hash = resolve_account_hash(client)
        except SchwabAuthError as e:
            print(f"\n[live] cannot trade: {e}")
            sys.exit(1)

    mgr = OrderManager(mode=mode, client=client, account_hash=account_hash)
    actionable = [s for s in signals if s.status in ("ENTER_NOW", "HOLDING")]
    for s in actionable:
        res = mgr.process(s)
        print(f"  [{res.outcome}] {res.ticker}: {res.detail}")

    log_path = _write_log(signals, mgr.results, mode.value)
    summ = mgr.summary()
    print(f"\nMode={mode.value} | signals={len(signals)} actionable={len(actionable)} | orders={summ}")
    print(f"Log written: {log_path}")

    # Alert (optional)
    enter_now = [s for s in signals if s.status == "ENTER_NOW"]
    if enter_now:
        body = "Reverse-split short signals (ENTER_NOW):\n" + "\n".join(
            f"- {s.ticker} short ~{s.shares}sh @ ~${s.current_price} exit {s.effective_date} "
            f"({'shortable' if s.likely_shortable else 'UNSHORTABLE?'})" for s in enter_now)
        if _maybe_email(f"[SplitShort] {len(enter_now)} signal(s) {datetime.now():%Y-%m-%d}", body):
            print("Alert email sent.")


if __name__ == "__main__":
    main()
