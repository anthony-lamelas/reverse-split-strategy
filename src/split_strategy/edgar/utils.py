"""
Utility functions for EDGAR processing.
"""
import re
from datetime import datetime, timedelta
from typing import Optional, Dict

def normalize_cik(cik: str) -> str:
    """Normalize CIK to 10 digits"""
    return str(cik).strip().zfill(10)

def primary_ticker_by_cik(ticker_to_cik: Dict[str, str]) -> Dict[str, str]:
    """Invert a ticker -> CIK mapping, keeping the FIRST ticker listed for each CIK.

    One company files under one CIK but can list several securities: common stock,
    warrants, preferred, notes. The SEC's ticker files put the common stock first
    (WHLR, WHLRD, WHLRP, WHLRL). Keeping the last one, as the scanner used to, tagged
    the company's reverse split with its warrant or preferred symbol - VEEAW for VEEA,
    WHLRL (an $80 note) for WHLR - so the live run quoted, floored and rejected a
    security the split does not even apply to.

    Each CIK is keyed both zero-padded and as a plain integer string, because callers
    hold it in either form.
    """
    out: Dict[str, str] = {}
    for ticker, cik in ticker_to_cik.items():
        out.setdefault(cik, ticker)
        out.setdefault(str(int(cik)), ticker)
    return out


def scan_target_dates(today, is_trading_day) -> list:
    """Filing dates a scan run on `today` must cover, oldest first.

    Everything from the last market session before `today` through `today` itself.
    "Yesterday and today" is the same thing Tuesday to Friday, but on a Monday it
    looked at Sunday and Monday and skipped Friday's filings entirely - which are
    exactly the ones whose entry is that Monday's open.
    """
    start = today - timedelta(days=1)
    # Bounded so a calendar gap can't spin forever.
    for _ in range(15):
        if is_trading_day(start):
            break
        start -= timedelta(days=1)
    return [start + timedelta(days=i) for i in range((today - start).days + 1)]


def search_cik_by_company_name(company_name: str, name_mapping: Dict[str, str] = None) -> Optional[str]:
    """Fallback: Search for CIK by company name"""
    if not company_name:
        return None
    
    # Clean company name
    clean_name = company_name.strip().upper()
    # Remove common suffixes for better matching
    for suffix in [" INC", " INC.", " CORPORATION", " CORP", " CORP.", " LLC", " LTD", " LTD.", " COMPANY", " CO", " CO."]:
        if clean_name.endswith(suffix):
            clean_name = clean_name[:-len(suffix)].strip()
    
    # Try direct lookup if mapping provided
    if name_mapping:
        if clean_name in name_mapping:
            return name_mapping[clean_name]
        # Try partial match
        for mapped_name, cik in name_mapping.items():
            if clean_name in mapped_name or mapped_name in clean_name:
                return cik
    
    return None

def parse_date(date_str: str) -> Optional[str]:
    """Parse date string to YYYY-MM-DD format"""
    if not date_str:
        return None
    
    try:
        dt = datetime.strptime(date_str.strip(), "%m/%d/%Y")
        return dt.strftime("%Y-%m-%d")
    except:
        try:
            dt = datetime.strptime(date_str.strip(), "%Y-%m-%d")
            return dt.strftime("%Y-%m-%d")
        except:
            return None

def get_date_window(split_date_str: Optional[str]) -> tuple:
    """Calculate EDGAR query window: [T-180d, T+15d] or [today-365d, today]"""
    if split_date_str:
        split_date = parse_date(split_date_str)
        if split_date:
            try:
                split_dt = datetime.strptime(split_date, "%Y-%m-%d")
                start = (split_dt - timedelta(days=180)).strftime("%Y-%m-%d")
                end = (split_dt + timedelta(days=15)).strftime("%Y-%m-%d")
                return start, end
            except:
                pass
    
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    return start, end
