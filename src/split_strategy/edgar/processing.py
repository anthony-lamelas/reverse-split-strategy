"""
High-level EDGAR processing workflow.
"""
from datetime import datetime, timezone
from typing import Dict, Optional, List
from bs4 import BeautifulSoup
import math
import re

from bs4 import BeautifulSoup

from ..database import get_collection, get_db
from ..config import SEC_ARCHIVES_URL, REVERSE_SPLITS_COLLECTION, EDGAR_COLLECTION, MONGODB_DATABASE

# Removed incorrect import from .config

from .client import download_filing_text, get_all_company_filings, get_company_filings
from .parsing import (
    check_items, extract_reverse_split_ratio, extract_announcement_date,
    extract_effective_date, check_compliance_flag, check_financing_flag,
    check_unregistered_sales_flag, check_rounding_up_flag, TARGET_FORMS, CONTEXT_FORMS
)
from .scoring import score_filing, parse_sa_ratio
from .utils import normalize_cik, parse_date, get_date_window, search_cik_by_company_name

# Re-import SEC_ARCHIVES_URL properly
# It seems I made a mistake in the import above. relative import from ..config
from ..config import SEC_ARCHIVES_URL

def check_already_processed_reverse_splits(reverse_splits_id: str) -> bool:
    """Check if this split already has EDGAR filings in reverse_splits_edgar"""
    edgar_collection = get_collection(EDGAR_COLLECTION)
    count = edgar_collection.count_documents({"reverse_splits_id": reverse_splits_id})
    return count > 0

def parse_and_score_filing(cik: str, ticker: str, filing: Dict, split_date: str, 
                          sa_ratio: Optional[tuple], sa_effective_date: Optional[datetime],
                          reverse_splits_id: str) -> Optional[Dict]:
    """Parse a filing, extract info, score it, and add reverse_splits_id"""
    form = filing.get("form")
    filing_date = filing.get("filingDate")
    accession = filing.get("accessionNumber")
    primary_doc = filing.get("primaryDocument")
    
    if not accession or not primary_doc:
        return None
    
    # Download filing text
    text = download_filing_text(cik, accession, primary_doc)
    if not text:
        return None
    
    # Parse HTML
    soup = BeautifulSoup(text, 'html.parser')
    text_content = soup.get_text()
    
    # Extract items
    items = check_items(text_content, form)
    
    # Extract ratio
    ratio_data = extract_reverse_split_ratio(text_content)
    
    # Extract dates
    announce_date = extract_announcement_date(text_content, filing_date)
    effective_data = extract_effective_date(text_content)
    
    # Check flags
    compliance_flag = check_compliance_flag(text_content)
    financing_flag = check_financing_flag(text_content)
    unregistered_sales_flag = check_unregistered_sales_flag(text_content, items)
    rounding_up_flag = check_rounding_up_flag(text_content)
    share_change_flag = "3.02" in items
    listing_deficiency_flag = "3.01" in items
    
    # Build result
    result = {
        "reverse_splits_id": reverse_splits_id,  # Link to reverse_splits
        "cik": normalize_cik(cik),
        "ticker": ticker.upper(),
        "form": form,
        "filing_date": filing_date,
        "accession": accession,
        "document_url": f"{SEC_ARCHIVES_URL}/{normalize_cik(cik)}/{accession.replace('-', '')}/{primary_doc}",
        "flags": {
            "compliance_flag": compliance_flag,
            "financing_flag": financing_flag,
            "unregistered_sales_flag": unregistered_sales_flag,
            "rounding_up_flag": rounding_up_flag,
            "share_change_flag": share_change_flag,
            "listing_deficiency_flag": listing_deficiency_flag
        },
        "items": items,
        "last_updated": datetime.now(timezone.utc)
    }
    
    # Add optional fields
    if announce_date:
        result["announce_date"] = announce_date
    if effective_data:
        result["effective_date"] = effective_data.get("effective_date")
        result["effective_time_text"] = effective_data.get("effective_time_text")
    if ratio_data:
        result["ratio_num"] = ratio_data.get("ratio_num")
        result["ratio_den"] = ratio_data.get("ratio_den")
        if ratio_data.get("ratio_num") and ratio_data.get("ratio_den"):
            num = ratio_data["ratio_num"]
            den = ratio_data["ratio_den"]
            if num > 0 and den > num:  # Reverse split check
                result["log_ratio"] = round(math.log(den / num), 4)
    
    # Add text matches for flags
    text_matches = {}
    if compliance_flag:
        match = re.search(r".{0,100}(regain|maintain|minimum bid|deficiency|Nasdaq|NYSE).{0,100}", 
                        text_content, re.IGNORECASE)
        if match:
            text_matches["compliance_text"] = match.group(0)[:200]
    if financing_flag:
        match = re.search(r".{0,100}(registered direct|ATM|S-3|424B5|warrant).{0,100}", 
                        text_content, re.IGNORECASE)
        if match:
            text_matches["financing_text"] = match.group(0)[:200]
    if rounding_up_flag:
        match = re.search(r".{0,150}(rounded\s+up|round\s+up|rounding\s+up).{0,150}", 
                        text_content, re.IGNORECASE)
        if match:
            text_matches["rounding_text"] = match.group(0)[:200]
    result["text_matches"] = text_matches
    
    # Score the filing
    scoring_result = score_filing(result, sa_ratio, sa_effective_date)
    result["score"] = scoring_result["score"]
    result["tier"] = scoring_result["tier"]
    result["candidate_announce_date"] = scoring_result["candidate_announce_date"]
    
    return result


