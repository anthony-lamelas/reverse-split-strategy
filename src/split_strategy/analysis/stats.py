"""Statistics for judging whether a backtest result is real.

Extracted from `scripts/analyze_robustness.py` so more than one analysis can use
them. The point of every function here is to make a weak result look weak: this
strategy's whole history is one of numbers that looked convincing pooled and
dissolved under a confidence interval.

The RNG is seeded so a published figure can be reproduced exactly. Re-running an
analysis and getting a different confidence interval is indistinguishable from the
analysis having changed, which is a poor way to find out either.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RNG = np.random.default_rng(12345)


def bootstrap_ci(returns: np.ndarray, n_boot: int = 20000, alpha: float = 0.05):
    """Bootstrap CI for the mean per-trade return. Returns (lo, hi, p_positive)."""
    returns = np.asarray(returns, dtype=float)
    if len(returns) == 0:
        return np.nan, np.nan, np.nan
    means = RNG.choice(returns, size=(n_boot, len(returns)), replace=True).mean(axis=1)
    lo, hi = np.percentile(means, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    p_positive = float((means > 0).mean())
    return lo, hi, p_positive


def t_stat(returns: np.ndarray) -> float:
    """t-statistic of the mean per-trade return against zero.

    |t| > ~2 is the conventional bar. On a few dozen micro-cap trades it is a low
    bar, not a high one - it says the mean is unlikely to be zero, not that the
    edge will survive costs, borrow, or the next regime.
    """
    r = np.asarray(returns, dtype=float)
    if len(r) < 2:
        return float("nan")
    sd = r.std(ddof=1)
    if sd == 0:
        return float("nan")
    return float(r.mean() / (sd / np.sqrt(len(r))))


def borrow_adjusted(trades: pd.DataFrame, annual_rate: float) -> float:
    """Mean per-trade return after subtracting a borrow fee for the holding period.

    `annual_rate` is a fraction (0.50 = 50%/yr). Hard-to-borrow micro-caps routinely
    cost 50-200%+ annualized, and a reverse split shrinks the float, so the rate on
    exactly these names tends to rise at exactly the wrong moment.
    """
    if trades.empty:
        return float("nan")
    days = (pd.to_datetime(trades["exit_date"])
            - pd.to_datetime(trades["entry_date"])).dt.days.clip(lower=1)
    fee = annual_rate * days / 365.0
    return float((trades["net_return"] - fee).mean())
