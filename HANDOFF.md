# Handoff — Reverse-Split Shorting Strategy

_Last updated: 2026-09-05. Written to resume work in a fresh session with zero prior
context. Supersedes the 2026-07-29 version, which described a dry-run system on a
Windows laptop; that is no longer what this is._

## Start here

- **Repo:** `C:\Coding\reverse-split-strategy`. **GitHub:**
  `anthony-lamelas/reverse-split-strategy`, private. Branch `main`.
- **The system is LIVE.** It places real orders, from Modal, every weekday at 09:25 ET.
- **It has never opened a position.** Every candidate since going live on 2026-08-24
  has been rejected by a filter. That is the central fact of the current state.
- **Read next:**
  - [`REPORT.md`](REPORT.md) — full system explanation (some sections predate the Modal
    migration; architecture below is authoritative).
  - [`docs/LIVE_DEPLOYMENT.md`](docs/LIVE_DEPLOYMENT.md) — running live, guards.
  - [`analysis/deep_review_fable.md`](analysis/deep_review_fable.md) — **read this
    first of the analyses.** A 2026-09-05 adversarial audit that supersedes several
    numbers below. Summary of what changed is in the box under "⚠ Superseded".
  - [`analysis/price_floor_corrected.md`](analysis/price_floor_corrected.md) — the
    2026-09-04 price-basis finding. Right in direction, incomplete in size; see the
    deep review before quoting any figure from it.
  - [`analysis/postsplit_entry.md`](analysis/postsplit_entry.md) — a rejected hypothesis.
    The rejection was re-verified and strengthened; the *reasoning* recorded there
    (half-year sign flips) does not hold at that sample size.

## What this project is

Shorts micro-cap stocks between the SEC's confirmation of a reverse stock split and the
split's execution date. Signals come from a nightly pipeline (scrapers → SEC EDGAR →
LLM classification) into MongoDB. Backtested and walk-forward validated over ~2 years.

## The strategy ("Strategy B")

- **Entry:** short at the open of the session after the SEC filing confirms the split.
- **Exit:** cover at the open on the split's execution date.
- **20% take-profit** (`run_trading.TAKE_PROFIT_PCT`), resting order after entry fills.
- **40% stop-loss** (`STOP_LOSS_PCT`). Note the older docs say "no stop-loss"; live runs
  with one.
- **No gap-up filter** — the walk-forward chose `inf` in 9 of 11 folds.
- **$1.00 minimum entry price** (`MIN_ENTRY_PRICE`). **Keep it, but the reason recorded
  in the code is wrong — see below.**

## ⚠ Superseded by the 2026-09-05 deep review

The section below is kept for the record. Four of its numbers no longer hold. Full
evidence in [`analysis/deep_review_fable.md`](analysis/deep_review_fable.md);
regenerate with `.\venv\Scripts\python.exe scripts\price_basis_audit.py`.

| Claim below | Now |
|---|---|
| "Prices alone cannot separate adjusted from never-executed" | They cannot, but the provider's split table can, and does: 908 of 913 splits before the panel's last month were absorbed. It is a measurement now, not an inference. |
| Median quoted $0.79, 43% ≥ $1.00 | **$0.70, 38.8%.** Dividing by the event's ratio undoes one split; the panel is adjusted for **every** later split, and 265 of 658 tickers split more than once. |
| Sub-$1 earns "one eleventh as much per dollar of capital" | 6.9× less **per trade**, but it turns capital over 3× faster. On a $4,944 account the sub-$1 book out-earns the above-$1 book. The floor needs re-deriving on borrow and spread evidence, not on the per-trade margin ratio. |
| ≥$1 +17.15%/trade, sub-$1 +11.53%/trade | Both are walk-forward figures whose magnitude the same procedure reproduces on **placebo** universes (12 runs, mean +10.03%, 5 of 12 above the real result). The honest deployed-parameter number is **+4.08%/trade**. |

Also new: the backtest's `t_ann` comes from the earliest EDGAR filing of any type, and
for 45% of checkable events the effective date — which the exit rule depends on — had
not been published yet at that point. Re-entered when it was, the edge disappears.

