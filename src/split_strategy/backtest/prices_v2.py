"""Unadjusted daily prices and the split table, from Polygon (now Massive).

The first backtest ran on Yahoo, whose prices are always split-adjusted, cumulatively,
for every later split - so a stock quoted at $0.11 appeared at $0.88, or at $791,000.
Every price-based filter in the project was calibrated on those numbers, and delisted
names were simply absent. This source fixes both: `adjusted=false` returns the price
that was actually quoted, and delisted tickers keep their history.

Results are cached per ticker under `DATA/prices_v2/` (git-ignored; the provider's
terms require deleting it if the subscription ends). A cached range is reused only
when it covers the request, so widening the window re-fetches rather than silently
truncating.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from .. import config

BASE_URL = "https://api.polygon.io"
COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


class PolygonPrices:
    def __init__(self, api_key: Optional[str] = None, cache_dir=None,
                 min_interval: Optional[float] = None, session=None):
        self.api_key = api_key or config.POLYGON_API_KEY
        if not self.api_key:
            raise ValueError("POLYGON_API_KEY is not set (put it in .env).")
        self.cache_dir = Path(cache_dir or (config.DATA_DIR / "prices_v2"))
        #: Seconds between requests. The free tier allows 5 calls a minute; paid tiers
        #: are unlimited, so set POLYGON_MIN_INTERVAL=0 after upgrading.
        self.min_interval = (config.POLYGON_MIN_INTERVAL if min_interval is None
                             else min_interval)
        self._session = session or requests.Session()
        self._last_call = 0.0

    # -- HTTP -----------------------------------------------------------------

    def _get(self, path: str, **params) -> dict:
        for attempt in range(6):
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            resp = self._session.get(BASE_URL + path, timeout=60,
                                     params={**params, "apiKey": self.api_key})
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(max(self.min_interval, 2.0 * (attempt + 1)))
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"Polygon kept refusing {path} (rate limit or server error)")

    # -- cache ----------------------------------------------------------------

    def _paths(self, kind: str, ticker: str) -> tuple[Path, Path]:
        safe = ticker.upper().replace("/", "_")
        base = self.cache_dir / kind
        return base / f"{safe}.csv", base / f"{safe}.json"

    # -- daily bars -----------------------------------------------------------

    def daily(self, ticker: str, start, end) -> pd.DataFrame:
        """Unadjusted daily OHLCV for `ticker`, indexed by session date (tz-naive).

        Empty frame when the provider has nothing for the symbol in that range.
        """
        start, end = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
        csv, meta = self._paths("daily", ticker)
        if csv.exists() and meta.exists():
            have = json.loads(meta.read_text())
            if pd.Timestamp(have["start"]) <= start and pd.Timestamp(have["end"]) >= end:
                frame = pd.read_csv(csv, index_col=0, parse_dates=True)
                return frame.loc[start:end]

        data = self._get(
            f"/v2/aggs/ticker/{ticker.upper()}/range/1/day/{start.date()}/{end.date()}",
            adjusted="false", sort="asc", limit=50000)
        frame = bars_to_frame(data.get("results") or [])
        csv.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(csv)
        meta.write_text(json.dumps({"start": str(start.date()), "end": str(end.date()),
                                    "fetched_at": pd.Timestamp.now().isoformat()}))
        return frame

    # -- splits ---------------------------------------------------------------

    def splits(self, ticker: str) -> pd.DataFrame:
        """Every split the provider records for `ticker`.

        Columns `execution_date`, `split_from`, `split_to`, and `factor` - the
        multiple the PRICE moves by on that date (10.0 for a 1-for-10 reverse split).
        """
        csv, _ = self._paths("splits", ticker)
        if csv.exists():
            return pd.read_csv(csv, parse_dates=["execution_date"])
        data = self._get("/v3/reference/splits", ticker=ticker.upper(), limit=1000)
        frame = splits_to_frame(data.get("results") or [])
        csv.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(csv, index=False)
        return frame


def bars_to_frame(results: list[dict]) -> pd.DataFrame:
    """Polygon aggregate bars -> an OHLCV frame indexed by session date."""
    if not results:
        return pd.DataFrame(columns=COLUMNS, index=pd.DatetimeIndex([], name="date"))
    frame = pd.DataFrame(results)
    # `t` is the bar's start in epoch ms: midnight EASTERN, i.e. 04:00 or 05:00 UTC.
    # Normalising in UTC would be right by accident; converting first is right.
    index = (pd.to_datetime(frame["t"], unit="ms", utc=True)
             .dt.tz_convert("America/New_York").dt.normalize().dt.tz_localize(None))
    out = pd.DataFrame({"Open": frame["o"], "High": frame["h"], "Low": frame["l"],
                        "Close": frame["c"], "Volume": frame["v"]})
    out.index = pd.DatetimeIndex(index, name="date")
    return out.sort_index()


def splits_to_frame(results: list[dict]) -> pd.DataFrame:
    rows = [{"execution_date": pd.Timestamp(r["execution_date"]),
             "split_from": float(r["split_from"]), "split_to": float(r["split_to"]),
             "factor": float(r["split_from"]) / float(r["split_to"])}
            for r in results if r.get("execution_date") and r.get("split_to")]
    frame = pd.DataFrame(rows, columns=["execution_date", "split_from", "split_to", "factor"])
    return frame.sort_values("execution_date").reset_index(drop=True)
