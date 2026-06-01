"""
EDGAR Client for SEC API interaction.
"""
import os
import json
from pathlib import Path
import requests
import time
from typing import Optional, Dict

from ..config import SEC_BASE_URL, SEC_ARCHIVES_URL, REQUEST_DELAY, SEC_USER_AGENT
from .utils import normalize_cik

HEADERS = {
    "User-Agent": SEC_USER_AGENT,
    "Accept": "application/json"
}

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# Cache paths
CACHE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "DATA"
ACTIVE_CIK_CACHE = CACHE_DIR / "company_tickers_cache.json"
EXCHANGE_CIK_CACHE = CACHE_DIR / "company_tickers_exchange_cache.json"

def get_cik_mapping_with_names() -> Dict[str, Dict[str, str]]:
    """Fetch CIK mapping with both ticker and company name lookups, using local cache fallback if rate-limited"""
    ticker_mapping = {}
    name_mapping = {}
    
    max_retries = 6
    base_delay = 1.5
    
    # Ensure cache directory exists
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    
    # 1. Fetch standard active company_tickers.json
    fetched_active = False
    data = None
    for attempt in range(max_retries):
        try:
            # Pacing delay
            time.sleep(REQUEST_DELAY * (attempt + 1) * 0.2)
            response = requests.get(COMPANY_TICKERS_URL, headers=HEADERS)
            
            if response.status_code == 429:
                delay = base_delay * (2 ** attempt)
                print(f"  [HTTP 429] SEC Rate Limit hit on active CIK mapping. Backing off {delay:.1f}s (Attempt {attempt+1}/{max_retries})...")
                time.sleep(delay)
                continue
            elif response.status_code >= 500:
                delay = base_delay * (attempt + 1)
                print(f"  [HTTP {response.status_code}] SEC Server Error for active CIK mapping. Retrying in {delay:.1f}s...")
                time.sleep(delay)
                continue
                
            response.raise_for_status()
            data = response.json()
            fetched_active = True
            
            # Save to local cache
            try:
                with open(ACTIVE_CIK_CACHE, "w", encoding="utf-8") as f:
                    json.dump(data, f)
            except Exception as cache_err:
                print(f"  Warning: Failed to save active CIK mapping cache: {cache_err}")
            break
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"Error fetching standard CIK mapping after {max_retries} attempts: {e}")
            else:
                time.sleep(base_delay * (attempt + 1))
                
    if not fetched_active:
        # Fallback to local cache
        if ACTIVE_CIK_CACHE.exists():
            try:
                print(f"  [Fallback] Loading active CIK mapping from local cache: {ACTIVE_CIK_CACHE.name}")
                with open(ACTIVE_CIK_CACHE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as cache_read_err:
                print(f"  Error reading local active CIK cache: {cache_read_err}")
        else:
            print("  Warning: No local active CIK mapping cache available.")
            
    if data:
        for entry in data.values():
            ticker = entry.get("ticker", "").upper()
            title = entry.get("title", "").upper()
            cik = str(entry.get("cik_str", "")).zfill(10)
            
            if ticker and cik:
                ticker_mapping[ticker] = cik
            
            if title and cik:
                # Store normalized company name -> CIK
                clean_title = title
                for suffix in [" INC", " INC.", " CORPORATION", " CORP", " CORP.", " LLC", " LTD", " LTD.", " COMPANY", " CO", " CO."]:
                    if clean_title.endswith(suffix):
                        clean_title = clean_title[:-len(suffix)].strip()
                if clean_title:
                    name_mapping[clean_title] = cik

    # 2. Fetch company_tickers_exchange.json (broader coverage, including OTC and delisted)
    fetched_exchange = False
    exchange_data = None
    for attempt in range(max_retries):
        try:
            # Pacing delay
            time.sleep(REQUEST_DELAY * (attempt + 1) * 0.2)
            response = requests.get("https://www.sec.gov/files/company_tickers_exchange.json", headers=HEADERS)
            
            if response.status_code == 429:
                delay = base_delay * (2 ** attempt)
                print(f"  [HTTP 429] SEC Rate Limit hit on exchange CIK mapping. Backing off {delay:.1f}s (Attempt {attempt+1}/{max_retries})...")
                time.sleep(delay)
                continue
            elif response.status_code >= 500:
                delay = base_delay * (attempt + 1)
                print(f"  [HTTP {response.status_code}] SEC Server Error for exchange CIK mapping. Retrying in {delay:.1f}s...")
                time.sleep(delay)
                continue
                
            response.raise_for_status()
            exchange_data = response.json()
            fetched_exchange = True
            
            # Save to local cache
            try:
                with open(EXCHANGE_CIK_CACHE, "w", encoding="utf-8") as f:
                    json.dump(exchange_data, f)
            except Exception as cache_err:
                print(f"  Warning: Failed to save exchange CIK mapping cache: {cache_err}")
            break
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"Error fetching exchange CIK mapping after {max_retries} attempts: {e}")
            else:
                time.sleep(base_delay * (attempt + 1))
                
    if not fetched_exchange:
        # Fallback to local cache
        if EXCHANGE_CIK_CACHE.exists():
            try:
                print(f"  [Fallback] Loading exchange CIK mapping from local cache: {EXCHANGE_CIK_CACHE.name}")
                with open(EXCHANGE_CIK_CACHE, "r", encoding="utf-8") as f:
                    exchange_data = json.load(f)
            except Exception as cache_read_err:
                print(f"  Error reading local exchange CIK cache: {cache_read_err}")
        else:
            print("  Warning: No local exchange CIK mapping cache available.")
            
    if exchange_data:
        fields = exchange_data.get("fields", [])
        data_rows = exchange_data.get("data", [])
        
        if "cik" in fields and "ticker" in fields:
            cik_idx = fields.index("cik")
            ticker_idx = fields.index("ticker")
            name_idx = fields.index("name") if "name" in fields else -1
            
            for row in data_rows:
                if len(row) > max(cik_idx, ticker_idx):
                    ticker = str(row[ticker_idx]).upper().strip()
                    cik = str(row[cik_idx]).zfill(10)
                    
                    if ticker and cik:
                        # Don't overwrite standard one, but backfill
                        if ticker not in ticker_mapping:
                            ticker_mapping[ticker] = cik
                            
                    if name_idx != -1 and len(row) > name_idx:
                        name = str(row[name_idx]).upper().strip()
                        clean_name = name
                        for suffix in [" INC", " INC.", " CORPORATION", " CORP", " CORP.", " LLC", " LTD", " LTD.", " COMPANY", " CO", " CO."]:
                            if clean_name.endswith(suffix):
                                clean_name = clean_name[:-len(suffix)].strip()
                        if clean_name and clean_name not in name_mapping:
                                name_mapping[clean_name] = cik
                                
    return {"ticker": ticker_mapping, "name": name_mapping}