## ⚠ The most important open finding (2026-09-04)

`DATA/prices_full.pkl` is **back-adjusted**. Of 998 evaluable events, only **45** still
show a price jump at the effective date. Back-adjustment for a 1-for-N reverse split
multiplies pre-split bars by N, so a stock really quoted at $0.11 appears at $0.88.

| | Median entry price | Share ≥ $1.00 |
|---|---:|---:|
| Panel (what every backtest reads) | **$9.80** | 94% |
| Quoted (what the live filter sees) | **$0.79** | 43% |

Companies reverse-split *because* they are under $1 and face delisting. A population
median of $9.80 has no reason to split.

**Three consequences:**

1. **The previous handoff's "signal-flow divergence" was not real.** It recorded live
   flow as 71% sub-$1 with median $0.39, versus a backtest universe 7% sub-$1 with
   median $10.00, and treated it as a genuine shift needing more live data. It is the
   same population measured in two different currencies.
2. **The floor is twice as restrictive as believed** — it keeps 46% of priced events,
   not 93%. That is why the live system has rejected 100% of candidates.
3. **The floor's stated justification is wrong.** `schwab_orders.py` cites "no measured
   edge below $1" (n=29, t=0.46). Re-run on quoted prices with a full walk-forward, the
   sub-$1 bucket has **174 out-of-sample trades, +11.53%/trade, t=7.06**. There is an
   edge there.

**The floor is still correct** — for a reason far stronger than the one written down:

| Bucket (quoted) | Median price | Margin per $1 notional | Mean/trade | **Return per margin $** |
|---|---:|---:|---:|---:|
| ≥ $1.00 | $4.72 | 1.00× | +17.15% | **+17.15%** |
| < $1.00 | $0.34 | 7.39× | +11.53% | **+1.56%** |

FINRA 4210(c) floors a short's collateral at $2.50/share below $5. The rejected bucket
earns about **one eleventh as much per dollar of capital tied up**. On a ~$4,900 account
the same collateral funds one sub-$1 short or eleven ordinary ones.

**Not yet done:** correcting the rationale comment in
`src/split_strategy/broker/schwab_orders.py:167`, and deciding whether any other
analysis that read entry prices off the panel needs re-running.

**The caveat that keeps this honest:** a continuous series means *either* the provider
back-adjusted it *or* the split never executed. Prices alone cannot separate those.
953 of 998 are continuous and the medians support the adjusted reading — but it is an
inference, not a measurement.

## A hypothesis that was tested and rejected (2026-09-04)

"Since a reverse split lifts the price above $1, short these names *after* the split
instead — same trade, no floor problem, better margin."

Half right. The margin/spread/borrowability gains are real. But the return isn't there:

| Entry timing | Bucket | Trades | Mean/trade | t |
|---|---|---:|---:|---:|
| After announcement (current) | ≥ $1 | 398 | +5.22% | +4.47 |
| After announcement (current) | < $1 | 440 | +3.43% | +3.16 |
| **After the split** | < $1 | 465 | **−0.68%** | −0.62 |
| **After the split** | ≥ $1 | 393 | +1.30% | +1.17 |

All hold lengths (5/10/20d) fail; half-year means flip sign (+6.4, −4.7, +0.8, −0.6,
+3.5); and at 50%/yr borrow — routine, and *worse* after a split because the float
shrinks — it is solidly negative. **The drift is consumed between announcement and
effective date.** Reproduce with `python scripts/postsplit_entry_test.py`.

Note also: a split does not reduce exposure. The broker divides the share count by the
same factor it multiplies the price by. `engine.neutralize_split` exists for this.

## System architecture

1. **Data collection** — GitHub Actions, `.github/workflows/nightly-scrape.yml`, daily.
   Scrapers → EDGAR → LLM confirmation (`gpt-4o-mini`) → MongoDB. Never trades.
2. **Offline research** — run by hand. `walk_forward_analysis.py`,
   `price_floor_walkforward.py`, `postsplit_entry_test.py`, `analyze_robustness.py`.
   Exports a fixed parameter set; never adapts automatically.
