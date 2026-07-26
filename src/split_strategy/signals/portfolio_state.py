"""Persisted ledger of currently-open positions.

Fixes the same capital-overcommitment gap found in backtesting: sizing every new
signal at a flat % of TOTAL account equity, with no regard for how much capital is
already tied up in other still-open positions. A daily signal run has no memory of
its own between invocations, so this ledger is that memory — it lets
`generate_signals()` know how much capital is already committed before sizing new
trades, and skip/flag trades that would exceed a max-exposure cap.

This is a planning ledger, not a source of truth for actual broker fills: a position
is assumed "closed" once its planned exit date (the split's effective_date, matching
the day_of_split strategy) has passed. It does not yet reconcile against real account
positions or confirm that an exit order was actually placed/filled.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd


def load_positions(path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_positions(path, positions: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(positions, indent=2, default=str), encoding="utf-8")


def prune_closed(positions: list[dict], as_of: Optional[pd.Timestamp] = None) -> tuple[list[dict], int]:
    """Drop positions whose planned exit date is on/before as_of (assumed closed).

    Returns (still_open_positions, n_closed).
    """
    as_of = pd.Timestamp.now().normalize() if as_of is None else pd.Timestamp(as_of).normalize()
    still_open, closed = [], 0
    for p in positions:
        try:
            exit_dt = pd.Timestamp(p["planned_exit_date"])
        except Exception:
            still_open.append(p)  # malformed entry: keep rather than silently drop capital tracking
            continue
        if exit_dt <= as_of:
            closed += 1
        else:
            still_open.append(p)
    return still_open, closed


def committed_capital(positions: list[dict]) -> float:
    return float(sum(p.get("notional", 0.0) for p in positions))


def add_position(positions: list[dict], ticker: str, entry_date, planned_exit_date, notional: float) -> list[dict]:
    positions.append(dict(
        ticker=ticker,
        entry_date=str(entry_date),
        planned_exit_date=str(planned_exit_date),
        notional=float(notional),
        opened_at=pd.Timestamp.now().isoformat(),
    ))
    return positions
