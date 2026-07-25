"""Construct and (optionally) place SELL_SHORT orders from signals.

Default mode is DRY_RUN: orders are fully constructed and logged as "would place …"
but never sent. LIVE mode submits via schwab-py and records fill-vs-reject outcomes —
many micro-caps reject for no-borrow / non-marginable, which is expected and logged,
not treated as an error.

This milestone ships DRY_RUN only; LIVE is wired but gated behind an explicit flag.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional

from ..signals.generate import Signal


class OrderMode(str, Enum):
    DRY_RUN = "DRY_RUN"
    LIVE = "LIVE"


@dataclass
class OrderResult:
    ticker: str
    side: str
    quantity: int
    order_type: str
    mode: str
    outcome: str            # WOULD_PLACE | SUBMITTED | REJECTED | SKIPPED
    detail: str = ""
    order_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def build_order_spec(signal: Signal, order_type: str = "MARKET") -> dict:
    """A broker-agnostic dict describing the intended short order."""
    return {
        "symbol": signal.ticker,
        "instruction": "SELL_SHORT",
        "quantity": int(signal.shares or 0),
        "order_type": order_type,
        "planned_stop_price": signal.stop_price,
        "planned_exit_date": signal.effective_date,
    }


class OrderManager:
    def __init__(self, mode: OrderMode = OrderMode.DRY_RUN, client=None, account_hash: Optional[str] = None):
        self.mode = OrderMode(mode)
        self.client = client
        self.account_hash = account_hash
        self.results: list[OrderResult] = []

    # --- validation shared by both modes ---
    def _presubmit_reasons_to_skip(self, signal: Signal) -> Optional[str]:
        if not signal.shares or signal.shares <= 0:
            return "no share quantity (missing/invalid price)"
        if signal.gap_up_ok is False:
            return f"gap-up filter ({signal.gap_up_pct:.1f}%)"
        if signal.status == "UPCOMING":
            return "not yet at entry date"
        return None

    def process(self, signal: Signal, order_type: str = "MARKET") -> OrderResult:
        spec = build_order_spec(signal, order_type)
        skip = self._presubmit_reasons_to_skip(signal)
        if skip:
            res = OrderResult(signal.ticker, "SELL_SHORT", spec["quantity"], order_type,
                              self.mode.value, "SKIPPED", detail=skip)
            self.results.append(res)
            return res

        if self.mode == OrderMode.DRY_RUN:
            detail = (f"would SELL_SHORT {spec['quantity']} {signal.ticker} @ market "
                      f"(notional ~${signal.notional:,.0f}, stop ${signal.stop_price}); "
                      f"{'shortable' if signal.likely_shortable else 'LIKELY UNSHORTABLE'}")
            res = OrderResult(signal.ticker, "SELL_SHORT", spec["quantity"], order_type,
                              self.mode.value, "WOULD_PLACE", detail=detail)
            self.results.append(res)
            return res

        # --- LIVE ---
        return self._submit_live(signal, spec, order_type)

    def _submit_live(self, signal: Signal, spec: dict, order_type: str) -> OrderResult:
        try:
            from schwab.orders.equities import (
                equity_sell_short_market,
                equity_sell_short_limit,
            )
        except ImportError as e:
            res = OrderResult(signal.ticker, "SELL_SHORT", spec["quantity"], order_type,
                              self.mode.value, "REJECTED", detail=f"schwab-py not installed: {e}")
            self.results.append(res)
            return res

        if self.client is None or self.account_hash is None:
            res = OrderResult(signal.ticker, "SELL_SHORT", spec["quantity"], order_type,
                              self.mode.value, "REJECTED", detail="no authenticated client/account")
            self.results.append(res)
            return res

        try:
            order = equity_sell_short_market(signal.ticker, spec["quantity"])
            resp = self.client.place_order(self.account_hash, order)
            # schwab-py returns an httpx.Response; 201 = created.
            if resp.status_code >= 400:
                detail = f"broker rejected ({resp.status_code}): {resp.text[:200]}"
                outcome = "REJECTED"
                order_id = None
            else:
                order_id = resp.headers.get("Location", "").rsplit("/", 1)[-1] or None
                detail = f"submitted (HTTP {resp.status_code})"
                outcome = "SUBMITTED"
        except Exception as e:  # includes no-borrow / not-shortable rejections
            detail = f"exception on submit: {e}"
            outcome = "REJECTED"
            order_id = None

        res = OrderResult(signal.ticker, "SELL_SHORT", spec["quantity"], order_type,
                          self.mode.value, outcome, detail=detail, order_id=order_id)
        self.results.append(res)
        return res

    def summary(self) -> dict:
        from collections import Counter
        c = Counter(r.outcome for r in self.results)
        return dict(total=len(self.results), **c)
