"""Research research: issuer tender offers with odd-lot priority, and a check of the
round-up flag. Runs on Modal for the SEC user agent and OpenAI key; returns data to
the local caller, stores nothing in MongoDB."""
import json, re, sys, random
import modal

R = str(__import__("pathlib").Path(__file__).resolve().parents[2])
DATA = str(__import__("pathlib").Path(__file__).resolve().parent / "data")
img = (modal.Image.debian_slim(python_version="3.12")
       .pip_install("pandas", "pymongo", "python-dotenv", "requests", "beautifulsoup4", "openai")
       .add_local_dir(R + "/src", remote_path="/app/src"))
app = modal.App("split-strategy-tender-research")
secret = modal.Secret.from_name("split-strategy-secrets")

PROMPT = """You are reading an SEC Schedule TO-I filing by {company} (filed {date}).
Extract the terms of the tender offer. Return JSON with exactly these keys:
- "is_issuer_tender": true only if the company is offering to buy back its OWN shares for cash.
- "security": what is being bought, e.g. "common stock", "preferred stock", "notes", "fund shares", "options", "warrants".
- "is_fund": true if the issuer is a closed-end fund, interval fund, BDC, REIT that is not exchange-traded, or the price is based on net asset value.
- "offer_type": "fixed" (one price), "dutch_auction" (a price range), or "other".
- "price_low": lowest cash price per share as a number, or null.
- "price_high": highest cash price per share as a number (same as price_low for a fixed price), or null.
- "expiration_date": scheduled expiration as YYYY-MM-DD, or null.
- "odd_lot_priority": true if holders of fewer than 100 shares who tender all their shares are accepted first / not subject to proration.
- "max_dollars": the maximum total dollar amount of the offer as a number, or null.
- "conditions": short phrase naming any financing or minimum-tender condition, or "none".

Text:
{context}
"""

def _context(text):
    parts = [text[:3000]]
    for pat, n, w in ((r"odd lot", 2, 1500), (r"purchase price|price per share|per share", 3, 1000),
                      (r"expir", 2, 700), (r"proration|prorat", 1, 900)):
        for m in list(re.finditer(pat, text, re.I))[:n]:
            parts.append(text[max(0, m.start() - w): m.end() + w])
    return "\n...\n".join(parts)[:15000]

@app.function(image=img, secrets=[secret], timeout=3600)
def scan(start: str, end: str) -> list:
    import os, time, requests, concurrent.futures
    sys.path.insert(0, "/app/src")
    from split_strategy.edgar import client as ec, scanner
    from openai import OpenAI
    oa = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    hits, frm = [], 0
    while True:
        for attempt in range(4):
            ec._SEC_LIMITER.acquire()
            r = requests.get("https://efts.sec.gov/LATEST/search-index", headers=ec.HEADERS, timeout=60,
                             params={"q": '"odd lot" OR "odd lots"', "forms": "SC TO-I", "dateRange": "custom",
                                     "startdt": start, "enddt": end, "from": frm})
            if r.status_code < 500 and r.status_code != 429: break
            time.sleep(2 * (attempt + 1))
        r.raise_for_status()
        body = r.json()["hits"]; page = body["hits"]; hits += page; frm += len(page)
        if not page or frm >= body["total"]["value"]: break
    filings = {}
    for h in hits:
        s = h["_source"]
        if s.get("form") != "SC TO-I" or s["adsh"] in filings: continue   # initial filing only
        dn = (s.get("display_names") or [""])[0]
        m = re.search(r"\(([A-Z][A-Z0-9.\-]*)[,)]", dn)
        filings[s["adsh"]] = dict(adsh=s["adsh"], cik=s["ciks"][0], company=dn.split("  (")[0],
                                  listed_ticker=m.group(1) if m else None, filing_date=s["file_date"],
                                  url=f"https://www.sec.gov/Archives/edgar/data/{int(s['ciks'][0])}/{s['adsh']}.txt")
    def one(f):
        try:
            raw = scanner.fetch_submission(f["url"])
            text = scanner.filing_text(raw)
            resp = oa.chat.completions.create(model="gpt-4o-mini", temperature=0,
                response_format={"type": "json_object"},
                messages=[{"role": "user", "content": PROMPT.format(company=f["company"], date=f["filing_date"], context=_context(text))}])
            return {**f, **json.loads(resp.choices[0].message.content),
                    "accepted_at": scanner.parse_acceptance(raw), "cover_symbol": scanner.parse_trading_symbol(raw)}
        except Exception as e:
            return {**f, "error": str(e)[:150]}
    with concurrent.futures.ThreadPoolExecutor(6) as pool:
        out = list(pool.map(one, filings.values()))
    print(start, end, "hits", len(hits), "initial filings", len(filings), "errors", sum("error" in o for o in out))
    return out

@app.function(image=img, secrets=[secret], timeout=1800)
def check_roundup(items: list) -> list:
    sys.path.insert(0, "/app/src")
    from split_strategy.edgar import scanner
    out = []
    for it in items:
        try:
            text = scanner.filing_text(scanner.fetch_submission(it["url"]))
        except Exception as e:
            out.append({**it, "error": str(e)[:100]}); continue
        frac = [m.start() for m in re.finditer(r"fraction", text, re.I)]
        near = " ".join(text[max(0, i - 400): i + 600] for i in frac[:8])
        out.append({**it, "says_round_up": bool(re.search(r"round(ed|ing)?\s+(up|to the (next|nearest) whole)", near, re.I)),
                    "says_cash": bool(re.search(r"cash[^.]{0,60}in lieu|in lieu[^.]{0,80}cash", near, re.I)),
                    "snippet": re.sub(r"\s+", " ", near[:0] + (re.search(r"[^.]*fraction[^.]*\.[^.]*\.", near, re.I).group(0) if re.search(r"[^.]*fraction[^.]*\.[^.]*\.", near, re.I) else ""))[:300]})
    return out

@app.local_entrypoint()
def main():
    sys.path.insert(0, R + "/src")
    docs = json.load(open(R + "/DATA/events_v2.json"))
    random.seed(7)
    win = [d for d in docs if "2021-10-12" <= d["filing_date"] < "2025-10-01" and d.get("filing_url")]
    sample = ([dict(url=d["filing_url"], flag=True) for d in random.sample([d for d in win if d.get("rounding_up") is True], 40)]
              + [dict(url=d["filing_url"], flag=False) for d in random.sample([d for d in win if d.get("rounding_up") is False], 25)])
    ranges = [("2021-10-01", "2022-09-30"), ("2022-10-01", "2023-09-30"), ("2023-10-01", "2024-09-30"), ("2024-10-01", "2025-09-30")]
    chk = check_roundup.spawn(sample)
    tenders = [t for part in scan.starmap(ranges) for t in part]
    json.dump(tenders, open(DATA + "/tenders.json", "w"))
    json.dump(chk.get(), open(DATA + "/roundup_check.json", "w"))
    print("TENDERS", len(tenders))
