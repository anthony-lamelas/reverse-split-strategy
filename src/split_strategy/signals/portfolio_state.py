"""Persisted ledger of positions, with an explicit lifecycle.

The original version tracked only committed capital and assumed a position was closed
once its planned exit date passed. That is dangerous for live trading: a date passing
does not close a short. Nothing had actually covered, so the ledger would forget a
position the account still held.

Here a position moves through explicit states, and only a confirmed cover marks it
CLOSED:

    PENDING_ENTRY  entry order submitted, fill not yet confirmed
    OPEN           entry filled - we are short and MUST eventually cover
    PENDING_EXIT   cover order submitted, fill not yet confirmed
    CLOSED         cover confirmed filled; capital released

Anything not CLOSED still ties up capital and still needs an exit. Dry-run mode
simulates the transitions (`simulate_lifecycle`) so paper runs behave sensibly without
pretending they reflect real fills.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

PENDING_ENTRY = "PENDING_ENTRY"
OPEN = "OPEN"
PENDING_EXIT = "PENDING_EXIT"
CLOSED = "CLOSED"

#: States in which capital is still committed and an exit is still owed.
LIVE_STATES = (PENDING_ENTRY, OPEN, PENDING_EXIT)


# ----------------------------------------------------------------------------------
# persistence
# ----------------------------------------------------------------------------------

def load_positions(path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        # A corrupt ledger must not be silently treated as "no positions" - that would
        # under-report committed capital and re-open positions we already hold.
        raise RuntimeError(
            f"Position ledger at {path} is unreadable. Refusing to continue: treating "
            f"it as empty would double-commit capital. Inspect or restore it manually."
        )
    if not isinstance(data, list):
        raise RuntimeError(f"Position ledger at {path} is not a list of positions.")
    return [_migrate(p) for p in data]


def save_positions(path, positions: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(positions, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)  # atomic: a crash mid-write can't truncate the ledger


def _migrate(p: dict) -> dict:
    """Fill in fields added after a ledger was first written."""
    p.setdefault("status", OPEN)
    p.setdefault("shares", None)
    p.setdefault("entry_order_id", None)
    p.setdefault("entry_fill_price", None)
    p.setdefault("filled_shares", None)
    p.setdefault("tp_order_id", None)
    p.setdefault("exit_order_id", None)
    p.setdefault("exit_fill_price", None)
    p.setdefault("client_order_id", None)
    p.setdefault("closed_at", None)
    return p


# ----------------------------------------------------------------------------------
# queries
# ----------------------------------------------------------------------------------

def live_positions(positions: Iterable[dict]) -> list[dict]:
    return [p for p in positions if p.get("status") in LIVE_STATES]


def committed_capital(positions: Iterable[dict]) -> float:
    """Capital tied up in anything not yet confirmed closed."""
    return float(sum(p.get("notional") or 0.0 for p in live_positions(positions)))


def find_live_position(positions: Iterable[dict], ticker: str) -> Optional[dict]:
    """The open/pending position for a ticker, if any.

    This is the duplicate-order guard: without it, a signal that stays actionable for
    several days re-shorts the same name on every run.
    """
    t = (ticker or "").upper()
    for p in live_positions(positions):
        if (p.get("ticker") or "").upper() == t:
            return p
    return None


def positions_due_for_exit(positions: Iterable[dict], as_of: Optional[pd.Timestamp] = None) -> list[dict]:
    """OPEN positions whose planned exit date has arrived.

    PENDING_ENTRY is excluded: we don't know we own it yet. PENDING_EXIT is excluded:
    a cover is already working, and submitting another could over-cover into a long.
    """
    as_of = pd.Timestamp.now().normalize() if as_of is None else pd.Timestamp(as_of).normalize()
    due = []
    for p in positions:
        if p.get("status") != OPEN:
            continue
        try:
            exit_dt = pd.Timestamp(p["planned_exit_date"]).normalize()
        except Exception:
            continue
        if exit_dt <= as_of:
            due.append(p)
    return due


def malformed_positions(positions: Iterable[dict]) -> list[dict]:
    """Live positions we cannot schedule an exit for - these need human attention."""
    bad = []
    for p in live_positions(positions):
        try:
            pd.Timestamp(p["planned_exit_date"])
        except Exception:
            bad.append(p)
    return bad


# ----------------------------------------------------------------------------------
# mutations
# ----------------------------------------------------------------------------------

def new_client_order_id(ticker: str) -> str:
    """Idempotency key so a retried or double-run submit can be recognized."""
    return f"rss-{(ticker or '').upper()}-{uuid.uuid4().hex[:12]}"


def add_position(
    positions: list[dict],
    ticker: str,
    entry_date,
    planned_exit_date,
    notional: float,
    shares: Optional[int] = None,
    entry_order_id: Optional[str] = None,
    client_order_id: Optional[str] = None,
    status: str = PENDING_ENTRY,
) -> dict:
    """Record a newly submitted entry. Returns the created record."""
    record = dict(
        ticker=(ticker or "").upper(),
        entry_date=str(entry_date),
        planned_exit_date=str(planned_exit_date),
        notional=float(notional or 0.0),
        shares=shares,
        status=status,
        entry_order_id=entry_order_id,
        client_order_id=client_order_id or new_client_order_id(ticker),
        entry_fill_price=None,
        filled_shares=None,
        tp_order_id=None,
        exit_order_id=None,
        exit_fill_price=None,
        opened_at=pd.Timestamp.now().isoformat(),
        closed_at=None,
    )
    positions.append(record)
    return record


def mark_entry_filled(position: dict, fill_price: float, filled_shares: int) -> dict:
    position["status"] = OPEN
    position["entry_fill_price"] = float(fill_price)
    position["filled_shares"] = int(filled_shares)
    # Size the committed capital off what actually filled, not what we intended.
    position["notional"] = float(fill_price) * int(filled_shares)
    return position


def mark_entry_rejected(position: dict, positions: list[dict], reason: str = "") -> list[dict]:
    """An entry that never filled is not a position - drop it so it stops holding
    capital and stops blocking future signals on that ticker."""
    position["status"] = CLOSED
    position["closed_at"] = pd.Timestamp.now().isoformat()
    position["close_reason"] = reason or "entry_rejected"
    position["notional"] = 0.0
    return positions


def attach_take_profit(position: dict, tp_order_id: Optional[str]) -> dict:
    position["tp_order_id"] = tp_order_id
    return position


def mark_exit_submitted(position: dict, exit_order_id: Optional[str]) -> dict:
    position["status"] = PENDING_EXIT
    position["exit_order_id"] = exit_order_id
    return position


def mark_closed(position: dict, fill_price: Optional[float] = None, reason: str = "covered") -> dict:
    position["status"] = CLOSED
    position["exit_fill_price"] = None if fill_price is None else float(fill_price)
    position["closed_at"] = pd.Timestamp.now().isoformat()
    position["close_reason"] = reason
    position["notional"] = 0.0
    return position


def simulate_lifecycle(positions: list[dict], as_of: Optional[pd.Timestamp] = None) -> tuple[list[dict], int, int]:
    """Advance a DRY-RUN ledger as if orders filled as intended.

    Live runs must never call this - real state comes from broker reconciliation. This
    exists so paper runs still exercise capital limits and the duplicate guard.

    Returns (positions, n_filled, n_closed).
    """
    as_of = pd.Timestamp.now().normalize() if as_of is None else pd.Timestamp(as_of).normalize()
    filled = closed = 0
    for p in positions:
        if p.get("status") == PENDING_ENTRY:
            p["status"] = OPEN
            filled += 1
        if p.get("status") in (OPEN, PENDING_EXIT):
            try:
                exit_dt = pd.Timestamp(p["planned_exit_date"]).normalize()
            except Exception:
                continue
            if exit_dt <= as_of:
                mark_closed(p, reason="simulated_time_exit")
                closed += 1
    return positions, filled, closed


def prune_archive(positions: list[dict], keep_closed: int = 200) -> list[dict]:
    """Keep all live positions plus the most recent closed ones, so the ledger stays
    an audit trail without growing without bound."""
    live = [p for p in positions if p.get("status") in LIVE_STATES]
    done = [p for p in positions if p.get("status") == CLOSED]
    done.sort(key=lambda p: p.get("closed_at") or "")
    return live + done[-keep_closed:]