3. **Live trading — on Modal, not this laptop.** `modal_app.py`, cron
   `25 9 * * 1-5 America/New_York`, `--live --i-am-sure`. State lives on the
   `split-strategy-data` **Volume** (`open_positions_live.json`,
   `/data/logs/trading_audit.jsonl`, `/data/.schwab_token.json`).
   **The local `DATA/` and `logs/` copies are dry-run fossils from August — do not read
   them as live state.**
   Why Modal: the laptop's Modern Standby ignored scheduled-task wake timers, and the
   job silently drifted to 12:15, 17:07, even 23:32 ET for weeks.
4. **Watchdog** — `.github/workflows/trading-watchdog.yml`, weekdays 15:00 UTC, on
   GitHub's infrastructure. Fails (emails you) with no healthy in-window heartbeat.
   It lives off the trading host deliberately.

## Current operational state (2026-09-05)

- **Mode: LIVE.** Equity **$4,944.21**. `MAX_TRADE_NOTIONAL=$50`,
  `MAX_NEW_SHORTS_PER_DAY=1` — deliberately tiny while validating.
- **Zero positions ever opened.** The live ledger is empty. So the ledger, fill-price,
  reconciliation, exit and realized-P&L paths have **never executed against real
  fills**. Treat the first real entry as an event to watch start to finish.
- **Runs are firing on time.** 09:25:10 ET, `halted=False`, heartbeat healthy.
- **Prices come from Schwab, not yfinance** (since 2026-09-03, live from 09-04).
  Sizing and the $1.00 floor use `Quote.entry_price` — the **bid**, because a short
  sells into it, falling back to `last`. A bid only counts when `bid_size > 0`:
  `WHLRL` quoted a $0.0001 bid (size 0) against an $80 stock, which would have sized
  5,000,000 shares.
- **Recent rejections** are almost all the $1.00 floor. On 09-04 `DPU` became the first
  candidate ever to clear it, and was rejected on borrow cost (33%/yr over ~104d =
  9.4% of notional, over the 5% cap). If long-dated splits keep dominating the above-$1
  candidates, **borrow becomes the binding filter, not the price floor.** Watch this.
- **Schwab auth:** 7-day hard expiry. Re-login locally then push the token to the
  Volume (see Commands). The dashboard warns at ≤2 days.
- **Account type must be MARGIN.** Shorting is impossible in a cash account. Live runs
  no longer report account-type rejections, so this appears resolved — confirm with
  `scripts/check_account.py` before trusting it.
- **Tests: 505 passing**, offline, no secrets.

## Modal profile — read this before any `modal` command

`~/.modal.toml` is global and holds **one** active profile. It is set to `verse-prod`
(another project) and should stay there. This repo pins its workspace per invocation
via `config.MODAL_PROFILE` (default `anthony-lamelas23`), so `scripts/dashboard.py`
and `scripts/emergency_stop.py` work with no switching. **Direct `modal` CLI commands
do not** — prefix them:

```powershell
$env:MODAL_PROFILE="anthony-lamelas23"; .\venv\Scripts\python.exe -m modal deploy modal_app.py
```

A bare `modal deploy` fails with "Secret 'split-strategy-secrets' not found in
environment 'main'". That is the profile, not a missing secret.

## Command reference

```powershell
# Ops dashboard — health, open book, today's skips, realized P&L. Prints once, exits.
.\venv\Scripts\python.exe scripts\dashboard.py

# Emergency stop / resume / status  (status exits 2 = UNKNOWN, not "safe")
.\venv\Scripts\python.exe scripts\emergency_stop.py
.\venv\Scripts\python.exe scripts\emergency_stop.py --resume
.\venv\Scripts\python.exe scripts\emergency_stop.py --status

# Weekly Schwab re-login, then push the token to the Volume
.\venv\Scripts\python.exe scripts\run_trading.py --login
$env:MODAL_PROFILE="anthony-lamelas23"; .\venv\Scripts\python.exe -m modal volume put --force split-strategy-data .schwab_token.json /.schwab_token.json

# Local dry run (never places orders)
.\venv\Scripts\python.exe scripts\run_trading.py

# Deploy the live schedule
$env:MODAL_PROFILE="anthony-lamelas23"; .\venv\Scripts\python.exe -m modal deploy modal_app.py

# Research
.\venv\Scripts\python.exe scripts\price_floor_walkforward.py --price-basis quoted
.\venv\Scripts\python.exe scripts\postsplit_entry_test.py
.\venv\Scripts\python.exe -m pytest -q
```

