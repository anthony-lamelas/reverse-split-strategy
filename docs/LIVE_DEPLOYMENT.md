# Live Deployment Guide

How to run the strategy for real, what protects you, and what to do when something
looks wrong.

> **Nothing here places a real order until you pass BOTH `--live` and `--i-am-sure`.**
> Every other invocation is dry-run.

---

## 1. The one constraint that shapes everything

Schwab refresh tokens **expire every 7 days** and renewing them requires an interactive
browser login. There is no API path around it. So:

- **Fully unattended trading is impossible on Schwab.** Anyone claiming otherwise is
  wrong about the API.
- **GitHub Actions cannot trade.** A fresh checkout has no token and no position ledger.
- **Trading must run from a machine that stays awake** and holds persistent state.

What you actually sign up for: automated trading, plus a **~2 minute browser login once
a week**. You will never approve individual trades. The bot texts you the day before
the token expires.

---

## 2. What runs where

| Job | Where | Trades? | Why |
|---|---|---|---|
| Scrapers, EDGAR enrichment, LLM scanner | GitHub Actions, 5am ET daily | No | Data collection only; no secrets beyond Mongo/OpenAI |
| `run_signals.py` (report + SMS) | GitHub Actions, same run | **Never** — cannot place an order by construction | Safe anywhere |
| `run_trading.py` | **Your PC**, ~9:25am ET weekdays | Yes, when `--live --i-am-sure` | Needs the token + ledger |
| `pytest` | GitHub Actions, every push | No | Offline, no secrets |

---

## 3. First-time setup

### 3a. Confirm dry-run works
```bash
cd C:\Coding\reverse-split-strategy
.\venv\Scripts\python.exe scripts\run_trading.py --no-alert
```
You should see an equity line, a reconciliation summary, and any entries/exits marked
`WOULD_PLACE`. No orders are sent.

### 3b. Log in to Schwab
```bash
.\venv\Scripts\python.exe scripts\run_trading.py --login
```
Browser opens → approve → token cached to `.schwab_token.json` (gitignored). Repeat
weekly.

### 3c. Schedule it (Windows Task Scheduler)
Create a task that runs **weekdays at 9:25am ET**, ~5 minutes before the open:

- **Program:** `C:\Coding\reverse-split-strategy\venv\Scripts\python.exe`
- **Arguments:** `scripts\run_trading.py` *(add `--live --i-am-sure` only when ready)*
- **Start in:** `C:\Coding\reverse-split-strategy`
- Check **"Run whether user is logged on or not"**
- Check **"Wake the computer to run this task"**

> **The PC must be awake.** A sleeping machine silently skips the day — including exits
> for positions you already hold. See §6 for how to detect a missed run.

Why 9:25am and not 5am: before the open there is no current quote. The old code took
*yesterday's* open as "current price," so the gap-up filter compared the wrong days and
position sizes were computed from a >24h-stale price.

---

## 4. What protects you

| Guard | Behavior |
|---|---|
| **Reconciliation** | Compares the ledger against real Schwab positions. **Any** discrepancy halts the session — no entries, no exits. |
| **Duplicate guard** | One live position per ticker. Only `ENTER_NOW` opens; `HOLDING` never re-enters. |
| **Shortability veto** | Skips anything Schwab reports as not shortable, or whose borrow rate exceeds `MAX_HTB_RATE` (default 100%/yr). |
| **Spread veto** | Skips names wider than `MAX_SPREAD_PCT` (default 5%, validated by backtest sweep). |
| **Marketable limits** | Never a market order. Caps how bad any fill can be. |
| **Exposure ceiling** | Total committed notional ≤ `MAX_EXPOSURE` × equity (default 100%). No position-count cap by design. |
| **Rate limits** | `MAX_NEW_SHORTS_PER_DAY` (5) and `MAX_DAILY_NOTIONAL` ($5,000). |
| **Cancel-before-cover** | On the exit date the resting take-profit is cancelled *first*. If the cancel fails, **no cover is sent** — filling both would flip you long. |
| **Kill switch** | A file named `STOP` in the repo root halts everything, no code or config change. |
| **Uncertain submits** | A submit that times out is recorded `UNCERTAIN`, never written off — next run reconciles it. |

