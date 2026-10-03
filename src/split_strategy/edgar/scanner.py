"""Read one EDGAR submission and decide whether it announces a reverse split.

Shared by the live scanner (`scripts/scan_early_edgar.py`) and the historical backfill
(`scripts/backfill_events.py`). They must share it: a backtest is only evidence about
the live strategy if its events come from the same classifier the live system trades
on. The earlier backtest drew 93% of its events from a different pipeline, with a
median 84 days from announcement to split against the live feed's 5.
"""
from __future__ import annotations

import re
import time
from typing import Optional

import pandas as pd
import requests

from . import client as edgar_client
from .llm_analysis import analyze_with_llm, check_keywords_extensive

_ACCEPTANCE = re.compile(r"<ACCEPTANCE-DATETIME>(\d{14})")
_TRADING_SYMBOL = re.compile(r"dei:TradingSymbol[^>]*>\s*([A-Za-z][A-Za-z.\-]{0,9})\s*<")
_TAG = re.compile(r"<[^>]+>")


def parse_acceptance(raw: str) -> Optional[str]:
    """When EDGAR accepted the submission, as an ISO string in Eastern time (naive).

    This, not the filing DATE, is when the market could know. A filing accepted at
    08:00 moves the stock in that day's session; one accepted at 17:05 cannot be
    traded until the next open. The filing date alone cannot tell them apart.
    """
    m = _ACCEPTANCE.search(raw[:5000])
    if not m:
        return None
    ts = pd.to_datetime(m.group(1), format="%Y%m%d%H%M%S", errors="coerce")
    return None if pd.isna(ts) else ts.isoformat()


def parse_trading_symbol(raw: str) -> Optional[str]:
    """The ticker the filer itself reported on the cover page, if it reported one.

    Point-in-time by construction, which the SEC's company_tickers.json is not: that
    file lists today's symbols, so a company since renamed or delisted resolves to
    the wrong ticker or to none. Domestic 8-Ks carry this tag from 2019; 6-Ks do not.
    """
    m = _TRADING_SYMBOL.search(raw)
    return m.group(1).upper() if m else None


def filing_text(raw: str) -> str:
    """Visible text of a submission.

    Falls back to stripping tags by regex when the HTML parser refuses the markup -
    it does on submissions with embedded binary exhibits, and those filings used to
    be dropped after five retries without being read at all.
    """
    try:
        from bs4 import BeautifulSoup
        return BeautifulSoup(raw, "html.parser").get_text(separator=" ", strip=True)
    except Exception:
        return re.sub(r"\s+", " ", _TAG.sub(" ", raw)).strip()


#: Document types that are never prose. A submission lists its readable documents
#: first (the form, then its exhibits) and these after, often as megabytes of
#: uuencoded images - so the download stops at the first one.
_BINARY_TYPES = ("GRAPHIC", "ZIP", "EXCEL", "XML", "EX-101", "JSON", "PDF")
_BINARY_MARKER = re.compile(r"<TYPE>(?:%s)" % "|".join(re.escape(t) for t in _BINARY_TYPES))
_MAX_TEXT_BYTES = 4_000_000


def readable_part(raw: str) -> str:
    """`raw` up to the first non-prose document, cut at that document's start."""
    m = _BINARY_MARKER.search(raw)
    if not m:
        return raw
    start = raw.rfind("<DOCUMENT>", 0, m.start())
    return raw[: start if start >= 0 else m.start()]


def fetch_submission(url: str, max_retries: int = 5) -> str:
    """Download the readable part of a submission, paced by the process-wide SEC
    limiter. Raises on failure.

    Streams and stops at the first image/XBRL/archive document (or a size cap): the
    full file is routinely tens of megabytes of attachments nothing here reads.
    """
    last = None
    for attempt in range(max_retries):
        edgar_client._SEC_LIMITER.acquire()
        try:
            with requests.get(url, headers=edgar_client.HEADERS, timeout=60,
                              stream=True) as resp:
                if resp.status_code == 429 or resp.status_code >= 500:
                    last = RuntimeError(f"SEC returned {resp.status_code}")
                    time.sleep(1.0 * (2 ** attempt))
                    continue
                resp.raise_for_status()
                parts, size = [], 0
                for chunk in resp.iter_content(chunk_size=262_144):
                    parts.append(chunk)
                    size += len(chunk)
                    # Re-check the tail too, so a marker split across chunks is seen.
                    tail = b"".join(parts[-2:]).decode("utf-8", errors="replace")
                    if size >= _MAX_TEXT_BYTES or _BINARY_MARKER.search(tail):
                        break
                return readable_part(b"".join(parts).decode("utf-8", errors="replace"))
        except requests.RequestException as e:
            last = e
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"could not fetch {url}: {last}")


def split_is_ahead(analysis: dict, date_filed: str) -> bool:
    """Is the split still in the future as of the filing? Decided from the dates.

    The LLM's own `is_future_split` flag is not trusted when it disagrees with the
    dates it extracted: measured over 2019-2026 it marked 13% of 2025's dated,
    still-ahead splits as past, and most of 2022's. When no effective date can be
    parsed there is nothing to check against, so the flag is all there is.
    """
    effective = pd.to_datetime(analysis.get("effective_date"), errors="coerce")
    filed = pd.to_datetime(str(date_filed), errors="coerce")
    if pd.isna(effective) or pd.isna(filed):
        return analysis.get("is_future_split") is not False
    return effective.normalize() > filed.normalize()


def classify(raw: str, company_name: str, date_filed: str,
             openai_api_key: Optional[str]) -> Optional[dict]:
    """Run the live keyword filter and LLM classifier over one submission.

    Returns None when no split keyword appears (no LLM call is made), otherwise the
    LLM's analysis with `accepted_at` and `trading_symbol` added. `date_filed` is
    passed to the prompt exactly as the live scanner passes it (YYYYMMDD).
    """
    text = filing_text(raw)
    if not check_keywords_extensive(text):
        return None
    analysis = analyze_with_llm(text, company_name, date_filed,
                                openai_api_key=openai_api_key)
    analysis["accepted_at"] = parse_acceptance(raw)
    analysis["trading_symbol"] = parse_trading_symbol(raw)
    return analysis