**Exit codes** (`run_trading.py`): 0 clean · 1 halted (auth/equity/reconciliation) ·
2 refused (`--live` without `--i-am-sure`) · 3 STOP kill switch · 4 outside the
09:15–09:45 entry window · 5 no live quotes · **6 no Schwab client, so nothing could be
priced** (added 2026-09-03 — Schwab is now the only price source, and a run that
silently skips everything for lack of prices is indistinguishable from a quiet day).

## Key modules added recently

Paths are relative to `src/split_strategy/` unless they start with `scripts/`.

| Module | Purpose |
|---|---|
| `backtest/price_basis.py` | Panel → quoted price conversion. **Read its docstring.** |
| `backtest/engine.py::split_factor` | Single source of truth for raw-vs-adjusted |
| `backtest/engine.py` `entry_anchor` | `"t_split"` for post-split entry tests |
| `analysis/stats.py` | bootstrap CI, t-stat, borrow adjustment (shared) |
| `fees.py` | Regulatory fees, charged on the **entry** (a short's sell) |
| `modal_cli.py` | Modal subprocess: UTF-8, ASCII-safe output, profile pinning |
| `scripts/dashboard.py` | Terminal ops view (Streamlit app was retired) |

## Known open items

1. **Correct the $1.00 floor's rationale** in `schwab_orders.py:167` — the margin
   argument, not the (wrong) "no measured edge" one.
2. **Audit other analyses that read entry prices off the panel.**
   `analysis/live_expectations.md` and `analysis/price_floor_12mo.csv` are suspect.
3. **Uncommitted research work** sits in the working tree (`price_basis.py`,
   `stats.py`, `postsplit_entry_test.py`, the two new analyses, tests). Branch
   `schwab-pricing-and-ops-dashboard` is already merged, so this needs a fresh branch.
4. **Survivorship bias is unaddressed AND currently unmeasured.**  yfinance drops
   delisted tickers, and this strategy shorts companies at real risk of delisting —
   so the missing names are plausibly the most profitable ones. `build_prices.py:27`
   defines `DATA/survivorship_coverage.csv`, but **that file has never been
   generated**; only the older `DATA/prices_full_missing.txt` exists. Re-running
   `scripts/build_prices.py` would at least quantify the gap. Correcting it needs a
   point-in-time source (Polygon/CRSP).
5. **Shortability proxy is pessimistic** — Schwab reported all 66 logged signals
   shortable while the proxy called 40 unshortable. Recalibrate upward.
6. **`_price_snapshot` is gone but `yfinance` stays in `requirements-runtime.txt`.**
   An import trace shows the live path no longer loads it; that file's own comment
   warns lazy imports don't show in traces. Drop after a few clean live runs.
7. **Reject-tally mixes dry-run and live days** in `dashboard.py` — the only section
   that isn't live-only.
8. **Fee rates are unverified placeholders** (`FEE_PER_SHARE`, `FEE_PER_NOTIONAL`).
   The dashboard labels them `[UNVERIFIED DEFAULTS]` until `FEE_RATES_VERIFIED=1`.
9. **Duplicate signals** — the scanner can emit two documents for one ticker (seen
   with `ALP`, `PHGE`). Harmless: `session.py:249` enforces one position per ticker
   and `_flush_ledger` writes intent before submission. Cosmetic audit noise only.

## A note on how this project talks about itself

This is a **research and automation system, not investment advice**, and every number
in it is a backtested or simulated estimate. The person you are working with has
consistently wanted honest, unhedged reporting of what is proven versus not — several
real bugs have materially changed results, including two documented in this file that
overturned previously confident conclusions. Keep that standard. If a result looks
good, try to break it before reporting it.
