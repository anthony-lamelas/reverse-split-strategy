# Handoff — Reverse-Split Shorting Strategy

_Last updated: 2026-07-29. Written to resume work in a fresh Claude Code session with
zero prior context._

## Start here

- **Repo:** `C:\Coding\reverse-split-strategy` (also cloned at
  `C:\Coding\Other_Projets\Split-Strategy-Devlopment` — same private repo, two local
  copies; keep them in sync via `git pull` if working from the other one).
- **GitHub:** `anthony-lamelas/reverse-split-strategy`, **private**. Not a real GitHub
  fork of anything — a standalone repo created and pushed into. The original shared repo
  (`Arda-Dinc04/Split-Strategy-Devlopment`) has never been touched or pushed to.
- **Branch:** `main`. All work happens directly on `main` now (an earlier
  `automation-and-backtest` branch was merged in and can be ignored/deleted).
- **Full narrative docs** (read these for depth, this file is just the map):
  - [`REPORT.md`](REPORT.md) — the complete system explanation: signal generation,
    trade timing, testing methodology, results, risk analysis.
  - [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — diagram + key-decisions summary.
  - [`docs/LIVE_DEPLOYMENT.md`](docs/LIVE_DEPLOYMENT.md) — how to actually run live
    trading, safety guards, troubleshooting.
  - [`docs/VALIDATION_REPORT.md`](docs/VALIDATION_REPORT.md) — original code audit
    (mostly fixed since; kept as a historical record).

## What this project is

Shorts micro-cap stocks between the SEC's confirmation of a reverse stock split and the
split's execution date. Signal comes from a nightly pipeline (scrapers + SEC EDGAR +
LLM classification) into MongoDB. Backtested and walk-forward validated over ~2 years
of data. A full live-trading system has been built (order construction, exits,
reconciliation, safety limits) but **no real capital has been deployed yet** — currently
running dry-run only on a Windows Task Scheduler job.

## The decided strategy ("Strategy B")

- **Entry:** short at the open of the session after the SEC filing confirms the split.
- **Exit:** cover at the open on the split's execution date.
- **No stop-loss** (tested directly — every stop level made both return and drawdown
  worse; these stocks spike and revert before the exit date, and a stop just locks in
  the spike).
- **20% take-profit** (a resting order placed immediately after entry fills).
- **No gap filter, no ratio-severity filter** — trade every confirmed signal.

**Validated result** (walk-forward, out-of-sample, 958 historical events, corrected for
the split-jump bug — see below): 560 trades, 81.4% win rate, t-stat +14.43,
+918% over ~20 months at 100% exposure cap, only −3.5% max drawdown.

**Realistic live target** (after 2% sizing instead of 5%, real spread costs, the
shortability veto, and a 30%/yr borrow assumption): **+70% over ~20 months**, 83.6% win
rate, −1.7% max drawdown. **This is the number to judge live performance against, not
the +918%.** See `analysis/live_expectations.md`.

## The most important bug that was found and fixed

A reverse split mechanically multiplies the quoted price overnight (a 1-for-10 split
makes the price look 10× bigger) — this does **not** hurt a short (share count adjusts
too), but the original backtest read it as a catastrophic price spike and fired false
stop-losses on every single trade that spanned a split. Fixed via `neutralize_split()`
in `src/split_strategy/backtest/engine.py`. The originally published 574-trade/60.97%
number (`analysis/strategy.md`, the old notebook) is **unreliable** — don't cite it.

## System architecture (see `docs/ARCHITECTURE.md` for the diagram)

1. **Data collection** (GitHub Actions, `.github/workflows/nightly-scrape.yml`, daily) —
   scrapers → SEC EDGAR fetch → LLM confirmation (`gpt-4o-mini`) → MongoDB. Never
   trades, no Schwab credentials needed.
2. **Offline research** (run by hand, not scheduled) — `scripts/walk_forward_analysis.py`,
   `scripts/tail_risk_test.py`, `scripts/live_expectations.py`. Only exports a fixed
   parameter set to the live system; doesn't adapt automatically.
3. **Live trading** (`scripts/run_trading.py`, Windows Task Scheduler on **this PC only**,
   weekdays 6:25am PDT / 9:25am ET — task name `ReverseSplitDryRun`) — reconcile ledger
   vs. real Schwab positions (halts on any mismatch) → process exits due today
   (cancel resting TP, then cover) → evaluate new entries (shortability veto, spread
   veto, capital allocation) → dry-run (log + one SMS per signal) or `--live`.

## Current operational state

- **Mode: dry-run only.** `--live` exists and is fully wired but has never been used
  with real money. Requires both `--live` **and** `--i-am-sure`.
