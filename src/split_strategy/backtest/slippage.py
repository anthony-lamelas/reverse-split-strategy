"""Spread-aware slippage estimation from daily OHLC.

The backtest deducts a flat 1.5% per trade for all costs. For sub-$1 micro-caps that is
optimistic to the point of being wrong: a $0.09 stock with a one-cent spread costs ~11%
to cross, and the strategy crosses twice (in and out).

We have no historical bid/ask data — yfinance gives daily OHLCV only — so the spread is
*estimated* using Corwin & Schultz (2012), which infers the effective bid-ask spread
from daily high/low ranges over consecutive days. The intuition: the high/low range of
a single day reflects both true volatility and the spread, while the range over two
days reflects proportionally more volatility and the same spread. Comparing them
separates the two.

What this can and cannot support:
- CAN: a realistic per-trade cost, and a "skip names wider than X%" rule that exactly
  mirrors the live spread filter — so the backtest can answer whether the edge survives
  refusing the worst-priced names.
- CANNOT: simulate whether a specific limit order would have filled. That needs
  intraday quotes we do not have; any such claim would be fabricated.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Corwin-Schultz can return small negative values on quiet days; the convention is to
# floor them at zero rather than treat them as negative cost.
MIN_SPREAD = 0.0
# Sanity ceiling. Estimates above this are noise (gaps, halts, splits) rather than a
# real quotable spread.
MAX_PLAUSIBLE_SPREAD = 0.50


def corwin_schultz_spread(high: pd.Series, low: pd.Series) -> pd.Series:
    """Estimated proportional bid-ask spread per day.

    Returns a Series aligned to the input index; the first observation is NaN because
    the estimator needs consecutive-day pairs.
    """
    high = pd.to_numeric(high, errors="coerce")
    low = pd.to_numeric(low, errors="coerce")

    valid = (high > 0) & (low > 0) & (high >= low)
    high = high.where(valid)
    low = low.where(valid)

    # Single-day log range squared.
    beta_single = np.log(high / low) ** 2
    # Sum over two consecutive days.
    beta = beta_single + beta_single.shift(1)

    # Two-day high/low range.
    high_2 = pd.concat([high, high.shift(1)], axis=1).max(axis=1)
    low_2 = pd.concat([low, low.shift(1)], axis=1).min(axis=1)
    gamma = np.log(high_2 / low_2) ** 2

    denom = 3.0 - 2.0 * np.sqrt(2.0)
    alpha = (np.sqrt(2.0 * beta) - np.sqrt(beta)) / denom - np.sqrt(gamma / denom)
    spread = 2.0 * (np.exp(alpha) - 1.0) / (1.0 + np.exp(alpha))

    spread = spread.replace([np.inf, -np.inf], np.nan)
    return spread.clip(lower=MIN_SPREAD)


def estimate_spread(
    ticker_data: pd.DataFrame,
    as_of,
    lookback: int = 20,
) -> float:
    """Median estimated spread over the `lookback` sessions before `as_of`.

    Median rather than mean: a single halt or gap day produces a wild estimate that
    would otherwise dominate. Returns NaN when it cannot be estimated — callers must
    decide whether that means "skip" (live: yes) or "fall back to flat" (backtest).
    """
    if ticker_data is None or ticker_data.empty:
        return float("nan")
    if "High" not in ticker_data or "Low" not in ticker_data:
        return float("nan")

    window = ticker_data[ticker_data.index < pd.Timestamp(as_of)].tail(lookback)
    if len(window) < 3:
        return float("nan")

    spreads = corwin_schultz_spread(window["High"], window["Low"]).dropna()
    spreads = spreads[spreads <= MAX_PLAUSIBLE_SPREAD]
    if spreads.empty:
        return float("nan")
    return float(spreads.median())


def round_trip_cost(spread: float, base_cost: float = 0.0) -> float:
    """Total proportional cost of entering and exiting.

    Crossing the spread costs roughly half of it per side (mid to bid, mid to ask), so
    a full round trip costs about one full spread. `base_cost` covers commissions and
    other fixed frictions on top.
    """
    if not np.isfinite(spread):
        return base_cost
    return base_cost + float(spread)
