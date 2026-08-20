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
- **No gap filter, no ratio-severity filter** — trade every confirmed signal above
  the price floor.
- **$1.00 minimum entry price** (added 2026-08-19, `MIN_ENTRY_PRICE`). Partitioning
  the 560 pooled out-of-sample trades by entry price showed the edge is monotonic in
  price and **unmeasurable at the bottom**:

  | Entry price | n | win % | mean/trade | t-stat | 95% CI |
  |---|---|---|---|---|---|
  | <$1 | 29 | 69.0 | +5.88% | **0.46** | [−20.6%, +32.3%] |
  | $1–5 | 110 | 63.1 | +8.10% | 3.09 | [+2.8%, +13.4%] |
  | ≥$5 | 421 | 87.2 | +20.52% | 18.66 | [+18.4%, +22.7%] |

  Sub-$1 is **not shown to lose money — it is shown to be unmeasured** (29 trades,
  a 53-point confidence interval). But it is a bet on an unmeasured effect, and those
  names cost ~5.9× their notional in margin versus 0.4× above $1, and 3× the borrow
  (median 7.5%/yr vs 0.0%). 89% of the cumulative backtest return came from names
  above $5.

  **This matters more than it looks:** live signal flow is ~71% sub-$1 while the
  backtest universe was only 7% sub-$1 (median entry $0.39 live vs $10.00 backtested).
  The floor therefore removes most of current flow. That divergence rests on only 44
  live observations and needs more data before it is treated as real.

**Validated result** (walk-forward, out-of-sample, 958 historical events, corrected for
the split-jump bug — see below): 560 trades, 81.4% win rate, t-stat +14.43,
+918% over ~20 months at 100% exposure cap, only −3.5% max drawdown.

**Realistic live target** — regenerated 2026-08-19 with the `$1.00` entry-price floor
applied to the universe, so it models what the code actually trades (2% sizing, real
spread costs, shortability veto, 30%/yr borrow): **+86% over ~20 months**, **87.0% win
rate**, −1.8% max drawdown, from 276 taken trades. **This is the number to judge live
performance against, not the +918%.** See `analysis/live_expectations.md`.

_(Superseded: the pre-floor figure was +70% / 83.6% win. The floor raised both, because
it removes the sub-$1 band where the strategy has no measured edge.)_

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
- **Task Scheduler:** `ReverseSplitDryRun` task exists and fires. Set to weekdays
  9:25am ET; **should be set to repeat every 5 min for 25 min** (see
  `docs/LIVE_DEPLOYMENT.md` §3c) so a late wake still catches the open. Runs "whether
  user is logged on or not" (user set this up manually, entering their own Windows
  password into the Windows prompt — not something Claude ever handled).
  `LastTaskResult=0` no longer means "healthy": exit codes 4 and 5 now signal a
  mistimed or blind run, and the run refuses to trade rather than trading late.
- **Watchdog:** `.github/workflows/trading-watchdog.yml` runs weekdays 15:00 UTC on
  GitHub's infrastructure and fails (→ GitHub emails you) if no healthy in-window
  heartbeat exists for today. It lives off this PC deliberately — every other alert is
  sent *by* the trading run, so a host that never wakes can never report itself.
