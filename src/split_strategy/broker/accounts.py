"""Account equity, live positions, and order-status lookups.

Two problems this fixes:

1. Sizing used a hardcoded `ACCOUNT_SIZE` env value, so the 2%-per-trade rule never
   compounded with gains and — worse — did not shrink after losses, silently raising
   the real risk per trade as the account fell.
2. Nothing ever asked Schwab what we actually hold. The ledger was the only record, so
   any divergence (a rejected order recorded as filled, a manual trade, a partial fill)
   went unnoticed forever.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Schwab reports a short position as a positive `shortQuantity`.
_SHORT_KEYS = ("shortQuantity", "longQuantity")


@dataclass
class BrokerPosition:
    ticker: str
    short_shares: float = 0.0
    long_shares: float = 0.0

    @property
    def is_short(self) -> bool:
        return self.short_shares > 0


def get_account_equity(client, account_hash: str) -> Optional[float]:
    """Liquidation value of the account, or None if it cannot be determined.

    Callers must treat None as "do not trade" rather than falling back to a default —
    sizing off a stale or guessed balance is how position sizes drift from intent.
    """
    try:
        resp = client.get_account(account_hash)
        if resp.status_code >= 400:
            return None
        data = resp.json() or {}
        account = data.get("securitiesAccount", data)
        for block in ("currentBalances", "projectedBalances", "initialBalances"):
            balances = account.get(block) or {}
            for key in ("liquidationValue", "equity", "cashBalance"):
                value = balances.get(key)
                if value is not None:
                    try:
                        equity = float(value)
                    except (TypeError, ValueError):
                        continue
                    if equity > 0:
                        return equity
        return None
    except Exception:
        return None


def get_broker_positions(client, account_hash: str) -> Optional[dict[str, BrokerPosition]]:
    """Every position Schwab thinks we hold, keyed by uppercase ticker.

    Returns None on failure — distinct from an empty dict, which legitimately means
    "the account holds nothing". Confusing the two would let a reconciliation pass
    succeed while blind.
    """
    try:
        resp = client.get_account(account_hash, fields=client.Account.Fields.POSITIONS)
        if resp.status_code >= 400:
            return None
        data = resp.json() or {}
        account = data.get("securitiesAccount", data)
        out: dict[str, BrokerPosition] = {}
        for raw in account.get("positions", []) or []:
            instrument = raw.get("instrument", {}) or {}
            symbol = (instrument.get("symbol") or "").upper()
            if not symbol:
                continue
            out[symbol] = BrokerPosition(
                ticker=symbol,
                short_shares=float(raw.get("shortQuantity") or 0.0),
                long_shares=float(raw.get("longQuantity") or 0.0),
            )
        return out
    except Exception:
        return None


def get_order_status(client, account_hash: str, order_id: str) -> Optional[dict]:
    """Fetch one order and summarize fill state.

    Returns a dict with `status`, `filled_quantity`, `avg_fill_price`, `is_filled`,
    `is_working`, `is_dead`; or None if the lookup fails.
    """
    try:
        resp = client.get_order(order_id, account_hash)
        if resp.status_code >= 400:
            return None
        return summarize_order(resp.json() or {})
    except Exception:
        return None


def summarize_order(payload: dict) -> dict:
    """Normalize a Schwab order payload into the few fields we act on."""
    status = (payload.get("status") or "").upper()
    filled = payload.get("filledQuantity")
    try:
        filled_qty = float(filled) if filled is not None else 0.0
    except (TypeError, ValueError):
        filled_qty = 0.0

    avg_price = None
    activities = payload.get("orderActivityCollection") or []
    legs = []
    for activity in activities:
        legs.extend(activity.get("executionLegs") or [])
    if legs:
        total_qty = 0.0
        total_cost = 0.0
        for leg in legs:
            try:
                qty = float(leg.get("quantity") or 0.0)
                price = float(leg.get("price") or 0.0)
            except (TypeError, ValueError):
                continue
            total_qty += qty
            total_cost += qty * price
        if total_qty > 0:
            avg_price = total_cost / total_qty
            filled_qty = filled_qty or total_qty
    if avg_price is None:
        try:
            avg_price = float(payload["price"]) if payload.get("price") is not None else None
        except (TypeError, ValueError):
            avg_price = None

    return {
        "status": status,
        "filled_quantity": filled_qty,
        "avg_fill_price": avg_price,
        "is_filled": status == "FILLED" and filled_qty > 0,
        "is_working": status in {"WORKING", "PENDING_ACTIVATION", "QUEUED", "ACCEPTED",
                                 "AWAITING_PARENT_ORDER", "AWAITING_MANUAL_REVIEW"},
        "is_dead": status in {"CANCELED", "CANCELLED", "REJECTED", "EXPIRED"},
    }


@dataclass
class Discrepancy:
    ticker: str
    kind: str
    detail: str


def reconcile(ledger_positions, broker_positions: dict[str, BrokerPosition]) -> list[Discrepancy]:
    """Compare our ledger against what Schwab actually reports.

    Trading on a wrong picture is worse than not trading, so any discrepancy is
    surfaced for the caller to halt on rather than being auto-corrected.
    """
    from ..signals import portfolio_state as ps

    issues: list[Discrepancy] = []
    ledger_live = {(p.get("ticker") or "").upper(): p for p in ps.live_positions(ledger_positions)}
    broker_shorts = {t: p for t, p in (broker_positions or {}).items() if p.is_short}

    for ticker, position in ledger_live.items():
        broker = broker_shorts.get(ticker)
        if broker is None:
            if position.get("status") == ps.PENDING_ENTRY:
                continue  # not filled yet - normal
            issues.append(Discrepancy(
                ticker, "missing_at_broker",
                f"ledger says {position.get('status')} but Schwab reports no short position",
            ))
            continue
        expected = position.get("filled_shares") or position.get("shares")
        if expected and abs(float(expected) - broker.short_shares) > 0.5:
            issues.append(Discrepancy(
                ticker, "quantity_mismatch",
                f"ledger {expected} shares vs Schwab {broker.short_shares:g}",
            ))

    for ticker in broker_shorts:
        if ticker not in ledger_live:
            issues.append(Discrepancy(
                ticker, "untracked_short",
                "Schwab reports a short position the ledger does not know about",
            ))

    return issues