def get_company_filings(cik: str) -> Optional[Dict]:
    """Fetch recent filings for a CIK with robust retry and backoff logic"""
    cik_normalized = normalize_cik(cik)
    url = f"{SEC_BASE_URL}/submissions/CIK{cik_normalized}.json"
    
    max_retries = 6
    base_delay = 1.5
    
    for attempt in range(max_retries):
        try:
            # Add small pacing delay to reduce instant concurrency spikes
            time.sleep(REQUEST_DELAY * (attempt + 1) * 0.2)
            response = requests.get(url, headers=HEADERS)
            
            if response.status_code == 429:
                delay = base_delay * (2 ** attempt)
                print(f"  [HTTP 429] SEC Rate Limit hit on submissions for CIK {cik_normalized}. Backing off {delay:.1f}s (Attempt {attempt+1}/{max_retries})...")
                time.sleep(delay)
                continue
            elif response.status_code >= 500:
                delay = base_delay * (attempt + 1)
                print(f"  [HTTP {response.status_code}] SEC Server Error for CIK {cik_normalized}. Retrying in {delay:.1f}s...")
                time.sleep(delay)
                continue
                
            response.raise_for_status()
            return response.json()
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"  Error fetching filings for CIK {cik_normalized} after {max_retries} attempts: {e}")
            else:
                time.sleep(base_delay * (attempt + 1))
                
    return None

