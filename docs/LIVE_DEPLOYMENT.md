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
- Set the trigger to **repeat every 5 minutes for 25 minutes** (so 09:25–09:50)

Repeating matters: if the host wakes at 09:31 a single 09:25 trigger has already been
missed, but a repeating one still trades near the open. Repeats are safe — one live
position per ticker, and the ledger is flushed to disk *before* any order can reach the
broker, so a crash mid-submit cannot produce a second short.

```powershell
# Re-register the repetition on an existing task
$t = Get-ScheduledTask -TaskName "ReverseSplitDryRun"
$t.Triggers[0].Repetition.Interval = "PT5M"
$t.Triggers[0].Repetition.Duration = "PT25M"
Set-ScheduledTask -TaskName "ReverseSplitDryRun" -Trigger $t.Triggers[0]
```

> **The PC must be awake — and "Wake the computer" is not enough on its own.**
> Wake timers are ignored on battery unless enabled, and are unreliable in Modern
> Standby (`S0 Low Power Idle`) regardless of the setting. Verify with:
>
> ```powershell
> powercfg /query SCHEME_CURRENT SUB_SLEEP RTCWAKE   # 0x1 = enabled, per AC/DC
> powercfg /waketimers                               # needs an ELEVATED prompt
> ```
>
> Do not rely on this alone. Two independent guards exist because it failed for weeks
> undetected: the run refuses to trade outside the entry window (§4), and an
> off-machine watchdog reports a missed session (§6).

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
| **Rate limits** | `MAX_NEW_SHORTS_PER_DAY` (8) and `MAX_DAILY_NOTIONAL` ($5,000). |
| **Cancel-before-cover** | On the exit date the resting take-profit is cancelled *first*. If the cancel fails, **no cover is sent** — filling both would flip you long. |
| **Kill switch** | A file named `STOP` in the repo root halts everything, no code or config change. |
| **Uncertain submits** | A submit that times out is recorded `UNCERTAIN`, never written off — next run reconciles it. |
| **Entry window** | In `--live`, a run firing outside 09:15–09:45 ET halts entirely (exit 4). Strategy B enters *at the open*; a run at 12:15 is not that trade. Dry-run continues but tags the report `OUT_OF_WINDOW`. |
| **No-quote halt** | If there is work to do and no market data (usually an expired login), the run halts with exit 5 instead of skipping everything and reporting success. |
| **Write-ahead ledger** | The intent to enter or cover is flushed to disk *before* the order can reach the broker, so a crash mid-submit cannot leave a real position unrecorded. |
| **Ambiguous-cover halt** | A cover written ahead but never assigned an order id halts the next run — covering twice would flip you long. |
| **Stranded-position alert** | Any halt that leaves a position past its cover date texts you the tickers to cover manually. |

**Exit codes** (Task Scheduler records these as `LastTaskResult`):

| Code | Meaning |
|---|---|
| 0 | Clean run |
| 1 | Auth, equity, or reconciliation failure |
| 2 | `--live` without `--i-am-sure` |
| 3 | `STOP` kill-switch present |
| 4 | Ran outside the entry window (live only) |
| 5 | No live quotes — nothing could be entered or covered |

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

**Detecting a missed run — automatically.** Every session writes a heartbeat to MongoDB
(`trading_heartbeats`), including halted ones. The **Trading Watchdog** GitHub Action
runs weekdays at 15:00 UTC and *fails the workflow* if today has no healthy in-window
heartbeat; GitHub emails you on workflow failure, so this needs no SMTP secrets.

It lives off this machine on purpose. Every other alert is sent *by* the trading run,
so the one thing the run can never report is that it never ran — a laptop asleep at
09:25 executes no code and sends no SMS. That is exactly how the job drifted to 12:15,
17:07 and even 23:32 ET for weeks in August 2026 without anyone being told.

```bash
# Check any date by hand
python scripts/check_heartbeat.py --date 2026-08-20
```
Exit 0 = healthy (or not a trading day), 1 = missed/late/halted, 2 = couldn't reach Mongo.

**By hand:** if the newest line in `trading_audit.jsonl` isn't from today and the market
was open, the task didn't fire — usually a sleeping PC. Cover anything overdue manually,
or just run the script; overdue exits are retried, not abandoned.

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
MAX_NEW_SHORTS_PER_DAY=8
MAX_DAILY_NOTIONAL=5000
MAX_HTB_RATE=100            # annualized borrow % ceiling
MAX_SPREAD_PCT=0.05         # skip wider names
ACCOUNT_SIZE=10000          # dry-run only; live reads real equity
ENTRY_WINDOW_BEFORE_MIN=15  # minutes before the 9:30 open the run may still trade
ENTRY_WINDOW_AFTER_MIN=15   # minutes after; outside this, --live halts (exit 4)
```

Widen the entry window only deliberately. It is the guard that stops a mistimed run
from filling at a price the backtest never measured — loosening it to "whenever the
laptop happened to wake" is how the problem it exists for looked normal for weeks.

---

## 9. Expectations

Read `analysis/live_expectations.md` before going live. Under the actual live
constraints the realistic target is roughly **+69% over ~20 months** with an 84.8% win
rate — **not** the +1208% headline, which assumes 5% sizing, zero borrow cost, and no
spread filter.

Even that figure stays optimistic in two ways this project cannot fix without paid data:
it assumes limit orders fill, and it inherits the survivorship bias of yfinance dropping
delisted tickers.
