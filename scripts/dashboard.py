#!/usr/bin/env python3
"""One screen answering: is the system healthy, and what does it own?

    python scripts/dashboard.py

Prints once and exits. There is no watch mode and no server - this is a thing you
open, read, and close, not a thing you leave running.

Where the data comes from
-------------------------
Since the Modal migration, live state does NOT live on this laptop. The ledger and
the audit log are on the `split-strategy-data` Volume, and the local `DATA/` and
`logs/` copies are dry-run fossils from August. So this pulls a snapshot from Modal
rather than reading local files, which would render a confident lie.

It deliberately never builds a Schwab client. The OAuth refresh token exists in two
copies - the Volume's and the repo's - and schwab-py rewrites the token file on every
refresh. Two hosts refreshing one grant independently is how you get an
`invalid_grant` halt at 09:25 on a morning you were not expecting one.
`account_snapshot` runs on the Volume host, which already owns the token, and hands
back JSON. Exactly one host refreshes. See modal_app.py::account_snapshot.

Heartbeats are the exception: they are in MongoDB, which is remote for every host, so
reading them from here costs nothing and risks nothing.

If Modal is unreachable - including the case where the global CLI profile has reverted
to another workspace, which it does - the last good snapshot is rendered under a loud
age banner. Stale data is useful; stale data presented as current is not.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "src"))

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError:
    print("The `rich` package is not installed. Run: pip install rich")
    raise SystemExit(2)

import pandas as pd  # noqa: E402

from split_strategy import borrow, config, fees, modal_cli  # noqa: E402
from split_strategy.live import calendar as mcal  # noqa: E402
from split_strategy.live import heartbeat  # noqa: E402
from split_strategy.signals import portfolio_state as ps  # noqa: E402

CACHE_PATH = config.DATA_DIR / "dashboard_cache.json"
TOKEN_LIFETIME_DAYS = 7      # Schwab refresh-token hard expiry
TOKEN_WARN_WITHIN_DAYS = 2   # warn once this little life is left
REJECT_TALLY_DAYS = 14
MIN_TRADES_FOR_SIGNAL = 30   # below this, realized results are decoration

console = Console()


# ---------------------------------------------------------------------------------
# snapshot acquisition
# ---------------------------------------------------------------------------------

def pull_snapshot() -> tuple[dict | None, str]:
    """Fetch a fresh snapshot from Modal. Returns (snapshot, error_message)."""
    # modal_cli handles UTF-8 decoding, ASCII-safe output and pinning the workspace -
    # see its docstring for why each of those is load-bearing.
    proc = modal_cli.run("run", f"{ROOT / 'modal_app.py'}::account_snapshot")

    if not proc.ok:
        return None, (f"modal exited {proc.returncode}: "
                      f"{modal_cli.one_line(proc.combined)}")

    # `modal run` interleaves its own progress output with the function's return
    # value, so scan for the last line that parses as our snapshot rather than
    # assuming stdout is clean JSON.
    snap = None
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and "ledger" in candidate:
            snap = candidate
    if snap is None:
        return None, "modal ran but returned no parseable snapshot"
    return snap, ""


def load_cache() -> dict | None:
    try:
        with open(CACHE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def save_cache(snap: dict) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(snap, _cached_at=datetime.now(timezone.utc).isoformat())
        with open(CACHE_PATH, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)
    except Exception as e:
        console.print(f"[yellow]could not write cache: {str(e)[:120]}[/yellow]")


# ---------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------

def _f(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _age_days(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        ts = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0


def money(value) -> str:
    v = _f(value)
    return "?" if v is None else f"${v:,.2f}"


def price(value) -> str:
    """A share price at 4dp.

    Two decimals is the wrong precision for this book: the strategy shorts
    sub-dollar names, so $0.4988 and $0.4426 both round to $0.50 and $0.44 and the
    difference between a fill and its mark disappears entirely.
    """
    v = _f(value)
    return "?" if v is None else f"${v:,.4f}"


def signed(value) -> Text:
    """A P&L figure coloured by sign, or a dim '?' when it cannot be computed."""
    v = _f(value)
    if v is None:
        return Text("?", style="dim")
    return Text(f"{'+' if v >= 0 else '-'}${abs(v):,.2f}",
                style="green" if v >= 0 else "red")


def current_mark(quote: dict | None) -> float | None:
    """Best available price for a short we would have to BUY back to cover.

    Prefers the ask, because covering lifts the offer. Marking a short at the mid or
    the last flatters it by half the spread, and these are deliberately wide-spread
    names - the entry filter tolerates up to 5%.
    """
    if not quote:
        return None
    return _f(quote.get("ask")) or _f(quote.get("mid")) or _f(quote.get("last"))


# ---------------------------------------------------------------------------------
# P&L
# ---------------------------------------------------------------------------------

def position_pnl(pos: dict, mark: float | None) -> dict:
    """Gross and net P&L for one short.

    Short P&L is (entry - exit) * shares: sold high, bought back lower. Costs are
    borrow, which accrues per day held, plus the regulatory fees paid once on the
    entry sell (see fees.py - they land on the sell, and a short's sell is its
    entry). Any unknown input yields None rather than a zero, so a missing quote can
    never masquerade as a flat position.
    """
    shares = _f(pos.get("filled_shares")) or _f(pos.get("shares"))
    entry = _f(pos.get("entry_fill_price"))
    out: dict = {"gross": None, "net": None, "borrow": None, "fees": None,
                 "days": None}
    if shares is None or entry is None:
        return out

    out["fees"] = fees.entry_fees(shares, entry)

    # Both ends must be naive to match `entry_date`'s bare "YYYY-MM-DD": now_et() is
    # tz-aware, and borrow.holding_days subtracts the two Timestamps outside its own
    # try/except, so mixing them raises rather than returning None. Truncating to the
    # date is also the right granularity - borrow accrues per calendar day.
    end = str(pos.get("closed_at") or mcal.today_et().isoformat())[:10]
    out["days"] = borrow.holding_days(str(pos.get("entry_date") or "")[:10], end)

    rate = pos.get("current_htb_rate")
    if rate is None:
        rate = pos.get("entry_htb_rate")
    cost_pct = borrow.expected_cost_pct(rate, out["days"]) if rate is not None else None
    if cost_pct is not None:
        out["borrow"] = cost_pct * shares * entry

    if mark is not None:
        out["gross"] = (entry - mark) * shares
        out["net"] = out["gross"] - (out["borrow"] or 0.0) - (out["fees"] or 0.0)
    return out


# ---------------------------------------------------------------------------------
# sections
# ---------------------------------------------------------------------------------

def build_alerts(snap: dict, beats_verdict: tuple[int, str] | None) -> list[Text]:
    alerts: list[Text] = []

    if snap.get("stop_present") or snap.get("stop_trading_env"):
        alerts.append(Text("TRADING HALTED - kill switch is set. The next scheduled "
                           "run will exit 3 and place no orders.", style="bold red"))

    token = snap.get("token") or {}
    if not token.get("exists"):
        alerts.append(Text("No Schwab token on the Volume - the next run cannot "
                           "authenticate.", style="bold red"))
    else:
        age = _age_days(token.get("mtime"))
        if age is not None:
            left = TOKEN_LIFETIME_DAYS - age
            if left <= 0:
                alerts.append(Text(
                    f"Schwab token last written {age:.1f}d ago - the 7-day refresh "
                    f"token has likely EXPIRED. Re-login: "
                    f"python scripts/run_trading.py --login", style="bold red"))
            elif left <= TOKEN_WARN_WITHIN_DAYS:
                alerts.append(Text(
                    f"Schwab token has ~{left:.1f}d left at most. Re-login soon: "
                    f"python scripts/run_trading.py --login", style="bold yellow"))

    if beats_verdict is not None and beats_verdict[0] != heartbeat.OK:
        alerts.append(Text(beats_verdict[1], style="bold red"))

    for d in snap.get("discrepancies") or []:
        alerts.append(Text(
            f"LEDGER/BROKER MISMATCH {d.get('ticker')}: {d.get('kind')} - "
            f"{d.get('detail')}", style="bold red"))

    for pos in ps.live_positions(snap.get("ledger") or []):
        if borrow.has_spiked(pos.get("entry_htb_rate"), pos.get("current_htb_rate"),
                             absolute_ceiling=config.BORROW_ALERT_RATE,
                             multiple=config.BORROW_ALERT_MULTIPLE):
            entry_rate = _f(pos.get("entry_htb_rate"))
            current = abs(_f(pos.get("current_htb_rate")) or 0.0)
            was = "unknown" if entry_rate is None else f"{abs(entry_rate):.0f}%"
            alerts.append(Text(
                f"BORROW SPIKE {pos.get('ticker')}: {current:.0f}%/yr now, "
                f"{was} at entry.", style="bold yellow"))

    for err in snap.get("errors") or []:
        alerts.append(Text(str(err), style="yellow"))

    return alerts


def render_session(snap: dict) -> None:
    audit = snap.get("audit") or []
    if not audit:
        console.print("[dim]No audit entries on the Volume yet.[/dim]\n")
        return

    today = mcal.today_et().strftime("%Y-%m-%d")
    todays = [a for a in audit if str(a.get("timestamp", "")).startswith(today)]
    latest = (todays or audit)[-1]
    is_today = bool(todays)

    header = (f"ran {latest.get('timestamp', '?')}   "
              f"mode={latest.get('mode', '?')}   "
              f"equity={money(latest.get('account_equity'))}")
    if latest.get("halted"):
        header += f"   HALTED: {latest.get('halt_reason')}"
    console.print(Panel(header,
                        title="Today's session" if is_today
                              else "Last session (NOT today)",
                        border_style="cyan" if is_today else "yellow"))

    for note in latest.get("notes") or []:
        console.print(f"  [yellow]note:[/yellow] {note}")

    rows = (latest.get("entries") or []) + (latest.get("exits") or [])
    if not rows:
        console.print("  [dim]no candidates evaluated[/dim]\n")
        return

    table = Table(box=None, pad_edge=False, header_style="bold")
    for col in ("Ticker", "Side", "Qty", "Outcome", "Why"):
        table.add_column(col)
    for r in rows:
        outcome = str(r.get("outcome", "?"))
        style = {"FILLED": "green", "SUBMITTED": "green",
                 "SKIPPED": "dim", "REJECTED": "red"}.get(outcome, "")
        table.add_row(str(r.get("ticker", "?")), str(r.get("side", "")),
                      str(r.get("quantity", "")), Text(outcome, style=style),
                      str(r.get("detail", "")))
    console.print(table)
    console.print()


def _reject_bucket(detail: str) -> str:
    d = (detail or "").lower()
    if "floor" in d:
        return "below $1.00 price floor"
    if "spread" in d:
        return "spread too wide"
    if "borrow" in d or "htb" in d:
        return "borrow cost / rate"
    if "shortab" in d or "not shortable" in d:
        return "not shortable"
    if "quantity" in d or "price" in d:
        return "no quantity (missing price)"
    if "notional" in d or "limit" in d or "max" in d:
        return "risk limit reached"
    return detail[:48] if detail else "unspecified"


def render_reject_tally(snap: dict) -> None:
    """Why candidates were turned away recently, grouped by reason.

    The point is to catch one filter quietly eating the whole funnel. The $1.00
    price floor removed 24% of candidates over the trailing year, and a drift in
    that share is invisible when you only ever see one morning at a time.
    """
    audit = snap.get("audit") or []
    if not audit:
        return

    cutoff = (mcal.today_et() - pd.Timedelta(days=REJECT_TALLY_DAYS)
              ).strftime("%Y-%m-%d")

    counts: dict[str, int] = {}
    seen_days: set[str] = set()
    for a in audit:
        day = str(a.get("timestamp", ""))[:10]
        if day < cutoff:
            continue
        seen_days.add(day)
        for e in a.get("entries") or []:
            if str(e.get("outcome")) in ("SKIPPED", "REJECTED"):
                key = _reject_bucket(str(e.get("detail", "")))
                counts[key] = counts.get(key, 0) + 1

    if not counts:
        return

    total = sum(counts.values())
    table = Table(title=f"Rejects, last {REJECT_TALLY_DAYS}d "
                        f"({len(seen_days)} session-days, {total} skips)",
                  box=None, title_justify="left", header_style="bold",
                  pad_edge=False)
    table.add_column("Reason")
    table.add_column("Count", justify="right")
    table.add_column("Share", justify="right")
    for reason, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        table.add_row(reason, str(n), f"{100 * n / total:.0f}%")
    console.print(table)
    console.print()


def render_open_book(snap: dict) -> None:
    live = ps.live_positions(snap.get("ledger") or [])
    if not live:
        console.print("[dim]No open positions.[/dim]\n")
        return

    quotes = snap.get("quotes") or {}
    table = Table(title="Open short book", box=None, title_justify="left",
                  header_style="bold", pad_edge=False)
    for col, justify in (("Ticker", "left"), ("Status", "left"), ("Shares", "right"),
                         ("Entry", "right"), ("Mark", "right"), ("Days", "right"),
                         ("Exit by", "left"), ("Borrow", "right"),
                         ("Gross", "right"), ("Net", "right")):
        # no_wrap keeps an ISO date from being ellipsised into ambiguity on a narrow
        # terminal - a half-printed exit date is worse than a wrapped table.
        table.add_column(col, justify=justify, no_wrap=(col == "Exit by"),
                         min_width=10 if col == "Exit by" else None)

    tot_gross = tot_net = 0.0
    have_any = False
    for pos in live:
        mark = current_mark(quotes.get(pos.get("ticker")))
        pnl = position_pnl(pos, mark)
        rate = _f(pos.get("current_htb_rate"))
        if pnl["gross"] is not None:
            have_any = True
            tot_gross += pnl["gross"]
            tot_net += pnl["net"]
        shares = _f(pos.get("filled_shares")) or _f(pos.get("shares")) or 0.0
        table.add_row(
            str(pos.get("ticker", "?")),
            str(pos.get("status", "")),
            f"{shares:,.0f}",
            price(pos.get("entry_fill_price")),
            price(mark),
            "?" if pnl["days"] is None else str(pnl["days"]),
            str(pos.get("planned_exit_date", "")),
            "?" if rate is None else f"{abs(rate):.0f}%",
            signed(pnl["gross"]),
            signed(pnl["net"]),
        )
    if have_any:
        table.add_section()
        table.add_row("TOTAL", "", "", "", "", "", "", "",
                      signed(tot_gross), signed(tot_net))
    console.print(table)
    console.print(f"[dim]Marked at the ask (what covering costs). Net = gross - "
                  f"borrow - {fees.verify_rates_note()}.[/dim]\n")


def render_realized(snap: dict) -> None:
    """Closed trades since going live.

    Only rows with BOTH fill prices count. `mark_entry_rejected()` also sets
    status=CLOSED, so a naive status filter would report never-opened rejects as
    completed trades - each one a phantom scratch that drags the average toward
    zero and inflates the trade count.
    """
    closed = [p for p in (snap.get("ledger") or [])
              if p.get("status") == ps.CLOSED
              and _f(p.get("entry_fill_price")) is not None
              and _f(p.get("exit_fill_price")) is not None]

    if not closed:
        console.print("[dim]No completed live trades yet.[/dim]\n")
        return

    table = Table(title="Realized (live fills only)", box=None, title_justify="left",
                  header_style="bold", pad_edge=False)
    for col, justify in (("Ticker", "left"), ("Entry", "right"), ("Exit", "right"),
                         ("Shares", "right"), ("Days", "right"),
                         ("Gross", "right"), ("Net", "right")):
        table.add_column(col, justify=justify)

    tot_gross = tot_net = 0.0
    wins = 0
    for pos in sorted(closed, key=lambda p: str(p.get("closed_at") or "")):
        pnl = position_pnl(pos, _f(pos.get("exit_fill_price")))
        if pnl["gross"] is not None:
            tot_gross += pnl["gross"]
            tot_net += pnl["net"]
            wins += 1 if pnl["net"] > 0 else 0
        table.add_row(
            str(pos.get("ticker", "?")),
            price(pos.get("entry_fill_price")),
            price(pos.get("exit_fill_price")),
            f"{_f(pos.get('filled_shares')) or 0:,.0f}",
            "?" if pnl["days"] is None else str(pnl["days"]),
            signed(pnl["gross"]),
            signed(pnl["net"]),
        )
    table.add_section()
    table.add_row("TOTAL", "", "", "", "", signed(tot_gross), signed(tot_net))
    console.print(table)

    n = len(closed)
    console.print(f"[dim]{n} completed trade(s), {wins} net-positive.[/dim]")
    if n < MIN_TRADES_FOR_SIGNAL:
        console.print("[yellow]Far too few trades to read anything into. The "
                      "backtested edge rests on hundreds; a sample this size has no "
                      "statistical content.[/yellow]")
    console.print()


# ---------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------

def read_heartbeat() -> tuple[int, str] | None:
    """Today's watchdog verdict from MongoDB, or None when nothing is expected."""
    day = mcal.today_et().strftime("%Y-%m-%d")
    if not mcal.is_trading_day(day):
        return None
    try:
        from split_strategy.database import get_collection
        beats = list(get_collection(heartbeat.COLLECTION).find({"date": day}))
    except Exception as e:
        return heartbeat.UNREACHABLE, f"could not read heartbeats: {str(e)[:150]}"
    return heartbeat.evaluate(beats, day)


