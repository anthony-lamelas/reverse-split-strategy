"""Research: profit from one share per reverse split when fractions are rounded up.

Uses DATA/events_v2.json (the classifier's `rounding_up` flag) and Polygon prices.
Buy one share at the last pre-split close, sell the whole post-split share.
"""
import json, sys, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT / "src"))
from split_strategy import config
from split_strategy.backtest import sim
from split_strategy.backtest.events import _to_ts
from split_strategy.backtest.prices_v2 import PolygonPrices

df = pd.DataFrame(json.loads((config.DATA_DIR / "events_v2.json").read_text()))
df["f"] = pd.to_datetime(df.filing_date); df["e"] = [_to_ts(x) for x in df.effective_date]
df = df[(df.f >= "2021-10-12") & (df.f < "2025-10-01") & df.e.notna() & ~df.ticker.isin(["UNKNOWN", "NONE"])]
df = df.sort_values("f").drop_duplicates(["ticker", "e"], keep="last")
print("distinct dated splits:", len(df), "| flagged round-up:", int((df.rounding_up == True).sum()))
src, C, out = PolygonPrices(), sim.Costs(), []
for _, r in df.iterrows():
    try:
        b, s = src.daily(r.ticker, "2021-09-01", "2025-11-15"), src.splits(r.ticker)
    except Exception:
        continue
    if b.empty or s is None or s.empty: continue
    when = pd.to_datetime(s.execution_date)
    near = s[(when >= r.e - pd.Timedelta(days=1)) & (when <= r.e + pd.Timedelta(days=15))]
    if near.empty: continue
    T, fac = pd.Timestamp(near.execution_date.iloc[0]), float(near.factor.iloc[0])
    pre, post = b[b.index < T], b[b.index >= T]
    if len(pre) < 1 or len(post) < 6 or fac <= 1: continue
    buy = float(pre.iloc[-1].Close); cost = buy * (1 + C.spread(buy) / 2)
    sell5 = float(post.iloc[5].Close)
    out.append(dict(year=T.year, ratio=fac, round_up=r.rounding_up, cost=cost,
                    profit=sell5 * (1 - C.spread(sell5) / 2) - cost))
d = pd.DataFrame(out); g = d[d.round_up == True]
print("executed and priced:", len(d), "| flagged round-up:", len(g))
print(f"cost of one share: median ${g.cost.median():.2f}")
print(f"profit per split (sold 5 sessions after): median ${g.profit.median():.2f}, mean ${g.profit.mean():.2f}, positive {(g.profit > 0).mean():.0%}")
print("by year:"); print(g.groupby("year").agg(splits=("profit", "size"), total_profit=("profit", "sum")).round(0).to_string())
chk = pd.DataFrame(json.load(open(Path(__file__).parent / "data" / "roundup_check.json")))
t = chk[chk.flag == True]
print(f"\nflag spot-check: {int(t.says_round_up.sum())} of {len(t)} flagged filings contain round-up wording; "
      f"{int(chk[chk.flag == False].says_round_up.sum())} of {int((chk.flag == False).sum())} unflagged ones do too")