- **Schwab auth:** token lives at `.schwab_token.json` (gitignored). **Expires every 7
  days, hard limit, no way around it** — refresh with:
  ```bash
  cd C:\Coding\reverse-split-strategy
  .\venv\Scripts\python.exe scripts\run_trading.py --login
  ```
  A bug where this crashed with an uncaught `OAuthError` on an expired token was fixed
  2026-07-29 (`src/split_strategy/broker/schwab_auth.py`) — `get_client()` now validates
  the cached token eagerly instead of only at first real use.
  **The 2-day expiry warning has never fired until now** (fixed 2026-08-19): it derived
  token age from the file's *mtime*, but the file is rewritten every ~30 min on access
  refresh, so the age was pinned near zero. It now reads `creation_timestamp` from
  inside the token. This is why the login silently lapsed twice (Aug 4–10, Aug 17–19),
  during which every run placed nothing and still exited 0.
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
MAX_HTB_RATE=100            # skip if borrow RATE exceeds 100%/yr
MAX_SPREAD_PCT=0.05         # skip if bid-ask spread exceeds 5%
MIN_ENTRY_PRICE=1.00        # no measured edge below this (t=0.46); also 5.9x margin
MAX_TRADE_NOTIONAL=         # absolute $ ceiling per position; unset/0 = no cap
MAX_BORROW_COST_PCT=0.05    # cap on EXPECTED borrow cost (rate x holding days/365)
BORROW_ALERT_RATE=100       # text if an OPEN position's borrow reaches this
BORROW_ALERT_MULTIPLE=3.0   # ...or triples from what it cost at entry
MARGIN_EQUITY_PCT=0.5       # share of equity usable for short maintenance margin
HOUSE_MARGIN_MULTIPLE=1.0   # Schwab charges exactly the FINRA floor ($2.50/share)
ENTRY_WINDOW_BEFORE_MIN=15  # live halts outside 09:15-09:45 ET (exit code 4)
ENTRY_WINDOW_AFTER_MIN=15
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
   **Blocked as of 2026-08-19: the Schwab account is type `CASH`.** Short selling
   requires margin; every entry would be rejected. A margin application was in flight.
   Re-check with a read-only `get_account` call and confirm `type: MARGIN` before any
   live run.
2. ~~**Shortability proxy needs calibration**~~ — **answered 2026-08-19.** Schwab
   reported **all 66** logged signals as shortable while the historical proxy called
   40 of them unshortable. The proxy is wrong in the *pessimistic* direction, so the
   real tradeable universe is wider than `live_expectations` assumed. The proxy should
   be recalibrated upward rather than trusted as a veto.
2b. **Margin is the binding constraint, not capital** (found 2026-08-19). FINRA
   4210(c) floors a short's maintenance requirement at **$2.50/share below $5**, so a
   cheap name eats margin out of all proportion to notional — 7.4× at $0.34, 112× at
   $0.0224. Schwab charges exactly the FINRA floor (confirmed), so
   `HOUSE_MARGIN_MULTIPLE=1.0`. `MAX_EXPOSURE` caps *notional* and never saw this.
   See `src/split_strategy/margin.py`.
3. **Survivorship bias unaddressed** — yfinance drops delisted tickers; a paid data
   source (e.g. Polygon) would fix this but was explicitly out of scope this round.
4. ~~Two other repo-quality items in `docs/VALIDATION_REPORT.md`~~ — **fixed 2026-08-19.**
   Return windows now step over trading bars instead of calendar days, and the
   tz-aware/naive comparison is resolved. Both were display-only (`returns.py` is
   imported solely by `ui/dashboard.py`), so no strategy number changed.
5. `DATA/shortability_ground_truth.csv` shows as locally modified in git right now
   (growing log file) — fine to commit whenever, not urgent.
6. **Wake reliability is still unproven** (opened 2026-08-19). The scheduled task never
   woke the machine once in the 12 days of event log examined — every "late" run was the
   task catching up seconds after a lid-open. DC wake timers were disabled and have been
   enabled, but the machine was likely on AC (where they were *already* enabled) during
   those sleeps, so the fix is unconfirmed. Modern Standby (`S0 Low Power Idle`) makes
   scheduled-task wake unreliable regardless. Watch whether runs land at 09:25; if they
   don't, the answer is an always-on host. The code no longer depends on this being
   right — it halts and pages instead.
7. **Survivorship is now measured, not corrected** (2026-08-19). `scripts/build_prices.py`
   appends to `DATA/survivorship_coverage.csv`: how many events had no price data and
   which tickers. A real fix still needs a point-in-time source (Polygon/CRSP).

## A note on how this project talks about itself

This is a **research and automation system**, not investment advice, and every number
in it is a backtested/simulated estimate, not a guarantee. If continuing work here,
keep that framing — the person you're working with has explicitly wanted honest,
unhedged reporting of what's proven vs. not (see `REPORT.md` §9) throughout, including
several real bugs that materially changed results. Keep that standard up.
