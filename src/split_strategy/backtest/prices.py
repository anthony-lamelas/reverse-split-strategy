"""Price-panel construction for the backtest.

Builds the yfinance-style OHLCV panel the engine expects: a DataFrame with a
MultiIndex column (ticker, field) and a tz-naive DatetimeIndex, so that
`panel[ticker]['Open'/'High'/'Low'/'Close'/'Volume']` works.

IMPORTANT: prices are fetched with auto_adjust=False (raw/unadjusted). yfinance
retroactively splits-adjusts by default, which would distort a short measured across
the split boundary (see docs/VALIDATION_REPORT.md, issue 1.5). We want the real
tradeable prices.
"""
from __future__ import annotations

from typing import Iterable, Optional

import pandas as pd


def fetch_prices(
    tickers: Iterable[str],
    start,
    end,
    chunk_size: int = 50,
) -> tuple[pd.DataFrame, list[str]]:
    """Download an OHLCV panel for `tickers` between start and end (inclusive-ish).

    Returns (panel, missing) where `panel` has MultiIndex columns (ticker, field)
    and `missing` is the list of tickers that returned no usable data.
    """
    import yfinance as yf

    tickers = sorted({str(t).upper() for t in tickers if t and str(t) != "nan"})
    start = pd.Timestamp(start).strftime("%Y-%m-%d")
    end = (pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")  # yfinance end is exclusive

    frames = {}
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i : i + chunk_size]
        raw = yf.download(
            chunk,
            start=start,
            end=end,
            auto_adjust=False,
            group_by="ticker",
            progress=False,
            threads=True,
        )
        if raw is None or raw.empty:
            continue
        # Normalize to per-ticker frames regardless of single/multi shape.
        if isinstance(raw.columns, pd.MultiIndex):
            level0 = set(raw.columns.get_level_values(0))
            for t in chunk:
                if t in level0:
                    sub = raw[t].dropna(how="all")
                    if not sub.empty:
                        frames[t] = sub
        else:
            # single-ticker shape: columns are the fields
            sub = raw.dropna(how="all")
            if not sub.empty and len(chunk) == 1:
                frames[chunk[0]] = sub

    missing = [t for t in tickers if t not in frames]
    if not frames:
        return pd.DataFrame(), missing

    panel = pd.concat(frames, axis=1)  # -> MultiIndex (ticker, field)
    if panel.index.tz is not None:
        panel.index = panel.index.tz_localize(None)
    panel = panel.sort_index()
    return panel, missing
