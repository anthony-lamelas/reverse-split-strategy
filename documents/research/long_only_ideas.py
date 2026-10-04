"""Research: three long-only ideas tested against holding the S&P 500 (SPY).
Development window only: nothing dated after 2025-09-30 is used."""
import sys, warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
R = str(__import__("pathlib").Path(__file__).resolve().parents[2]) + "/DATA/prices_v2/market"
DATA = str(__import__("pathlib").Path(__file__).resolve().parent / "data")
END = pd.Timestamp("2025-09-30")

def load(name):
    return pd.concat([pd.read_parquet(f"{R}/{name}_{y}.parquet") for y in range(2021, 2026)], ignore_index=True)

tick = pd.read_parquet(f"{R}/tickers.parquet")
common = set(tick[tick.type == "CS"].ticker)
adj = load("adj"); adj = adj[adj.date <= END]
raw = load("raw"); raw = raw[raw.date <= END]
keep = adj["T"].isin(common | {"SPY", "QQQ"})
adj = adj[keep]; raw = raw[raw["T"].isin(common)]
O = adj.pivot(index="date", columns="T", values="o"); C = adj.pivot(index="date", columns="T", values="c")
V = adj.pivot(index="date", columns="T", values="v")
RC = raw.pivot(index="date", columns="T", values="c").reindex(columns=C.columns)
RV = raw.pivot(index="date", columns="T", values="v").reindex(columns=C.columns)
del adj, raw
dates = C.index; pos = {d: i for i, d in enumerate(dates)}
DV = (RC * RV).rolling(20, min_periods=10).median().shift(1)      # typical $ traded, known the day before
PX = RC.shift(1)
Cf = C.ffill()                                                    # a delisted stock keeps its last price
spy_o, spy_c = O["SPY"], C["SPY"]
print(f"days {len(dates)} ({dates[0].date()}..{dates[-1].date()}), common stocks {len(common & set(C.columns))}")
yrs = (dates[-1] - dates[0]).days / 365.25
for b in ("SPY", "QQQ"):
    tot = C[b].iloc[-1] / C[b].iloc[0] - 1
    print(f"  {b} buy and hold: {tot:+.1%} total, {(1+tot)**(1/yrs)-1:+.1%} a year (price only, no dividends)")

def tstat(x):
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    return x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else np.nan

def event_study(events, holds, label):
    """events: DataFrame[ticker, day] where `day` is the signal day; buy next open."""
    print(f"\n{label}: {len(events)} signals")
    for h in holds:
        rows = []
        for t, d in zip(events.ticker, events.day):
            i = pos.get(d)
            if i is None or t not in O.columns or i + 1 + h >= len(dates): continue
            e = O[t].iloc[i + 1]
            if not e > 0: continue
            x = Cf[t].iloc[i + 1 + h]
            rows.append((dates[i + 1], x / e - 1, spy_c.iloc[i + 1 + h] / spy_o.iloc[i + 1] - 1))
        if len(rows) < 20: print(f"  hold {h:3d}d: too few ({len(rows)})"); continue
        r = pd.DataFrame(rows, columns=["d", "ret", "spy"]); r["ex"] = r.ret - r.spy
        # one number per month, so overlapping trades are not counted as independent
        monthly = r.groupby(r.d.dt.to_period("M")).ex.mean()
        by = r.groupby(r.d.dt.year).ex.mean()
        print(f"  hold {h:3d}d: n={len(r):5d} stock {r.ret.mean():+6.2%} SPY {r.spy.mean():+6.2%} "
              f"extra {r.ex.mean():+6.2%} (median {r.ex.median():+6.2%}) beat SPY {(r.ex>0).mean():.0%} "
              f"t(by month) {tstat(monthly):+.2f} | by year " + " ".join(f"{y}:{v:+.1%}" for y, v in by.items()))

# ---------------------------------------------------------------- 1. insider buying
ins = pd.read_pickle(DATA + "/insider_purchases.pkl")
ins["day"] = pd.to_datetime(ins.FILING_DATE, errors="coerce")
ins["val"] = pd.to_numeric(ins.TRANS_SHARES, errors="coerce") * pd.to_numeric(ins.TRANS_PRICEPERSHARE, errors="coerce")
ins["ticker"] = ins.ISSUERTRADINGSYMBOL.str.upper().str.strip()
rel = ins.RPTOWNER_RELATIONSHIP.fillna("")
ins = ins[(rel.str.contains("Officer|Director")) & ins.val.notna() & ins.day.notna() & ins.ticker.isin(C.columns)]
g = ins.groupby(["ticker", "day", "RPTOWNERCIK"], as_index=False).agg(val=("val", "sum"), title=("RPTOWNER_TITLE", "first"), rel=("RPTOWNER_RELATIONSHIP", "first"))
g = g[g.val >= 10_000]
def to_trading(d):                                  # a filing on a closed day counts on the next session
    i = dates.searchsorted(d); return dates[i] if i < len(dates) else pd.NaT
