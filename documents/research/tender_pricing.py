"""Research: buy 99 shares the session after an issuer tender offer with odd-lot priority.

Input: data/tenders.json from tender_scan.py. Assumes each offer completed as announced
(final outcomes were not collected). Dutch auctions are shown at both ends of the range.
"""
import json, sys, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT / "src"))
from split_strategy.backtest.prices_v2 import PolygonPrices
from split_strategy.live import calendar as mcal

t = pd.DataFrame(json.load(open(Path(__file__).parent / "data" / "tenders.json")))
print("SC TO-I filings mentioning odd lots, Oct 2021 - Sep 2025:", len(t), "| funds:", int((t.is_fund == True).sum()))
k = t[(t.is_issuer_tender == True) & (t.is_fund != True) & (t.odd_lot_priority == True)
      & t.security.str.lower().str.contains("common|share|stock|ordinary", na=False)
      & t.price_low.notna() & t.expiration_date.notna()].copy()
k["ticker"] = k.cover_symbol.fillna(k.listed_ticker)
print("operating companies, common stock, odd-lot priority, priced and dated:", len(k), k.groupby(k.filing_date.str[:4]).size().to_dict())
src, rows = PolygonPrices(), []
for _, r in k[k.ticker.notna()].iterrows():
    day = mcal.first_open_after(pd.Timestamp(r.accepted_at) if r.accepted_at else pd.Timestamp(r.filing_date))
    exp = pd.Timestamp(r.expiration_date)
    b = src.daily(r.ticker, "2021-09-01", "2025-11-15")
    if b.empty or day not in b.index or exp <= day: continue
    entry = float(b.loc[day, "Open"])
    rows.append(dict(ticker=r.ticker, filed=str(r.filing_date)[:10], type=r.offer_type, low=r.price_low, high=r.price_high,
                     entry=entry, prem_low=r.price_low / entry - 1, prem_high=r.price_high / entry - 1,
                     days=(exp - day).days, profit99_low=99 * (r.price_low - entry), cost99=99 * entry))
d = pd.DataFrame(rows); pd.set_option("display.width", 220)
print(d.round(3).to_string(index=False))
f = d[(d.type == "fixed") & (d.prem_low > 0.005)]
print(f"\nfixed-price offers bought below the offer: {len(f)}; profit on 99 shares total ${f.profit99_low.sum():.0f} "
      f"(median ${f.profit99_low.median():.0f}), capital ${f.cost99.median():.0f} for {f.days.median():.0f} days")
print("note: IMO's offers are priced in Canadian dollars and ATIP's entry price looks wrong; neither is a real profit.")
