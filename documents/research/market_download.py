"""Research: download the whole US stock market's daily bars from Polygon (grouped
endpoint), split-adjusted and unadjusted, plus the ticker type list."""
import sys, time, concurrent.futures as cf
import pandas as pd, requests
from dotenv import dotenv_values
R = str(__import__("pathlib").Path(__file__).resolve().parents[2])
DATA = str(__import__("pathlib").Path(__file__).resolve().parent / "data")
KEY = dotenv_values(R + "/.env")["POLYGON_API_KEY"]
OUT = R + "/DATA/prices_v2/market"
import os; os.makedirs(OUT, exist_ok=True)
sess = requests.Session()

def get(url, **p):
    for a in range(6):
        r = sess.get(url, params={**p, "apiKey": KEY}, timeout=90)
        if r.status_code == 200: return r.json()
        time.sleep(2 * (a + 1))
    raise RuntimeError(f"{url} -> {r.status_code}")

def day(d, adjusted):
    j = get(f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{d}", adjusted=str(adjusted).lower())
    rows = j.get("results") or []
    if not rows: return None
    f = pd.DataFrame(rows)[["T", "o", "h", "l", "c", "v"]]
    f["date"] = pd.Timestamp(d)
    return f

for adjusted, name in ((True, "adj"), (False, "raw")):
    for year in range(2021, 2027):
        path = f"{OUT}/{name}_{year}.parquet"
        if os.path.exists(path): continue
        days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(max(f"{year}-01-01", "2021-10-04"), min(f"{year}-12-31", "2026-10-02"))]
        with cf.ThreadPoolExecutor(6) as pool:
            frames = [f for f in pool.map(lambda d: day(d, adjusted), days) if f is not None]
        if not frames: continue
        df = pd.concat(frames, ignore_index=True)
        for c in "ohlc": df[c] = df[c].astype("float32")
        df.to_parquet(path)
        print(name, year, len(frames), "days", len(df), "rows", flush=True)

if not os.path.exists(f"{OUT}/tickers.parquet"):
    rows = []
    for active in ("true", "false"):
        url, p = "https://api.polygon.io/v3/reference/tickers", dict(market="stocks", active=active, limit=1000)
        while url:
            j = get(url, **p); rows += j.get("results") or []
            url, p = j.get("next_url"), {}
    t = pd.DataFrame(rows)[["ticker", "name", "type", "active", "primary_exchange"]]
    t.to_parquet(f"{OUT}/tickers.parquet"); print("tickers", len(t), t.type.value_counts().head(8).to_dict(), flush=True)
print("DONE")