def download_filing_text(cik: str, accession: str, primary_doc: str) -> Optional[str]:
    """Download and return filing text with robust retry and backoff logic"""
    cik_normalized = normalize_cik(cik)
    accession_clean = accession.replace("-", "")
    url = f"{SEC_ARCHIVES_URL}/{cik_normalized}/{accession_clean}/{primary_doc}"
    
    max_retries = 6
    base_delay = 1.5
    
    for attempt in range(max_retries):
        try:
            time.sleep(REQUEST_DELAY * (attempt + 1) * 0.2)
            response = requests.get(url, headers=HEADERS)
            
            if response.status_code == 429:
                delay = base_delay * (2 ** attempt)
                print(f"  [HTTP 429] SEC Rate Limit hit on filing {accession}. Backing off {delay:.1f}s (Attempt {attempt+1}/{max_retries})...")
                time.sleep(delay)
                continue
            elif response.status_code >= 500:
                delay = base_delay * (attempt + 1)
                print(f"  [HTTP {response.status_code}] SEC Server Error on filing {accession}. Retrying in {delay:.1f}s...")
                time.sleep(delay)
                continue
                
            response.raise_for_status()
            return response.text
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"    Error downloading filing {accession} after {max_retries} attempts: {e}")
            else:
                time.sleep(base_delay * (attempt + 1))
                
    return None

def get_daily_index_url(date_obj) -> str:
    """Construct URL for daily index file"""
    year = date_obj.year
    qtr = (date_obj.month - 1) // 3 + 1
    date_str = date_obj.strftime("%Y%m%d")
    return f"https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{qtr}/company.{date_str}.idx"

def parse_idx_line(line: str) -> Optional[Dict]:
    """Parse a fixed-width line from company.YYYYMMDD.idx"""
    import re
    parts = re.split(r'\s{2,}', line.strip())
    if len(parts) < 5:
        return None
    
    filename = parts[-1]
    date_str = parts[-2]
    cik = parts[-3]
    form_type = parts[-4]
    company_name = " ".join(parts[:-4])
    
    return {
        "company_name": company_name,
        "form": form_type,
        "cik": cik,
        "date_filed": date_str,
        "filename": filename
    }

def fetch_daily_filings(date_obj, target_forms=None) -> list:
    """Download and parse daily index"""
    url = get_daily_index_url(date_obj)
    print(f"Fetching Daily Index: {url}")
    
    max_retries = 5
    base_delay = 1.0
    
    for attempt in range(max_retries):
        try:
            time.sleep(base_delay * (attempt + 1) * 0.5)
            response = requests.get(url, headers=HEADERS)
            
            if response.status_code in [404, 403]:
                # 403 can sometimes be a ban, but usually 404/403 means no file yet (weekend/holiday)
                print(f"  No index found (Status {response.status_code})")
                return []
                
            if response.status_code == 429 or response.status_code >= 500:
                print(f"  [Attempt {attempt+1}/{max_retries}] SEC returned {response.status_code} for index, backing off...")
                if attempt == max_retries - 1:
                    response.raise_for_status()
                time.sleep(base_delay * (2 ** attempt))
                continue
                
            response.raise_for_status()
            
            lines = response.text.splitlines()
            filings = []
            
            start_parsing = False
            for line in lines:
                if "---" in line:
                    start_parsing = True
                    continue
                if not start_parsing:
                    continue
                
                entry = parse_idx_line(line)
                if entry:
                    if target_forms:
                         if entry["form"] in target_forms:
                            filings.append(entry)
                    else:
                        filings.append(entry)
            
            return filings
            
        except requests.exceptions.HTTPError as e:
            if attempt == max_retries - 1:
                print(f"Error fetching index after {max_retries} attempts: {e}")
                return []
        except Exception as e:
            print(f"Unexpected error fetching index: {e}")
            return []
            
    return []