### Emergency stop
```bash
cd C:\Coding\reverse-split-strategy
echo stop > STOP
```
Next run halts immediately. Delete the file to resume. **This does not close existing
positions** — do that in the Schwab UI if you need out now.

---

## 5. Going live

Only after dry-run has behaved sensibly for a few weeks.

1. Set a small `ACCOUNT_SIZE` or use `--account` to cap early risk.
2. Run manually the first time and **watch it start to finish**:
   ```bash
   .\venv\Scripts\python.exe scripts\run_trading.py --live --i-am-sure
   ```
3. Verify in the Schwab UI: the short filled, and a **resting GTC buy-to-cover** appears
   at ~80% of your fill price (the 20% take-profit).
4. Watch one full round trip — entry, then a clean cover on the exit date — before
   adding `--live --i-am-sure` to the scheduled task.

### Position sizing
`TRADE_PCT` defaults to **2%** of live account equity (read from Schwab, so it compounds
with gains and shrinks in a drawdown). The backtest used 5%; 2% was chosen because
stops don't work for this strategy — position size is the only real tail-risk control.
The worst historical trade (-709%) costs ~14% of the account at 2% versus ~35% at 5%.

---

## 6. Monitoring

**Audit log** — every session appends one JSON line:
```bash
type logs\trading_audit.jsonl
```
Contains mode, equity, discrepancies, every entry/exit, and all notes.

**Position ledger** — `DATA/open_positions_live.json`. States: `PENDING_ENTRY` → `OPEN`
→ `PENDING_EXIT` → `CLOSED`. Anything not `CLOSED` still ties up capital and still owes
an exit.

**Detecting a missed run:** if the newest line in `trading_audit.jsonl` isn't from today
and the market was open, the task didn't fire — usually a sleeping PC. Cover anything
overdue manually, or just run the script; overdue exits are retried, not abandoned.

---

## 7. Troubleshooting

| Symptom | Meaning | Fix |
|---|---|---|
| `HALTED: auth failed` | Token older than 7 days | `run_trading.py --login` |
| `HALTED: N ledger/broker discrepancy` | Ledger and Schwab disagree | Compare `DATA/open_positions_live.json` against the Schwab UI. Do not force past it. |
| `HALTED: could not read account equity` | Schwab API issue | Retry; if it persists, trade manually or stay flat |
| `untracked_short` | Schwab shows a short we don't know about | Usually a manual trade or an `UNCERTAIN` submit that did land. Add it to the ledger or close it. |
| `could NOT cancel take-profit` | Cancel failed, cover deliberately skipped | Check whether the TP already filled. If still resting, cancel in the UI and re-run. |
| Everything `SKIPPED: spread ... exceeds` | Illiquid day | Working as intended. Loosen `MAX_SPREAD_PCT` only with evidence. |
| Everything `SKIPPED: not shortable` | No borrow available | Expected for this universe; nothing to fix. |

---

## 8. Configuration

All via `.env` (gitignored):

```bash
TRADE_PCT=0.02              # per-trade notional, fraction of equity
MAX_EXPOSURE=1.0            # total committed notional ceiling
MAX_NEW_SHORTS_PER_DAY=5
MAX_DAILY_NOTIONAL=5000
MAX_HTB_RATE=100            # annualized borrow % ceiling
MAX_SPREAD_PCT=0.05         # skip wider names
ACCOUNT_SIZE=10000          # dry-run only; live reads real equity
```

---

## 9. Expectations

Read `analysis/live_expectations.md` before going live. Under the actual live
constraints the realistic target is roughly **+69% over ~20 months** with an 84.8% win
rate — **not** the +1208% headline, which assumes 5% sizing, zero borrow cost, and no
spread filter.

Even that figure stays optimistic in two ways this project cannot fix without paid data:
it assumes limit orders fill, and it inherits the survivorship bias of yfinance dropping
delisted tickers.
