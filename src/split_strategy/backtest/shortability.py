"""Shortability proxy classifier.

Estimates whether each trade would have been *shortable at a retail broker like
Schwab*. This is a PROXY, not ground truth: exact historical borrow/shortable-
availability data is not retrievable for past dates at any broker, and Schwab
publishes none. We infer likely-unshortable from observable features:

  - Listing venue: OTC / pink-sheet / unlisted names are generally not shortable at
    Schwab; major-exchange (Nasdaq / NYSE / CBOE) names may be.
  - Entry price: sub-$1 stocks are typically non-marginable and cannot be shorted.
  - Security type: warrants / units (ticker suffix W, U, R, WS) are not shortable.
  - Liquidity: very thin dollar-volume names are usually hard-to-borrow / no locate.
  - Post-event delisting: names that stop trading shortly after the split were
    effectively untradeable (a strong hard-to-borrow signal).

Optional stronger validators (documented in docs, not implemented here): Interactive
Brokers' public daily "shortable shares" list, and Schwab's live instrument endpoint
for still-listed survivors once API keys exist.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

MAJOR_EXCHANGES = {"nasdaq", "nyse", "cboe", "nyse american", "nyse arca", "amex"}

# Tunable thresholds for the proxy.
MIN_SHORTABLE_PRICE = 1.00          # sub-$1 -> not shortable / non-marginable
MIN_AVG_DOLLAR_VOLUME = 500_000.0   # below this ~ hard-to-borrow / no locate
LIQUIDITY_LOOKBACK_DAYS = 10        # window before entry to average dollar volume
POST_SPLIT_SURVIVE_DAYS = 5         # must trade this many days after t_split to count as "survived"
WARRANT_SUFFIXES = ("W", "U", "R")  # crude: warrants/units/rights


def load_exchange_map(cache_path: Optional[Path] = None) -> dict:
    """Load ticker -> exchange from the in-repo SEC company_tickers_exchange cache."""
    if cache_path is None:
        cache_path = Path(__file__).resolve().parents[3] / "DATA" / "company_tickers_exchange_cache.json"
    try:
        blob = json.loads(Path(cache_path).read_text())
    except Exception:
        return {}
    fields = blob.get("fields", [])
    data = blob.get("data", [])
    try:
        ti, ei = fields.index("ticker"), fields.index("exchange")
    except ValueError:
        return {}
    out = {}
    for r in data:
        t = (r[ti] or "").upper()
        if t:
            out[t] = r[ei]  # may be None
    return out


def _looks_like_warrant(ticker: str) -> bool:
    t = ticker.upper()
    if "." in t or "-" in t:  # e.g. ABC.WS, ABC-WT
        tail = t.replace(".", "-").split("-")[-1]
        if tail.startswith(("W", "U", "R")):
            return True
    # 5-letter tickers ending in W/U/R are very often warrants/units/rights
    if len(t) == 5 and t[-1] in WARRANT_SUFFIXES:
        return True
    return False


def _avg_dollar_volume(ticker_prices: pd.DataFrame, entry_date, lookback: int) -> float:
    window = ticker_prices[ticker_prices.index < entry_date].tail(lookback)
    if window.empty or "Volume" not in window or "Close" not in window:
        return np.nan
    dv = (window["Close"] * window["Volume"]).dropna()
    return float(dv.mean()) if not dv.empty else np.nan


def classify_shortability(
    trades: pd.DataFrame,
    prices: pd.DataFrame,
    exchange_map: Optional[dict] = None,
) -> pd.DataFrame:
    """Annotate a trades DataFrame with shortability flags.

    Adds columns: exchange, likely_unshortable (bool), unshortable_reasons (str),
    primary_reason (str). Returns a copy.
    """
    if trades is None or trades.empty:
        return trades
    if exchange_map is None:
        exchange_map = load_exchange_map()

    try:
        available = set(prices.columns.levels[0])
    except AttributeError:
        available = set(prices.columns.get_level_values(0))

    out = trades.copy()
    exchanges, flags, reasons_all, primary = [], [], [], []

    for _, r in out.iterrows():
        ticker = str(r["ticker"]).upper()
        reasons = []

        exch = exchange_map.get(ticker)
        exchanges.append(exch)
        exch_l = (exch or "").lower()
        is_major = exch_l in MAJOR_EXCHANGES

        # 1) Listing venue
        if exch is None or exch == "":
            reasons.append("not on major-exchange list (unlisted/unknown)")
        elif not is_major:
            reasons.append(f"non-major exchange ({exch})")

        # 2) Security type
        if _looks_like_warrant(ticker):
            reasons.append("warrant/unit/right (not shortable)")

        # 3) Entry price
        ep = r.get("entry_price", np.nan)
        if pd.notna(ep) and ep < MIN_SHORTABLE_PRICE:
            reasons.append(f"entry price ${ep:.2f} < ${MIN_SHORTABLE_PRICE:.2f} (non-marginable)")

        # 4) Liquidity + 5) post-split survival (need price data)
        if ticker in available:
            tp = prices[ticker].dropna(how="all")
            adv = _avg_dollar_volume(tp, r["entry_date"], LIQUIDITY_LOOKBACK_DAYS)
            if pd.notna(adv) and adv < MIN_AVG_DOLLAR_VOLUME:
                reasons.append(f"thin liquidity (~${adv:,.0f}/day avg)")
            t_split = r.get("t_split")
            if pd.notna(t_split):
                after = tp[tp.index > t_split]
                if len(after) < POST_SPLIT_SURVIVE_DAYS:
                    reasons.append("delisted/halted shortly after split")

        flags.append(len(reasons) > 0)
        reasons_all.append("; ".join(reasons))
        primary.append(reasons[0] if reasons else "")

    out["exchange"] = exchanges
    out["likely_unshortable"] = flags
    out["unshortable_reasons"] = reasons_all
    out["primary_reason"] = primary
    return out