- **Task Scheduler:** `ReverseSplitDryRun` task exists and is confirmed working
  (`LastTaskResult=0`). Runs weekdays 6:25am PDT. Was recently changed to "run whether
  user is logged on or not" (user did this manually, entering their own Windows
  password directly into the Windows prompt — not something Claude ever handled).
- **Schwab auth:** token lives at `.schwab_token.json` (gitignored). **Expires every 7
  days, hard limit, no way around it** — refresh with:
  ```bash
  cd C:\Coding\reverse-split-strategy
  .\venv\Scripts\python.exe scripts\run_trading.py --login
  ```
  A bug where this crashed with an uncaught `OAuthError` on an expired token was fixed
  2026-07-29 (`src/split_strategy/broker/schwab_auth.py`) — `get_client()` now validates
  the cached token eagerly instead of only at first real use.
- **SMS alerts:** working, one text per signal (not bundled), via carrier
  email-to-SMS gateway. `.env` has `SMTP_*` + `ALERT_EMAIL_TO` configured locally (not
  in git). GitHub Actions no longer sends alerts — that was a duplicate path, removed;
  only `run_trading.py` on this PC does now.
- **Tests:** 332 passing, run in CI on every push (`.github/workflows/tests.yml`),
  entirely offline/mocked, no secrets required.
- **EDGAR pagination bug fixed** (2026-07-28) — coverage went from 63%→84% of all
  splits. Backtest numbers were regenerated afterward and got *stronger*, not weaker
  (t-stats rose, drawdowns shrank) — a good sign the edge is real, not an artifact.

## Config reference (`.env`, gitignored — not in git, exists locally only)

```bash
TRADE_PCT=0.02              # 2% of equity per trade (deliberately below the 5% backtested)
MAX_EXPOSURE=1.0            # 100% total exposure ceiling; NO cap on position count
MAX_NEW_SHORTS_PER_DAY=8    # rate limiter on new entries/day, not a position cap
MAX_DAILY_NOTIONAL=5000
MAX_HTB_RATE=100            # skip if borrow cost exceeds 100%/yr
MAX_SPREAD_PCT=0.05         # skip if bid-ask spread exceeds 5%
ACCOUNT_SIZE=10000          # dry-run only; --live reads real Schwab equity
SCHWAB_APP_KEY / SCHWAB_APP_SECRET   (or CLIENT_ID / CLIENT_SECRET, both accepted)
SCHWAB_CALLBACK_URL=https://127.0.0.1:8182
SMTP_HOST/PORT/USER/PASSWORD, ALERT_EMAIL_TO
MONGODB_URI, OPENAI_API_KEY, SEC_USER_AGENT
```
GitHub repo secrets mirror the subset the CI/data-collection workflow needs (`MONGODB_URI`,
`OPENAI_API_KEY`, `SEC_USER_AGENT` — no Schwab/trading secrets in CI, trading never runs there).

## Command reference

```bash
# Daily dry-run manually (normally automatic via Task Scheduler)
.\venv\Scripts\python.exe scripts\run_trading.py

# Weekly Schwab re-login (required, interactive, ~2 min)
.\venv\Scripts\python.exe scripts\run_trading.py --login

# Go live (NOT done yet — do this deliberately, watch the first run start to finish)
.\venv\Scripts\python.exe scripts\run_trading.py --live --i-am-sure

# Re-run research after any data/logic change
.\venv\Scripts\python.exe scripts\walk_forward_analysis.py
.\venv\Scripts\python.exe scripts\tail_risk_test.py
.\venv\Scripts\python.exe scripts\live_expectations.py

# Tests
.\venv\Scripts\python.exe -m pytest tests\ -q
```

## Known open items (not yet done — see `REPORT.md` §11 for the fuller list)

1. **No real capital deployed yet.** Plan: watch dry-run for a while longer, then a
   single small manually-watched live trade before trusting it unattended.
2. **Shortability proxy still needs calibration** — `DATA/shortability_ground_truth.csv`
   is accumulating real Schwab data vs. the historical proxy's guesses; only a handful
   of data points so far.
3. **Survivorship bias unaddressed** — yfinance drops delisted tickers; a paid data
   source (e.g. Polygon) would fix this but was explicitly out of scope this round.
4. Two other repo-quality items flagged in `docs/VALIDATION_REPORT.md` remain (calendar-
   vs-trading-day mislabeling in `returns.py`, a tz-aware/naive comparison bug) — low
   severity, not blocking.
5. `DATA/shortability_ground_truth.csv` shows as locally modified in git right now
   (growing log file) — fine to commit whenever, not urgent.

## A note on how this project talks about itself

This is a **research and automation system**, not investment advice, and every number
in it is a backtested/simulated estimate, not a guarantee. If continuing work here,
keep that framing — the person you're working with has explicitly wanted honest,
unhedged reporting of what's proven vs. not (see `REPORT.md` §9) throughout, including
several real bugs that materially changed results. Keep that standard up.
