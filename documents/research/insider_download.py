"""Research: open-market insider purchases from the SEC's quarterly insider data sets."""
import io, json, sys, zipfile
import modal
DATA = str(__import__("pathlib").Path(__file__).resolve().parent / "data")
img = modal.Image.debian_slim(python_version="3.12").pip_install("pandas", "requests")
app = modal.App("split-strategy-insider-research")

@app.function(image=img, secrets=[modal.Secret.from_name("split-strategy-secrets")], timeout=1800, memory=4096)
def quarter(q: str) -> str:
    import os, requests, pandas as pd
    url = f"https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{q}_form345.zip"
    r = requests.get(url, headers={"User-Agent": os.environ["SEC_USER_AGENT"]}, timeout=300)
    if r.status_code != 200:
        return json.dumps({"q": q, "error": r.status_code})
    z = zipfile.ZipFile(io.BytesIO(r.content))
    def tsv(name):
        return pd.read_csv(z.open(name), sep="\t", dtype=str, low_memory=False)
    sub, own, tr = tsv("SUBMISSION.tsv"), tsv("REPORTINGOWNER.tsv"), tsv("NONDERIV_TRANS.tsv")
    tr = tr[(tr["TRANS_CODE"] == "P") & (tr["TRANS_ACQUIRED_DISP_CD"] == "A")]
    m = (tr.merge(sub[["ACCESSION_NUMBER", "FILING_DATE", "ISSUERCIK", "ISSUERTRADINGSYMBOL", "DOCUMENT_TYPE"]], on="ACCESSION_NUMBER")
           .merge(own[["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE"]].drop_duplicates("ACCESSION_NUMBER"), on="ACCESSION_NUMBER"))
    m = m[m["DOCUMENT_TYPE"] == "4"][["ACCESSION_NUMBER", "FILING_DATE", "ISSUERCIK", "ISSUERTRADINGSYMBOL", "RPTOWNERCIK",
                                      "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE", "TRANS_DATE", "TRANS_SHARES", "TRANS_PRICEPERSHARE"]]
    print(q, "purchases", len(m), flush=True)
    return m.to_json(orient="records")

@app.local_entrypoint()
def main():
    import pandas as pd
    qs = [f"{y}q{k}" for y in range(2021, 2027) for k in range(1, 5) if "2021q3" <= f"{y}q{k}" <= "2026q3"]
    frames, errs = [], []
    for res in quarter.map(qs):
        d = json.loads(res)
        if isinstance(d, dict): errs.append(d); continue
        frames.append(pd.DataFrame(d))
    df = pd.concat(frames, ignore_index=True)
    df.to_pickle(DATA + "/insider_purchases.pkl")
    print("INSIDER rows", len(df), "errors", errs)
