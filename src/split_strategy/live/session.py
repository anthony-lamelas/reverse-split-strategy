"""The trading session: reconcile, then exit, then enter.

Ordering is deliberate and safety-critical:

1. **Reconcile first.** If the ledger disagrees with Schwab we halt entirely. Trading
   on a wrong picture of what we hold is worse than not trading.
2. **Exits before entries.** Covering frees capital and closes risk; if the run dies
   halfway, it should die with fewer open positions, not more.
3. **Entries last**, and only for `ENTER_NOW` signals with no existing position.

A take-profit is attached as a resting GTC cover as soon as an entry fills, so the
profit target survives the bot being offline. On the exit date the resting order is
**cancelled before** the covering order is sent — filling both would flip the position
long.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from ..broker import accounts as acct
from ..broker.quotes import Quote, get_quotes
from ..broker.schwab_orders import OrderManager, OrderMode, Outcome
from ..signals import portfolio_state as ps
from . import calendar as mcal


@dataclass
class SessionReport:
    mode: str
    halted: bool = False
    halt_reason: str = ""
    account_equity: Optional[float] = None
    discrepancies: list = field(default_factory=list)
    exits: list = field(default_factory=list)
    entries: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def summary_line(self) -> str:
        if self.halted:
            return f"HALTED ({self.halt_reason})"
        return (f"mode={self.mode} equity="
                f"{'?' if self.account_equity is None else f'${self.account_equity:,.0f}'} "
                f"exits={len(self.exits)} entries={len(self.entries)}")


def reconcile_and_sync(
    positions: list[dict],
    client,
    account_hash: str,
    mode: OrderMode,
    report: SessionReport,
) -> bool:
    """Confirm the ledger matches Schwab. Returns False if the session must halt.

    In DRY_RUN there is no broker state to compare against, so this advances the
    simulated lifecycle instead.
    """
    if mode is OrderMode.DRY_RUN:
        _, filled, closed = ps.simulate_lifecycle(positions, mcal.today_et())
        if filled or closed:
            report.notes.append(f"dry-run lifecycle: {filled} filled, {closed} closed")
        return True

    broker_positions = acct.get_broker_positions(client, account_hash)
    if broker_positions is None:
        report.halted = True
        report.halt_reason = "could not read positions from Schwab"
        return False

    # Update PENDING_* records from real order status before comparing.
    _settle_pending(positions, client, account_hash, report)

    issues = acct.reconcile(positions, broker_positions)
    report.discrepancies = [vars(i) for i in issues]
    if issues:
        report.halted = True
        report.halt_reason = (
            f"{len(issues)} ledger/broker discrepancy(ies): "
            + "; ".join(f"{i.ticker} {i.kind}" for i in issues[:5])
        )
        return False

    malformed = ps.malformed_positions(positions)
    if malformed:
        report.halted = True
        report.halt_reason = (
            f"{len(malformed)} position(s) have an unusable exit date and cannot be "
            f"scheduled to cover: " + ", ".join(p.get("ticker", "?") for p in malformed)
        )
        return False

    # A cover was written ahead but never got an order id back, so we cannot tell
    # whether it reached the broker. Guessing either way is bad - covering again flips
    # us long - so stop and let a human look.
    ambiguous = [p for p in ps.live_positions(positions)
                 if p.get("status") == ps.PENDING_EXIT and not p.get("exit_order_id")]
    if ambiguous:
        report.halted = True
        report.halt_reason = (
            "cover may have been submitted without being recorded for "
            + ", ".join(p.get("ticker", "?") for p in ambiguous)
            + "; check the broker before re-running (covering twice would go long)"
        )
        return False
    return True


def _settle_pending(positions, client, account_hash, report: SessionReport) -> None:
    """Resolve PENDING_ENTRY / PENDING_EXIT records against real order status.

    Records the *actual* fill price and quantity. The old code booked the full intended
    notional the moment an order was submitted, so partial fills and open-rejections
    corrupted the ledger in both directions.
    """
    for pos in list(ps.live_positions(positions)):
        status = pos.get("status")
        if status == ps.PENDING_ENTRY and pos.get("entry_order_id"):
            info = acct.get_order_status(client, account_hash, pos["entry_order_id"])
            if not info:
                continue
            if info["is_filled"]:
                ps.mark_entry_filled(pos, info["avg_fill_price"] or pos.get("entry_fill_price") or 0.0,
                                     int(info["filled_quantity"]))
                report.notes.append(
                    f"{pos['ticker']}: entry filled {info['filled_quantity']:g} "
                    f"@ {info['avg_fill_price']}"
                )
            elif info["is_dead"]:
                ps.mark_entry_rejected(pos, positions, reason=f"entry {info['status'].lower()}")
                report.notes.append(f"{pos['ticker']}: entry {info['status'].lower()}, released")
            elif info["filled_quantity"] > 0:
                # Partially filled and still working: treat what filled as the position.
                ps.mark_entry_filled(pos, info["avg_fill_price"] or 0.0,
                                     int(info["filled_quantity"]))
                report.notes.append(f"{pos['ticker']}: partial fill "
                                    f"{info['filled_quantity']:g}")
        elif status == ps.PENDING_EXIT and pos.get("exit_order_id"):
            info = acct.get_order_status(client, account_hash, pos["exit_order_id"])
            if not info:
                continue
            if info["is_filled"]:
                ps.mark_closed(pos, info["avg_fill_price"], reason="covered")
                report.notes.append(f"{pos['ticker']}: cover filled @ {info['avg_fill_price']}")
            elif info["is_dead"]:
                # Cover died without filling - we are still short, so go back to OPEN
                # and let today's exit pass retry it.
                pos["status"] = ps.OPEN
                pos["exit_order_id"] = None
                report.notes.append(f"{pos['ticker']}: cover {info['status'].lower()}, will retry")


def _flush_ledger(persist, report: SessionReport, ticker: str) -> bool:
    """Force the ledger to disk before an order can reach the broker.

    The ledger is normally saved once, at the end of a run. That is fine when the run
    completes, but it means a crash between "order sent" and "order recorded" leaves a
    real position with no ledger row - and the next run, which now repeats every few
    minutes across the entry window, would open a second one. Returns False if the
    write failed, in which case the caller must NOT submit.
    """
    if persist is None:
        return True
    try:
        persist()
        return True
    except Exception as e:
        report.notes.append(
            f"{ticker}: could not persist ledger before submitting ({str(e)[:80]}); "
            f"skipping to avoid an unrecorded order"
        )
        return False


def process_exits(
    positions: list[dict],
    manager: OrderManager,
    quotes: dict[str, Quote],
    report: SessionReport,
    as_of: Optional[pd.Timestamp] = None,
    persist=None,
) -> None:
    """Cover every OPEN position whose exit date has arrived."""
    as_of = mcal.today_et() if as_of is None else as_of
    for pos in ps.positions_due_for_exit(positions, as_of):
        ticker = pos["ticker"]
        shares = int(pos.get("filled_shares") or pos.get("shares") or 0)
        if shares <= 0:
            report.notes.append(f"{ticker}: due to exit but share count unknown; skipping")
            continue

        # Cancel the resting take-profit FIRST. If both filled we would buy twice and
        # end up long, so a failed cancel means we do not send the cover.
        tp_id = pos.get("tp_order_id")
        if tp_id:
            cancel = manager.cancel(tp_id, ticker)
            if cancel.outcome in (Outcome.REJECTED.value, Outcome.UNCERTAIN.value):
                report.notes.append(
                    f"{ticker}: could NOT cancel take-profit {tp_id} ({cancel.detail}); "
                    f"skipping cover to avoid double-covering into a long position"
                )
                continue
            ps.attach_take_profit(pos, None)

        # Write-ahead the cover intent. A crash between sending the cover and recording
        # it used to leave the position OPEN, so the next run would cover a second time
        # and flip us long - survivable when runs were a day apart, not when they repeat
        # every few minutes across the entry window. PENDING_EXIT with no exit_order_id
        # is the "did I cover?" state, and reconcile_and_sync halts on it.
        previous_status = pos.get("status")
        pos["status"] = ps.PENDING_EXIT
        if not _flush_ledger(persist, report, ticker):
            pos["status"] = previous_status
            continue

        result = manager.submit_cover(ticker, shares, quotes.get(ticker.upper()))
        report.exits.append(result.to_dict())
        if result.outcome == Outcome.SUBMITTED.value:
            ps.mark_exit_submitted(pos, result.order_id)
        elif result.outcome == Outcome.WOULD_PLACE.value:
            ps.mark_closed(pos, reason="dry_run_cover")
        elif result.outcome == Outcome.UNCERTAIN.value:
            # May have reached the broker; mark pending so reconciliation resolves it.
            ps.mark_exit_submitted(pos, result.order_id)
            report.notes.append(f"{ticker}: cover uncertain, will reconcile next run")
        else:
            # Broker refused or we chose not to send: nothing is working, so put the
            # position back to where it was and let a later run retry it.
            pos["status"] = previous_status


def process_entries(
    signals,
    positions: list[dict],
    manager: OrderManager,
    quotes: dict[str, Quote],
    report: SessionReport,
    take_profit_pct: float,
    persist=None,
) -> None:
    """Open new shorts for ENTER_NOW signals, one position per ticker."""
    for signal in signals:
        if signal.status != "ENTER_NOW":
            continue

        existing = ps.find_live_position(positions, signal.ticker)
        if existing:
            report.notes.append(
                f"{signal.ticker}: already have a {existing.get('status')} position; "
                f"not re-shorting"
            )
            continue

        quote = quotes.get(signal.ticker.upper())
        client_order_id = ps.new_client_order_id(signal.ticker)

        # Write-ahead the entry intent for anything that will actually be submitted,
        # and flush it to disk before the order can reach the broker. Checking the
        # block reason first keeps the ledger free of records for the many entries
        # that get vetoed on spread, shortability or caps - `entry_block_reason` is a
        # pure read, so asking twice costs nothing.
        pos = None
        if manager.entry_block_reason(signal, quote) is None:
            pos = ps.add_position(
                positions, signal.ticker, signal.entry_date, signal.effective_date,
                notional=signal.notional or 0.0, shares=signal.shares,
                client_order_id=client_order_id,
            )
            if not _flush_ledger(persist, report, signal.ticker):
                ps.mark_entry_rejected(pos, positions, reason="ledger_write_failed")
                continue

        result = manager.submit_entry(signal, quote, client_order_id=client_order_id)
        report.entries.append(result.to_dict())

        if result.outcome not in (Outcome.SUBMITTED.value, Outcome.WOULD_PLACE.value,
                                  Outcome.UNCERTAIN.value):
            if pos is not None:
                ps.mark_entry_rejected(pos, positions,
                                       reason=f"entry {result.outcome.lower()}")
            continue

        if pos is None:
            # entry_block_reason said no but the submit went through anyway. Should be
            # unreachable, but never let a live order go unrecorded.
            pos = ps.add_position(
                positions, signal.ticker, signal.entry_date, signal.effective_date,
                notional=signal.notional or 0.0, shares=signal.shares,
                client_order_id=client_order_id,
            )
        pos["entry_order_id"] = result.order_id

        if manager.mode is OrderMode.DRY_RUN:
            # Simulate the fill so the take-profit leg is exercised in paper runs too.
            fill = result.limit_price or signal.current_price or 0.0
            if fill > 0:
                ps.mark_entry_filled(pos, fill, int(signal.shares))
                tp = manager.submit_take_profit(signal.ticker, int(signal.shares), fill,
                                                take_profit_pct)
                ps.attach_take_profit(pos, tp.order_id)
        # In LIVE the take-profit is attached on the next run, once the entry is
        # confirmed filled and we know the real fill price (see attach_take_profits).


def attach_take_profits(
    positions: list[dict],
    manager: OrderManager,
    report: SessionReport,
    take_profit_pct: float,
) -> None:
    """Rest a GTC take-profit on any OPEN position that lacks one.

    Runs after reconciliation so it uses the real fill price. Separating this from
    entry submission is what makes the 20% target honest when the entry slipped.
    """
    if manager.mode is OrderMode.DRY_RUN:
        return
    for pos in positions:
        if pos.get("status") != ps.OPEN or pos.get("tp_order_id"):
            continue
        fill = pos.get("entry_fill_price")
        shares = int(pos.get("filled_shares") or 0)
        if not fill or shares <= 0:
            continue
        result = manager.submit_take_profit(pos["ticker"], shares, float(fill), take_profit_pct)
        if result.outcome == Outcome.SUBMITTED.value:
            ps.attach_take_profit(pos, result.order_id)
            report.notes.append(f"{pos['ticker']}: take-profit resting @ {result.limit_price}")
        else:
            report.notes.append(
                f"{pos['ticker']}: take-profit NOT placed ({result.detail}); "
                f"position will still exit on its date"
            )


def collect_quote_tickers(signals, positions) -> list[str]:
    tickers = {s.ticker.upper() for s in signals if s.status == "ENTER_NOW" and s.ticker}
    tickers |= {(p.get("ticker") or "").upper() for p in ps.live_positions(positions)}
    return sorted(t for t in tickers if t)