def process_reverse_split_with_edgar(split: Dict, cik_mapping: Dict[str, str],
                                     name_mapping: Dict[str, str] = None,
                                     skip_existing: bool = True,
                                     filings_fetcher=get_all_company_filings) -> Dict:
    """Process a single reverse split from reverse_splits collection.

    `filings_fetcher(cik, start_date, end_date) -> list[dict]` is injectable so the
    pagination + windowing logic can be tested against canned submissions JSON with no
    network access.
    """
    symbol = split.get("Symbol", "")
    split_date = split.get("Date", "")
    split_ratio_str = split.get("Split Ratio", "")
    company_name = split.get("Company Name", "")
    reverse_splits_id = str(split.get("_id"))
    
    # Skip if already processed
    if skip_existing and check_already_processed_reverse_splits(reverse_splits_id):
        return {"symbol": symbol, "status": "already_processed", "filings_processed": 0}
    
    # Get CIK - try ticker first, then company name
    cik = cik_mapping.get(symbol.upper())
    
    # Try stripping suffixes if not found (e.g., TSORF -> TSOR)
    if not cik and len(symbol) > 4:
        if symbol.endswith("F"):
             cik = cik_mapping.get(symbol[:-1])
        elif symbol.endswith("Y"):
             cik = cik_mapping.get(symbol[:-1])

    if not cik and name_mapping:
        cik = search_cik_by_company_name(company_name, name_mapping)
        if cik:
            print(f"  Found CIK via company name: {cik}")
    
    if not cik:
        return {"symbol": symbol, "status": "no_cik", "filings_processed": 0}
    
    # Parse SA data for scoring
    sa_ratio = parse_sa_ratio(split_ratio_str)
    sa_effective_date = None
    if split_date:
        sa_date_parsed = parse_date(split_date)
        if sa_date_parsed:
            sa_effective_date = datetime.strptime(sa_date_parsed, "%Y-%m-%d")
    
    # Get date window
    start_date, end_date = get_date_window(split_date)
    
    # Fetch company filings, including the paginated archive. Reading only
    # `filings.recent` (~1000 most recent) silently dropped older filings and could
    # hide the announcement that defines the trade date - see get_all_company_filings.
    all_filings = filings_fetcher(cik, start_date, end_date)
    if not all_filings:
        return {
            "symbol": symbol,
            "status": "no_filings_data",
            "filings_processed": 0
        }

    # Parse date window
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    # Collect relevant filings
    relevant_filings = []
    for filing in all_filings:
        filing_date_str = filing.get("filingDate")
        if not filing_date_str:
            continue

        try:
            filing_dt = datetime.strptime(filing_date_str, "%Y-%m-%d")
        except (ValueError, TypeError):
            continue

        # Check date window
        if filing_dt < start_dt or filing_dt > end_dt:
            continue

        # Check form type
        form = filing.get("form")
        if form in TARGET_FORMS or form in CONTEXT_FORMS:
            relevant_filings.append({
                "form": form,
                "filingDate": filing_date_str,
                "accessionNumber": filing.get("accessionNumber"),
                "primaryDocument": filing.get("primaryDocument"),
            })

    if not relevant_filings:
        return {
            "symbol": symbol,
            "status": "no_filings_in_window",
            "filings_processed": 0
        }
    
    # Process each filing
    results = []
    for filing in relevant_filings:
        if not filing.get("accessionNumber") or not filing.get("primaryDocument"):
            continue
        
        parsed = parse_and_score_filing(
            cik, symbol, filing, split_date,
            sa_ratio, sa_effective_date, reverse_splits_id
        )
        
        if parsed:
            results.append(parsed)
    
    # Save to MongoDB (reverse_splits_edgar collection)
    if results:
        collection = get_collection(EDGAR_COLLECTION)
        
        inserted = 0
        for result in results:
            filter_query = {"accession": result["accession"], "reverse_splits_id": reverse_splits_id}
            collection.update_one(filter_query, {"$set": result}, upsert=True)
            inserted += 1
        
        # Create index for efficient lookups
        try:
            collection.create_index([("reverse_splits_id", 1)])
            collection.create_index([("cik", 1), ("filing_date", -1)])
        except:
            pass
        
    return {
        "symbol": symbol,
        "status": "success",
        "filings_processed": len(results)
    }
