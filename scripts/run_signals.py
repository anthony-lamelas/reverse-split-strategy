#!/usr/bin/env python3
"""Daily signal report — what the strategy would trade today. Places NO orders, ever.

    python scripts/run_signals.py                    # today's ranked candidates + SMS
    python scripts/run_signals.py --no-alert         # ...without texting

This is the CI-friendly half of the system: it needs only MongoDB and an internet
connection, and it is what the nightly GitHub Actions workflow runs. Because it never
constructs or submits an order, it is safe to run anywhere.

Actual trading lives in `scripts/run_trading.py`, which must run from a machine with a
persistent Schwab token and position ledger (see docs/LIVE_DEPLOYMENT.md).

If a cached Schwab token happens to be available, this also records real shortability
data to DATA/shortability_ground_truth.csv — free calibration data for the historical
proxy classifier, gathered without any trading risk.
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "src"))

from split_strategy import config
from split_strategy.signals import portfolio_state as ps
from split_strategy.signals.generate import generate_signals


def print_table(signals):
    if not signals:
        print("No actionable signals today.")
        return
    print(f"\n{'STATUS':<10}{'TKR':<8}{'CONF':<7}{'ENTRY':<12}{'EXIT':<12}"
          f"{'PRICE':>8}{'GAP%':>7}{'SHARES':>8}  SHORTABLE  CAP  NOTES")
    print("-" * 120)
    for s in signals:
        price = f"{s.current_price:.2f}" if s.current_price else "-"
        gap = f"{s.gap_up_pct:+.1f}" if s.gap_up_pct is not None else "-"
        shares = str(s.shares) if s.shares else "-"
        if s.schwab_is_shortable is not None:
            short = "YES" if s.schwab_is_shortable else "NO"
        else:
            short = "yes?" if s.likely_shortable else "no?"
        cap = "OK" if s.capital_ok else ("NO" if s.capital_ok is False else "-")
        note = s.notes[0] if s.notes else ""
        print(f"{s.status:<10}{s.ticker:<8}{(s.confidence or '')[:4]:<7}"
              f"{s.entry_date or '-':<12}{s.effective_date or '-':<12}"
              f"{price:>8}{gap:>7}{shares:>8}  {short:<9}  {cap:<3}  {note}")


def write_log(signals):
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    path = log_dir / f"signals_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(),
        "signals": [s.to_dict() for s in signals],
    }, indent=2, default=str), encoding="utf-8")
    return path


def send_text(subject: str, body: str) -> bool:
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


def main():
    ap = argparse.ArgumentParser(description="Daily reverse-split short signal report")
    ap.add_argument("--account", type=float, default=None, help="account size for sizing")
    ap.add_argument("--min-confidence", default="High", choices=["High", "Medium", "Low"])
    ap.add_argument("--lookback", type=int, default=7, help="max days since filing to still act on")
    ap.add_argument("--no-price-check", action="store_true", help="skip live price fetch")
    ap.add_argument("--no-schwab-check", action="store_true",
                    help="skip the real Schwab shortability lookup")
    ap.add_argument("--no-alert", action="store_true", help="suppress text messages")
    ap.add_argument("--max-exposure", type=float, default=None)
    args = ap.parse_args()

    # Read the live ledger (if present) purely to report committed capital accurately.
    # This script never writes to it - only run_trading.py mutates position state.
    committed = 0.0
    for name in ("open_positions_live.json", "open_positions_dryrun.json"):
        path = ROOT / "DATA" / name
        if path.exists():
            committed = ps.committed_capital(ps.load_positions(path))
            print(f"Committed capital from {name}: ${committed:,.0f}")
            break

    signals = generate_signals(
        account_size=args.account,
        min_confidence=args.min_confidence,
        lookback_days=args.lookback,
        price_check=not args.no_price_check,
        existing_committed=committed,
        max_exposure=args.max_exposure,
    )

    # Opportunistic real-shortability logging; never required, never blocks.
    if not args.no_schwab_check:
        try:
            from split_strategy.broker.schwab_auth import get_client
            client = get_client(interactive=False)
        except Exception:
            client = None
        if client is not None:
            from split_strategy.signals.generate import (enrich_with_schwab_shortability,
                                                         log_shortability_ground_truth)
            enrich_with_schwab_shortability(signals, client)
            n = log_shortability_ground_truth(
                signals, ROOT / "DATA" / "shortability_ground_truth.csv")
            if n:
                print(f"\nLogged real Schwab shortability for {n} ticker(s).")
                for s in signals:
                    if s.schwab_is_shortable is not None:
                        agree = "match" if s.schwab_is_shortable == s.likely_shortable else "MISMATCH"
                        print(f"  {s.ticker}: proxy={s.likely_shortable} "
                              f"schwab={s.schwab_is_shortable} ({agree})")
        else:
            print("\n(No cached Schwab token - skipping real shortability check.)")

    print_table(signals)
    log_path = write_log(signals)
    enter_now = [s for s in signals if s.status == "ENTER_NOW"]
    print(f"\nsignals={len(signals)} ENTER_NOW={len(enter_now)} | no orders placed "
          f"(this script never trades)")
    print(f"Log written: {log_path}")

    if enter_now and not args.no_alert:
        sent = 0
        for s in enter_now:
            if s.shares and s.current_price:
                body = f"{s.ticker}: short {s.shares}sh @ ${s.current_price:.2f}, exit {s.effective_date}"
            else:
                body = f"{s.ticker}: ENTER_NOW, no price data - check manually. Exit {s.effective_date}"
            if send_text(f"SplitShort: {s.ticker}", body):
                sent += 1
        print(f"Alert texts sent: {sent}/{len(enter_now)}")


if __name__ == "__main__":
    main()
