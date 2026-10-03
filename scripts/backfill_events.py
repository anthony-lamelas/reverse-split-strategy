#!/usr/bin/env python3
"""Backfill `events_v2`: the live classifier, run over EDGAR history.

    python scripts/backfill_events.py --start 2019-01-01 --end 2026-10-02
    python scripts/backfill_events.py --start 2026-09-01 --end 2026-09-30 --max-llm 500

For each weekday, EDGAR full-text search lists the 8-K/6-K filings that mention a
reverse split. Each is downloaded and put through `edgar.scanner.classify` - the same
keyword filter and LLM prompt the live scanner uses - and EVERY classified filing is
stored, the LLM's "not a split" verdicts included, so the classifier can be audited
and a re-run never pays to classify the same filing twice.

Why full-text search rather than the daily index the live scanner reads: the index
lists ~300 8-K/6-Ks a day, all of which the live scanner downloads to keyword-filter
itself. Over seven years that is ~570,000 downloads; the search returns the ~30 a day
that would have passed that filter. The search phrases are the scanner's own keywords
minus its bare-ratio patterns ("1-for-10", "1:10"), which cannot be expressed as a
search and almost never appear in a split filing without one of the phrases.

Resumable: a finished day is recorded in `events_v2_progress` and skipped next time.
Needs MONGODB_URI, OPENAI_API_KEY and SEC_USER_AGENT.
"""
import argparse
import concurrent.futures
import re
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "src"))

from split_strategy.backtest.events import EVENTS_V2_COLLECTION  # noqa: E402
from split_strategy.config import OPENAI_API_KEY  # noqa: E402
from split_strategy.edgar import client as edgar_client  # noqa: E402
from split_strategy.edgar import scanner  # noqa: E402

PROGRESS_COLLECTION = "events_v2_progress"
FTS_URL = "https://efts.sec.gov/LATEST/search-index"
FTS_QUERY = ('"reverse stock split" OR "reverse split" OR "stock consolidation" OR '
             '"share consolidation" OR "consolidation of shares" OR "share combination"')
FORMS = "8-K,6-K"  # root forms; the search includes their amendments
CLASSIFIER = "gpt-4o-mini"

_DISPLAY = re.compile(r"^(?P<name>.*?)\s+(?:\((?P<tickers>[A-Z][A-Z0-9.,\- ]*)\)\s+)?\(CIK \d+\)\s*$")


def parse_display_name(display: str) -> tuple[str, str | None]:
    """Split 'ACME CORP  (ACME, ACMEW)  (CIK 0001234567)' into name and first ticker."""
    m = _DISPLAY.match(display or "")
    if not m:
        return (display or "").strip(), None
    tickers = m.group("tickers")
    return m.group("name").strip(), (tickers.split(",")[0].strip() if tickers else None)


def filings_from_hits(hits: list[dict]) -> list[dict]:
    """One filing per accession number from full-text-search hits (one hit per document)."""
    out: dict[str, dict] = {}
    for hit in hits:
        src = hit.get("_source") or {}
        adsh, ciks = src.get("adsh"), src.get("ciks") or []
        if not adsh or not ciks or adsh in out:
            continue
        name, ticker = parse_display_name((src.get("display_names") or [""])[0])
        out[adsh] = {
            "adsh": adsh,
            "cik": ciks[0],
            "company_name": name,
            "listed_ticker": ticker,
            "form": src.get("form") or src.get("file_type"),
            "filing_date": src.get("file_date"),
            "filing_url": (f"https://www.sec.gov/Archives/edgar/data/"
                           f"{int(ciks[0])}/{adsh}.txt"),
        }
    return list(out.values())


def search_day(day: date) -> list[dict]:
    """Every 8-K/6-K filed on `day` that mentions a reverse split."""
    hits, start = [], 0
    while True:
        for attempt in range(4):  # the search endpoint returns sporadic 500s
            edgar_client._SEC_LIMITER.acquire()
            resp = requests.get(FTS_URL, headers=edgar_client.HEADERS, timeout=60, params={
                "q": FTS_QUERY, "forms": FORMS, "dateRange": "custom",
                "startdt": day.isoformat(), "enddt": day.isoformat(), "from": start})
            if resp.status_code < 500 and resp.status_code != 429:
                break
            time.sleep(2.0 * (attempt + 1))
        resp.raise_for_status()
        body = resp.json().get("hits") or {}
        page = body.get("hits") or []
        hits.extend(page)
        start += len(page)
        if not page or start >= (body.get("total") or {}).get("value", 0):
            return filings_from_hits(hits)


