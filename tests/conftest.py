"""Shared fixtures for the test suite.

Everything here is synthetic and in-memory: no MongoDB, no SEC, no yfinance, no
Schwab. The suite must stay runnable offline (it runs in CI with no secrets), so any
test that would touch the network belongs in tests/manual/ instead.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def make_bars(
    dates,
    open_=1.00,
    high=None,
    low=None,
    close=None,
    volume=1_000_000,
) -> pd.DataFrame:
    """Build a single ticker's OHLCV frame.

    Scalars are broadcast across all dates; sequences are used as-is. Defaults produce
    a flat, boring series so a test only has to specify the column it cares about.
    """
    idx = pd.DatetimeIndex(pd.to_datetime(dates))
    n = len(idx)

    def col(value, fallback):
        if value is None:
            value = fallback
        if np.isscalar(value):
            return np.full(n, float(value))
        arr = np.asarray(value, dtype=float)
        assert len(arr) == n, f"expected {n} values, got {len(arr)}"
        return arr

    o = col(open_, 1.00)
    c = col(close, o)
    h = col(high, np.maximum(o, c))
    lo = col(low, np.minimum(o, c))
    v = col(volume, 1_000_000)

    return pd.DataFrame({"Open": o, "High": h, "Low": lo, "Close": c, "Volume": v}, index=idx)


def make_panel(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Combine {ticker: ohlcv_frame} into the yfinance-style MultiIndex panel the
    backtest engine expects: panel[ticker]["Open"], etc."""
    return pd.concat(frames, axis=1).sort_index()


def make_events(rows) -> pd.DataFrame:
    """Build a df_events frame from (ticker, t_ann, t_split, ratio) tuples or dicts."""
    normalized = []
    for r in rows:
        if isinstance(r, dict):
            d = dict(r)
        else:
            ticker, t_ann, t_split, ratio = r
            d = {"ticker": ticker, "t_ann": t_ann, "t_split": t_split, "ratio": ratio}
        d["t_ann"] = pd.Timestamp(d["t_ann"])
        d["t_split"] = pd.Timestamp(d["t_split"])
        normalized.append(d)
    return pd.DataFrame(normalized)


@pytest.fixture
def bars():
    return make_bars


@pytest.fixture
def panel():
    return make_panel


@pytest.fixture
def events():
    return make_events


@pytest.fixture
def trading_days():
    """10 consecutive weekdays starting Mon 2025-01-06."""
    return pd.bdate_range("2025-01-06", periods=10)