def main() -> int:
    argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()

    snap, err = pull_snapshot()
    stale_note = None
    if snap is not None:
        save_cache(snap)
    else:
        snap = load_cache()
        if snap is None:
            console.print(Panel(
                f"Could not reach Modal, and no cached snapshot exists.\n\n{err}\n\n"
                f"Workspace used: {config.MODAL_PROFILE} (pinned per-run; your global "
                f"Modal profile is not touched). If that workspace is wrong, set "
                f"MODAL_PROFILE in .env.",
                title="No data", border_style="red"))
            return 2
        age = _age_days(snap.get("_cached_at"))
        age_txt = "age unknown" if age is None else f"{age:.1f} DAYS OLD"
        stale_note = f"SHOWING CACHED DATA - {age_txt}. Live pull failed: {err}"

    if stale_note:
        console.print(Panel(Text(stale_note, style="bold white on red"),
                            border_style="red"))

    alerts = build_alerts(snap, read_heartbeat())
    if alerts:
        console.print(Panel(Text("\n").join(alerts), title="Attention",
                            border_style="red"))
    else:
        console.print(Panel(Text("All clear.", style="green"), title="Attention",
                            border_style="green"))
    console.print()

    account = snap.get("account") or {}
    console.print(f"Equity {money(account.get('equity'))}    "
                  f"Available funds {money(account.get('available_funds'))}\n")

    render_session(snap)
    render_reject_tally(snap)
    render_open_book(snap)
    render_realized(snap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
