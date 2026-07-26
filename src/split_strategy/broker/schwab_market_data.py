"""Real (not proxy) shortability data from Schwab's quote endpoint.

Schwab's `/quotes` response includes a `reference` block with `isShortable`,
`isHardToBorrow`, and `htbRate` (the actual annualized hard-to-borrow fee, as a
percentage - negative values mean you pay financing cost instead of earning the
short-proceeds rebate). This is live, current data for currently-listed tickers only
- it cannot answer "was this shortable on a past date" (no broker publishes that), but
going forward it's real ground truth we can log daily to validate/calibrate the
historical proxy classifier in backtest/shortability.py.
"""
from __future__ import annotations

from typing import Optional


def get_shortability(client, ticker: str) -> Optional[dict]:
    """Query Schwab for a ticker's current shortability. Returns None on any failure
    (network, symbol not found, unauthenticated) - callers should treat that as
    "unknown," not "not shortable."
    """
    try:
        resp = client.get_quote(ticker)
        if resp.status_code >= 400:
            return None
        data = resp.json()
        q = data.get(ticker.upper()) or next(iter(data.values()), None)
        if not q:
            return None
        ref = q.get("reference", {})
        return dict(
            ticker=ticker,
            is_shortable=ref.get("isShortable"),
            is_hard_to_borrow=ref.get("isHardToBorrow"),
            htb_rate=ref.get("htbRate"),
            exchange=ref.get("exchangeName"),
            last_price=q.get("quote", {}).get("lastPrice"),
        )
    except Exception:
        return None


def get_shortability_batch(client, tickers: list[str]) -> dict[str, Optional[dict]]:
    """Best-effort per-ticker shortability lookup. Schwab's quote endpoint accepts a
    comma-separated symbol list in one call; fall back to per-ticker on failure."""
    out = {}
    try:
        resp = client.get_quotes(tickers)
        if resp.status_code < 400:
            data = resp.json()
            for t in tickers:
                q = data.get(t.upper())
                if not q:
                    out[t] = None
                    continue
                ref = q.get("reference", {})
                out[t] = dict(
                    ticker=t,
                    is_shortable=ref.get("isShortable"),
                    is_hard_to_borrow=ref.get("isHardToBorrow"),
                    htb_rate=ref.get("htbRate"),
                    exchange=ref.get("exchangeName"),
                    last_price=q.get("quote", {}).get("lastPrice"),
                )
            return out
    except Exception:
        pass
    # Fallback: one at a time.
    for t in tickers:
        out[t] = get_shortability(client, t)
    return out