g["day"] = g.day.map(to_trading); g = g.dropna(subset=["day"])
print(f"\ninsider purchases by officers/directors of $10k+: {len(g)} (after matching to listed common stocks)")

def clusters(min_people, min_total, window=30, cooldown=60):
    out = []
    for t, x in g.sort_values("day").groupby("ticker"):
        last = None
        for d in x.day.unique():
            w = x[(x.day <= d) & (x.day > d - pd.Timedelta(days=window))]
            if w.RPTOWNERCIK.nunique() >= min_people and w.val.sum() >= min_total:
                if last is None or (d - last).days > cooldown:
                    out.append((t, d)); last = d
    return pd.DataFrame(out, columns=["ticker", "day"])

def liquid(ev, px, dv):
    ok = [(PX.at[d, t] >= px and DV.at[d, t] >= dv) if d in PX.index else False for t, d in zip(ev.ticker, ev.day)]
    return ev[np.array(ok, bool)]

print("\n=== 1. INSIDER BUYING (buy next open after the filing)")
for people, total in ((3, 100_000), (2, 100_000), (3, 500_000)):
    ev = clusters(people, total)
    event_study(liquid(ev, 2, 500_000), (20, 60, 120), f"{people}+ insiders, ${total:,}+ within 30 days; price>=$2, $500k+/day")
ev = clusters(3, 100_000)
event_study(liquid(ev, 5, 5_000_000), (20, 60, 120), "3+ insiders, $100k+; bigger stocks only (price>=$5, $5M+/day)")
ceo = g[g.title.fillna("").str.contains("CEO|Chief Executive|CFO|Chief Financial", case=False) & (g.val >= 100_000)][["ticker", "day"]].drop_duplicates()
event_study(liquid(ceo, 2, 500_000), (20, 60, 120), "CEO/CFO buys $100k+; price>=$2, $500k+/day")

# ---------------------------------------------------------------- 2. gap up on volume
print("\n=== 2. BIG JUMP ON BIG VOLUME (buy next open)")
gap = O / C.shift(1) - 1
avgv = V.rolling(20, min_periods=10).mean().shift(1)
for thr in (0.08, 0.15):
    sig = (gap >= thr) & (C >= O) & (V >= 3 * avgv) & (PX >= 5) & (DV >= 5_000_000)
    ev = sig.stack(); ev = ev[ev].reset_index(); ev.columns = ["day", "ticker", "x"]
    event_study(ev[["ticker", "day"]], (5, 20, 60), f"opened {thr:.0%}+ higher, closed above the open, 3x volume; price>=$5, $5M+/day")
sig = (gap <= -0.08) & (V >= 3 * avgv) & (PX >= 5) & (DV >= 5_000_000)
ev = sig.stack(); ev = ev[ev].reset_index(); ev.columns = ["day", "ticker", "x"]
event_study(ev[["ticker", "day"]], (5, 20, 60), "for contrast: opened 8%+ LOWER on 3x volume (buying the drop)")

# ---------------------------------------------------------------- 3. momentum
print("\n=== 3. MOMENTUM (each month, buy last year's biggest winners, hold a month)")
firsts = [dates[dates.searchsorted(m)] for m in pd.date_range("2022-10-01", "2025-08-01", freq="MS")]
res = []
for a, b in zip(firsts[:-1], firsts[1:]):
    i = pos[a]
    if i < 252: continue
    mom = C.iloc[i - 21] / C.iloc[i - 252] - 1
    uni = (PX.iloc[i] >= 5) & (DV.iloc[i] >= 5_000_000) & mom.notna()
    m = mom[uni].drop(["SPY", "QQQ"], errors="ignore")
    nxt = (Cf.loc[b] / C.loc[a] - 1)
    top = m.nlargest(max(len(m) // 10, 1)).index; top20 = m.nlargest(20).index; bot = m.nsmallest(max(len(m) // 10, 1)).index
    res.append(dict(month=a, top10pct=nxt[top].mean(), top20=nxt[top20].mean(), bottom10pct=nxt[bot].mean(),
                    all=nxt[m.index].mean(), spy=spy_c.loc[b] / spy_c.loc[a] - 1, n=len(m)))
m = pd.DataFrame(res).set_index("month")
print(f"  months {len(m)}, stocks ranked each month ~{int(m.n.median())}")
for c in ("top10pct", "top20", "bottom10pct", "all", "spy"):
    tot = (1 + m[c]).prod() - 1; ann = (1 + tot) ** (12 / len(m)) - 1
    ex = m[c] - m.spy
    print(f"  {c:12s} {ann:+6.1%} a year | monthly extra vs SPY {ex.mean():+.2%} t {tstat(ex):+.2f} | worst month {m[c].min():+.1%} | beat SPY in {(ex>0).mean():.0%} of months")
print("  by year, top 10% minus SPY:", (m.top10pct - m.spy).groupby(m.index.year).mean().map("{:+.2%}".format).to_dict())
