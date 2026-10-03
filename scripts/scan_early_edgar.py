#!/usr/bin/env python3
"""
Early EDGAR Scanner for Confirmed Reverse Splits
Scans daily SEC filings for DEFINITIVE reverse split announcements.
"""

import sys
import os
import argparse
import concurrent.futures
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Add src to path
current_dir = Path(__file__).resolve().parent
src_path = current_dir.parent / 'src'
sys.path.append(str(src_path))

from pymongo import MongoClient
import requests
from bs4 import BeautifulSoup

from split_strategy.config import MONGODB_URI, MONGODB_DATABASE, OPENAI_API_KEY, HEADERS, SEC_ARCHIVES_URL
from split_strategy.database import get_collection, EARLY_WARNINGS_COLLECTION
from split_strategy.edgar.client import (
    fetch_daily_filings, 
    download_filing_text, 
    get_cik_mapping_with_names
)
from split_strategy.edgar.utils import normalize_cik, primary_ticker_by_cik, scan_target_dates
from split_strategy.live import calendar as mcal
from split_strategy.edgar import scanner


# Target Forms
TARGET_FORMS_SCAN = ["8-K", "6-K", "8-K/A", "6-K/A"]

CACHE_CIK_TO_TICKER = {}

def load_ticker_mapping():
    """Load CIK to Ticker mapping"""
    mappings = get_cik_mapping_with_names()
    CACHE_CIK_TO_TICKER.update(primary_ticker_by_cik(mappings.get("ticker", {})))

def resolve_ticker(cik: str) -> str:
    normalized_cik = str(int(cik))
    return CACHE_CIK_TO_TICKER.get(normalized_cik, "UNKNOWN")

def process_filing(filing: dict) -> dict:
    """Analyze a single filing with automated retry logic."""
    full_url = f"https://www.sec.gov/Archives/{filing['filename']}"
    
    max_retries = 5
    base_delay = 1.0
    
    for attempt in range(max_retries):
        try:
            # Add some jitter to avoid synchronized hammer
            time.sleep(base_delay * (attempt + 1) * 0.5) 
            
            resp = requests.get(full_url, headers=HEADERS)
            if resp.status_code == 429 or resp.status_code >= 500:
                print(f"  [Attempt {attempt+1}/{max_retries}] SEC returned {resp.status_code}, backing off...")
                if attempt == max_retries - 1:
                    resp.raise_for_status() # Give up on last try
                time.sleep(base_delay * (2 ** attempt)) # Exponential backoff
                continue
            
            resp.raise_for_status()
            
            # Keyword filter, then LLM - shared with the historical backfill so the
            # backtest's events come from this exact classifier.
            analysis = scanner.classify(resp.text, filing['company_name'],
                                        filing['date_filed'], OPENAI_API_KEY)
            if analysis is None:
                return None

            print(f"  > Keyword match found for {filing['company_name']}!")

            if not analysis.get("is_reverse_split", False):
                print("    LLM says: Not a reverse split.")
                return None
            
            # Filter out past splits based on confidence or explicit logic
            if not scanner.split_is_ahead(analysis, filing['date_filed']):
                 print(f"    LLM says: Past split (is_future_split=False). Skipping.")
                 return None

            if analysis.get("confidence") == "Low" and "already effective" in analysis.get("summary", "").lower():
                 print("    LLM says: Past split (Low Confidence). Skipping.")
                 return None

            print(f"    Confirmed! Date: {analysis.get('effective_date')}, Ratio: {analysis.get('ratio')}")
            
            return {
                # The symbol on the filing's own cover page when there is one;
                # otherwise resolved from the CIK in main().
                "ticker": analysis.get("trading_symbol") or "UNKNOWN",
                "cik": normalize_cik(filing["cik"]),
                "company_name": filing["company_name"],
                "filing_date": filing["date_filed"],
                # When the market could first know (ET). Not yet used for the entry
                # date - recorded so live and backtest can be compared on it.
                "accepted_at": analysis.get("accepted_at"),
                "form": filing["form"],
                "filing_url": full_url,
                "effective_date": analysis.get("effective_date"),
                "ratio": analysis.get("ratio"),
                "rounding_up": analysis.get("rounding_up"),
                "summary": analysis.get("summary"),
                "confidence": analysis.get("confidence"),
                "found_at": datetime.now(timezone.utc).isoformat()
            }

        except Exception as e:
            if attempt == max_retries - 1:
                print(f"Error processing {filing['filename']} after {max_retries} attempts: {e}")
            else:
                pass # Will retry
    
    return None

def main():
    parser = argparse.ArgumentParser(description="Early Edgar Scanner")
    parser.add_argument("date", nargs="?", default=None)
    args = parser.parse_args()
    
    if not OPENAI_API_KEY:
        print("Error: OPENAI_API_KEY not found in environment variables.")
        sys.exit(1)
    
    print("Loading ticker mappings...")
    load_ticker_mapping()
    
    # Setup MongoDB Collection
    collection = None
    if MONGODB_URI:
        client = MongoClient(MONGODB_URI)
        db = client[MONGODB_DATABASE]
        collection = db[EARLY_WARNINGS_COLLECTION]
        print(f"Connected to MongoDB: {MONGODB_DATABASE}.{EARLY_WARNINGS_COLLECTION}")
    else:
        print("Warning: MONGODB_URI not found. Not saving to database.")
        
    # Determine target dates
    ny_tz = ZoneInfo("America/New_York")
    now_ny = datetime.now(ny_tz)
    
    if args.date:
        try:
            target_dates = [datetime.strptime(args.date, "%Y-%m-%d").date()]
        except ValueError:
            print(f"Invalid date format: {args.date}. Use YYYY-MM-DD.")
            sys.exit(1)
    else:
        # Everything since the last market session, through today (ET) - so a
        # Monday run still picks up Friday's filings.
        target_dates = scan_target_dates(now_ny.date(), mcal.is_trading_day)
        
    for target_date in target_dates:
        print(f"\nStarting Early Edgar Scan for {target_date.strftime('%Y-%m-%d')}...")
        filings = fetch_daily_filings(target_date, target_forms=TARGET_FORMS_SCAN)
        print(f"Found {len(filings)} filings ({', '.join(TARGET_FORMS_SCAN)})")
        
        if not filings:
            continue
            
        hits = []
        
        # Parallel processing of filings
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_filing = {executor.submit(process_filing, filing): filing for filing in filings}
            
            count = 0
            total = len(filings)
            
            for future in concurrent.futures.as_completed(future_to_filing):
                count += 1
                filing = future_to_filing[future]
                print(f"Scanning {count}/{total}: {filing['company_name']}...      ", end="\r")
                
                try:
                    hit = future.result()
                    if hit:
                        if hit['ticker'] == "UNKNOWN":
                            hit['ticker'] = resolve_ticker(hit['cik'])
                        hits.append(hit)
                        
                        # Update MongoDB (if configured)
                        if collection is not None:
                            query = {"filing_url": hit["filing_url"]}
                            update = {"$set": hit}
                            collection.update_one(query, update, upsert=True)
                            print(f"\n[SAVED TO MONGODB] {hit['ticker']} - {hit['summary']}")
                except Exception as e:
                    print(f"\nError processing {filing['company_name']}: {e}")
        
        print(f"\nScan for {target_date.strftime('%Y-%m-%d')} Complete. Found {len(hits)} confirmed splits.")

if __name__ == "__main__":
    main()