def event_doc(filing: dict, analysis: dict) -> dict:
    """The stored record of one classified filing."""
    cover = analysis.get("trading_symbol")
    ticker = cover or filing.get("listed_ticker") or "UNKNOWN"
    return {
        "adsh": filing["adsh"],
        "cik": str(filing["cik"]).zfill(10),
        "company_name": filing["company_name"],
        "form": filing["form"],
        "filing_date": filing["filing_date"],
        "accepted_at": analysis.get("accepted_at"),
        "ticker": ticker,
        # "cover": the filer's own cover page, point-in-time. "listed": EDGAR's
        # current symbol for the CIK - wrong if the company has since been renamed.
        "ticker_source": "cover" if cover else ("listed" if ticker != "UNKNOWN" else None),
        "filing_url": filing["filing_url"],
        "is_reverse_split": bool(analysis.get("is_reverse_split")),
        "is_future_split": analysis.get("is_future_split"),
        "is_definitive": bool(analysis.get("is_definitive_split_announcement")),
        "effective_date": analysis.get("effective_date"),
        "ratio": analysis.get("ratio"),
        "rounding_up": analysis.get("rounding_up"),
        "confidence": analysis.get("confidence"),
        "summary": analysis.get("summary"),
        "classifier": CLASSIFIER,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }


def classify_filing(filing: dict) -> dict | None:
    """Download and classify one filing. None = no keyword in the text. Raises on a
    fetch or LLM failure so the day is left unfinished and retried."""
    raw = scanner.fetch_submission(filing["filing_url"])
    # The prompt gets the date in the live scanner's format (YYYYMMDD).
    analysis = scanner.classify(raw, filing["company_name"],
                                (filing["filing_date"] or "").replace("-", ""),
                                OPENAI_API_KEY)
    if analysis is None:
        return None
    if analysis.get("confidence") == "Error":
        raise RuntimeError(f"LLM failed: {analysis.get('summary')}")
    return event_doc(filing, analysis)


def weekdays(start: date, end: date):
    day = start
    while day <= end:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill events_v2 from EDGAR history")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--max-llm", type=int, default=0,
                    help="stop after this many LLM classifications (0 = no cap)")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    if not OPENAI_API_KEY:
        print("Error: OPENAI_API_KEY not set.")
        return 1
    from split_strategy.database import get_collection
    events = get_collection(EVENTS_V2_COLLECTION)
    progress = get_collection(PROGRESS_COLLECTION)

    done_days = {d["date"] for d in progress.find({}, {"date": 1})}
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    llm_calls = splits = 0
    lock = threading.Lock()

    for day in weekdays(start, end):
        key = day.isoformat()
        if key in done_days:
            continue
        if args.max_llm and llm_calls >= args.max_llm:
            print(f"Stopping at {key}: reached --max-llm {args.max_llm}.")
            break
        try:
            filings = search_day(day)
        except Exception as e:
            print(f"{key}: search failed ({str(e)[:120]}); will retry next run")
            continue
        have = {d["adsh"] for d in events.find(
            {"adsh": {"$in": [f["adsh"] for f in filings]}}, {"adsh": 1})}
        todo = [f for f in filings if f["adsh"] not in have]

        errors = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(classify_filing, f): f for f in todo}
            for fut in concurrent.futures.as_completed(futures):
                try:
                    doc = fut.result()
                except Exception as e:
                    errors += 1
                    print(f"  {futures[fut]['adsh']}: {str(e)[:140]}")
                    continue
                if doc is None:
                    continue
                events.update_one({"adsh": doc["adsh"]}, {"$set": doc}, upsert=True)
                with lock:
                    llm_calls += 1
                    splits += bool(doc["is_reverse_split"] and doc["is_definitive"])

        print(f"{key}: {len(filings)} filings, {len(todo)} new, {errors} errors "
              f"| running total: {llm_calls} classified, {splits} definitive splits")
        if not errors:
            progress.update_one({"date": key}, {"$set": {
                "date": key, "filings": len(filings),
                "done_at": datetime.now(timezone.utc).isoformat()}}, upsert=True)

    print(f"Done. {llm_calls} filings classified, {splits} definitive splits.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
