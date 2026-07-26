# Reverse-Split Shorting Strategy — Full System Report

_Last updated: 2026-07-26_

This document explains the entire system end to end: what the trade is, how signals are
discovered, how trades are timed, how everything was tested, what the testing actually
proved (and did not prove), the strategies that came out of it, and how the live
automation works.

> **Not investment advice.** This is a technical and statistical write-up of a research
> system. Nothing here is a recommendation to trade or a promise of returns. Every
> forward-looking number in this document is an estimate derived from a limited
> historical sample.

---

## Table of contents

1. [Executive summary](#1-executive-summary)
2. [The thesis — why this trade should work](#2-the-thesis--why-this-trade-should-work)
3. [Signal generation — how we find trades](#3-signal-generation--how-we-find-trades)
4. [Trade timing — the actual rules](#4-trade-timing--the-actual-rules)
5. [Testing methodology](#5-testing-methodology)
6. [Results — the two validated strategies](#6-results--the-two-validated-strategies)
7. [Risk analysis](#7-risk-analysis)
8. [Live automation architecture](#8-live-automation-architecture)
9. [What is proven vs. what is not](#9-what-is-proven-vs-what-is-not)
10. [Command reference](#10-command-reference)
11. [Open questions and next steps](#11-open-questions-and-next-steps)

---

## 1. Executive summary

**The trade:** short micro-cap stocks that have announced a reverse stock split, between
the SEC announcement and the split's execution date.

**Where the signal comes from:** a nightly pipeline scrapes upcoming split events,
cross-references SEC EDGAR filings, and uses an LLM to confirm which filings are
*definitive, future* reverse splits (not proposals or already-executed splits).

**What testing showed:** after fixing a critical bug that had corrupted the original
research, proper walk-forward validation across 745 events and 20 months of
out-of-sample windows found a **statistically strong edge** (t-stats of +9.9 and +13.2,
bootstrap confidence intervals comfortably excluding zero). Two distinct strategies
survived validation.

**The headline caveat:** the edge is statistically real *in this dataset*, but several
real-world costs and biases are either unmodeled or only partially modeled —
survivorship bias, borrow availability, and slippage on illiquid names. At 200%
annualized borrow cost, one of the two strategies goes to roughly breakeven. Live
trading has not been done; the system currently runs in dry-run only.

**Most surprising finding:** every single walk-forward fold, under both selection
methods, chose **no stop-loss**. A follow-up test confirmed that adding stops back in
*reduced* returns at every level tested and did not reliably reduce drawdown.

---

## 2. The thesis — why this trade should work

A reverse split (e.g. 1-for-10: ten old shares become one new share worth ~10× as much)
is a mechanical, value-neutral operation. Companies do them for a specific reason:
**they are about to be delisted for trading below $1**, and a reverse split is the
fastest way to get the quoted price back above the exchange minimum.

That makes the announcement a strong distress signal. Companies in this position tend to
share several traits:

- Sustained price decline (that's why they're under $1)
- Ongoing dilution — many are issuing shares to fund operations
- Weak or negative cash flow
- A retail-heavy shareholder base prone to momentum swings

Empirically, the original research measured the **median** price path across ~600
announcement-aligned events (median, not mean — a handful of 500% short squeezes would
make the average misleading) and found consistent negative drift between announcement
and execution.

**The key structural insight for shorts:** a reverse split does *not* hurt a short
position. If you short 100 shares at $0.72 ($72 notional) and a 1-for-10 split happens,
you now owe 10 shares at ~$7.20 — still $72. The broker adjusts your share count by the
same factor. This matters enormously, and misunderstanding it caused the single worst
bug in this project (see [§5.2](#52-the-critical-bug-that-invalidated-the-original-research)).

---

## 3. Signal generation — how we find trades

The pipeline runs in four stages, nightly.

### 3.1 Stage 1 — Split discovery (scrapers)

Three sources are scraped for announced reverse splits:

| Source | Module |
|---|---|
| StockAnalysis | `src/split_strategy/scrapers/stockanalysis.py` |
| TipRanks | `src/split_strategy/scrapers/tipranks.py` |
| HedgeFollow | `src/split_strategy/scrapers/hedgefollow.py` |

Results are upserted into the MongoDB collection **`reverse_splits`**, keyed on
`{Symbol, Date}`. Fields: `Symbol`, `Date` (execution date), `Company Name`,
`Split Ratio`.

Entry point: `python -m split_strategy.scrapers.runner`

**Why not stop here?** These sources tell you a split is *happening* but not reliably
*when it was first announced*. The announcement date is the entire basis of the trade —
it's when the information becomes public and the price starts drifting. For that we need
SEC filings.

### 3.2 Stage 2 — EDGAR confirmation and announcement dating

Two separate jobs consume SEC EDGAR:

**a) Backfill enrichment** — `scripts/nightly_job.py`
For each split in `reverse_splits` without EDGAR data, query the SEC submissions API for
that company's filings in a window around the split date, score each filing, and store
results in **`reverse_splits_edgar`**.

Filings are scored (`src/split_strategy/edgar/scoring.py`) on:
- Form type (8-K/6-K = +3, DEF 14A/proxy = +2, S-1/424B = +1)
- Valid extracted ratio (+2)
- Extracted effective date (+1)
- Compliance/listing cue, e.g. Item 3.01 (+1)
- Share-change cue, Item 3.02 (+1)
- Agreement with the scraped ratio/date (+1 each)

Score ≥5 = **Tier A**, ≥3 = **Tier B**, else Tier C. Only **Tier A/B** filings are
treated as high-confidence announcements. The announcement date `t_ann` is the
*earliest* Tier A/B filing for that split.

**b) Forward-looking daily scan** — `scripts/scan_early_edgar.py`
This is the one that actually drives live trading. It scans **every 8-K, 6-K, 8-K/A and
6-K/A filed that day**, and for each:

1. **Keyword pre-filter** — cheap regex pass for reverse-split language
   (`reverse stock split`, `share consolidation`, `1-for-N`, `exchange ratio`, etc.)
2. **LLM classification** (`gpt-4o-mini`) — for filings that pass the keyword filter,
   extract structured judgment.

The LLM is asked to distinguish three things that matter enormously and are easy to
confuse:

- **Is this a reverse split at all?**
- **Is it in the future?** (A filing announcing a split that *already became effective*
  is useless — the trade window has closed.)
- **Is it definitive, or just a proposal?** A proxy statement seeking shareholder
  authority to *maybe* do a split later is not tradeable. A board announcing an executed
  decision with a date and ratio is.

It returns: `is_reverse_split`, `is_future_split`,
`is_definitive_split_announcement`, `effective_date`, `ratio`, `rounding_up`, and a
`confidence` of High / Medium / Low. Confirmed hits are upserted into
**`early_edgar_splits`** keyed on `filing_url`.

### 3.3 Stage 3 — The three collections

| Collection | Contents | Used for |
|---|---|---|
| `reverse_splits` | Raw scraped split events | Historical universe |
| `reverse_splits_edgar` | Scored EDGAR filings joined to splits | Historical announcement dates (Tier A/B) |
| `early_edgar_splits` | Forward-looking confirmed splits from the daily scan | **Live trading signals** |

The live signal generator reads `early_edgar_splits`. The historical backtest uses a
union of both sources (745 events; see [§5.3](#53-the-event-dataset)).

### 3.4 Stage 4 — Signal generation

`src/split_strategy/signals/generate.py` turns the raw collection into ranked, sized,
actionable signals. For each candidate it:

1. Filters to minimum confidence (default **High**)
2. Requires `filing_date < effective_date` (a real forward-looking window)
3. Requires the trade window to still be open (`effective_date` in the future)
4. Requires the filing to be recent enough to still act on (default 7-day lookback)
5. Computes `entry_date` = next business day after the filing
6. Assigns a status:
   - `UPCOMING` — entry date hasn't arrived yet
   - `ENTER_NOW` — today is the entry date
   - `HOLDING` — past ideal entry, window still open
7. Fetches a live price to compute the gap-up check and position size
8. Checks shortability (proxy classifier, plus real Schwab data if a token is available)
9. Allocates capital against the exposure cap

Ranking: actionable-now first, then confidence, then soonest execution date.

---

## 4. Trade timing — the actual rules

### 4.1 Entry

**Enter short at the open of the first trading session after the SEC announcement.**

This detail matters more than it looks. The original research entered at the open of the
*same day* as the filing — but 8-Ks and 6-Ks are filed intraday or after the close, so
that price was not actually tradeable on the signal. That's look-ahead bias. The
corrected engine uses `entry_offset=1` (next session's open) for all validated results
in this report.

### 4.2 Exit

Depends on the strategy (see [§6](#6-results--the-two-validated-strategies)), but the
two validated variants are:

- **`day_of_split`** — cover at the open on the split's execution date
- **`day_before_split`** — cover at the open on the last session before execution

Both deliberately avoid holding *through and past* the split, which is when the
post-split micro-float squeeze risk is highest.

### 4.3 Filters

| Filter | Rule | Purpose |
|---|---|---|
| Gap-up | Skip if the entry open is >30% above prior close | Avoid names already spiking on retail momentum |
| Ratio bucket | `<10`, `10–50`, `>50`, or all | Tested; "all ratios" won consistently |
| Confidence | High only (live default) | Avoid proposals/ambiguous filings |

Note: walk-forward selected **no gap-up filter** in most folds — it was neutral-to-mildly
negative on the corrected data. The live signal generator still applies a 30% gap check
as a conservative default.

### 4.4 Position sizing

- **Notional per trade:** 5% of account equity (`TRADE_PCT`, config default)
- **Max total exposure:** 100% of equity across all concurrently open positions
  (`MAX_EXPOSURE`) — this is the cash-collateralized ceiling, realistic for
  non-marginable microcaps
- Signals are allocated capital in ranked order; once the cap is hit, remaining signals
  are flagged and skipped rather than silently over-allocated

The exposure cap is not cosmetic. The backtest found **up to 86 positions open
simultaneously** (median 55) for one strategy. Without a cap, position sizing implicitly
assumes unlimited buying power.

---

## 5. Testing methodology

### 5.1 The backtest engine

`src/split_strategy/backtest/engine.py` — `backtest_mega()`. For each event:

1. Load the ticker's OHLCV series, **unadjusted** (`auto_adjust=False`)
2. Neutralize the mechanical reverse-split price jump (see §5.2)
3. Find the entry bar (`entry_offset` sessions after `t_ann`), enter at its Open
4. Apply the gap-up filter against the prior close
5. Build the holding window per the hold rule
6. Scan bars for stop-loss (intrabar High) or take-profit (intrabar Low); otherwise exit
   at the Open of the final holding bar
7. Compute `net_return = (entry - exit)/entry - 1.5%` (flat slippage/fees)
8. Apply P&L to the running portfolio

### 5.2 The critical bug that invalidated the original research

**This is the single most important finding in the project.**

A reverse split multiplies the quoted price by the split factor overnight. As established
in §2, this does *not* harm a short position — the broker adjusts share count identically.

But the original backtest compared raw quoted prices across the split boundary. A 1-for-10
split looked like a **+900% adverse move**, which instantly triggered the 40% stop-loss on
a trade that was in reality completely fine.

For the `day_of_split` strategy, the holding period spans the split date on **every
single trade**. So this bug touched everything.

**Why neither yfinance mode saves you:** verified empirically on BANL (1-for-13, eff.
2026-07-20) and APUS (1-for-10, eff. 2026-07-24) — the ~10× jump is present with
`auto_adjust=False` **and** `auto_adjust=True`. Yahoo simply has not recorded these
micro-cap reverse splits in its adjustment table. You must neutralize the split yourself.

**The fix:** `neutralize_split()` in `engine.py` divides prices on/after the effective
date by the declared EDGAR ratio, guarded so already-adjusted series aren't
double-adjusted.

**Measured impact** on a 49-trade window:

| | Win rate | Total return | Stop-outs |
|---|---:|---:|---:|
| Uncorrected (original behavior) | 51.0% | −7.54% | 7 |
| Split-neutralized (correct) | **55.1%** | **+2.38%** | 5 |

Two trades flipped from phantom −41.5% stop-outs to real winners (APUS +7.4%,
BANL +16.9%).

> ⚠️ **The originally published result (574 trades / 60.97% win rate in
> `analysis/strategy.md`) was computed with this bug present** and should be treated as
> unreliable. The notebook was not modified (the validation pass was report-only).

### 5.3 The event dataset

Walk-forward testing uses a **union** of both event sources, deduplicated on
(ticker, execution date):

- Tier A/B historical join: longest history (Jun 2024 →), but the nightly EDGAR job lags,
  so recent months are thin
- `early_edgar_splits` scanner: stays current, fills the recent end

**Result: 745 events, 614 unique tickers, 2024-06-07 → 2026-07-20.**
Prices fetched for 598 of 614 tickers (16 unavailable — see survivorship caveat in §9).

### 5.4 Grid search

The parameter space is the original 4,620 permutations:

| Dimension | Options | Count |
|---|---|---:|
| Hold rule | 1, 2, 5, 10, 14 days; day_before/of/after_split; 5/10/14_days_after_split | 11 |
| Stop-loss | 15%, 20%, 25%, 30%, 35%, 40%, none | 7 |
| Take-profit | 20%, 40%, 60%, 80%, none | 5 |
| Gap-up filter | 10%, 30%, none | 3 |
| Ratio bucket | all, <10, 10–50, >50 | 4 |

**Total: 11 × 7 × 5 × 3 × 4 = 4,620**

A naive implementation (calling `backtest_mega` 4,620 times per fold × 10 folds × 2
selection methods) would take hours. `src/split_strategy/backtest/gridsearch.py`
precomputes the expensive per-event work once (split neutralization, entry lookup,
holding-window slicing per hold rule) and vectorizes the inner scan.

**Result: full 4,620-permutation grid in ~12 seconds.** Verified numerically identical to
the reference engine across 5 spot-checked permutations spanning different hold rules and
filters (`scripts/verify_gridsearch.py`).

### 5.5 Why the grid search alone proves nothing

If you run 4,620 experiments and report the best one, you have not found an edge — you
have found the luckiest corner of your own dataset.

Quantified: with 574 trades, the standard error of the win rate is 2.09%. Across 4,620
trials, the *expected best* win rate from pure chance alone (assuming zero real edge) is
**~58.6%**. The originally published figure was **60.97%** — not comfortably above the
noise threshold. (The permutations are highly correlated, so the true threshold is
somewhat lower than this independent-tests bound — but the point stands.)

**This is why walk-forward validation was necessary.**

### 5.6 Walk-forward validation

The method (`src/split_strategy/backtest/walkforward.py`):

1. Split history into rolling 60-day test windows
2. For each window, run the **full 4,620-permutation grid** using **only events
   announced strictly before that window** (in-sample)
3. Freeze the winning parameters
4. Evaluate them on the window itself — data the optimizer has never seen
   (out-of-sample)
5. Discard the in-sample numbers entirely; pool only the out-of-sample results

**10 folds**, covering 2024-12-27 → 2026-08-19.

Two selection philosophies were compared, because "best" is ambiguous:

- **Return-maximizing** (`total_return_pct`) — picks the highest in-sample return. Prone
  to latching onto a few lucky trades.
- **Stability-favoring** (`t_stat`) — picks the highest t-statistic, favoring an edge
  that shows up *consistently* and penalizing noisy small samples.

A variance floor was added to the t-stat calculation: a handful of trades exiting at a
fixed take-profit can have near-zero variance, producing an absurd t-stat that reflects
sample-size artifact rather than robustness.

### 5.7 The compounding fix

The first walk-forward implementation applied each trade's P&L in entry-date order, as if
every trade resolved instantly before the next was sized. With a median of 13–55
concurrent open positions, that's badly wrong — it let gains compound before they had
actually been realized, and assumed unlimited buying power.

`compounded_oos_curve()` now processes OPEN and CLOSE as separate chronological events
(closes first on a given date, to free capital), and only opens a position if it fits
under the exposure cap. Trades that don't fit are **skipped** — a real missed signal, not
a free lunch.

### 5.8 Robustness checks

`scripts/analyze_robustness.py` and `scripts/tail_risk_test.py` add:

- Bootstrap confidence intervals on per-trade expectancy (20,000 resamples)
- Borrow-cost sensitivity (0% → 200% annualized)
- Gap-through fill realism (stop fills at the *open* after trigger, not at the stop price)
- Multiple-testing analysis
- Forced-stop-loss sensitivity across 6 levels

---

## 6. Results — the two validated strategies

### 6.1 Strategy A — Return-maximizing

**Parameters:** `day_before_split` · no stop-loss · no take-profit · no gap filter · all ratios

| Metric | Value |
|---|---:|
| Pooled OOS trades | 469 |
| Win rate | 74.4% |
| Mean return/trade | +25.88% |
| t-statistic | +9.88 |
| Bootstrap 95% CI | [+20.51%, +30.66%] |
| P(edge > 0) | 100% |
| Equity @100% exposure cap | $10,000 → $58,307 (+483%) |
| Max drawdown | −34.8% |
| Median holding period | 27 days |
| Trades skipped for capital | 239 of 469 (51%) |
| Max concurrent positions | 86 (median 55) |

Parameter stability: `day_before_split` ×8, `14_days_after_split` ×2; no stop ×10.

### 6.2 Strategy B — Stability-favoring ⭐ *stronger candidate*

**Parameters:** `day_of_split` · no stop-loss · **20% take-profit** · no gap filter · all ratios

| Metric | Value |
|---|---:|
| Pooled OOS trades | 551 |
| Win rate | **80.4%** |
| Mean return/trade | +11.01% |
| t-statistic | **+13.18** |
| Bootstrap 95% CI | [+9.32%, +12.56%] |
| P(edge > 0) | 100% |
| Equity @100% exposure cap | $10,000 → **$130,770 (+1,208%)** |
| Max drawdown | **−12.0%** |
| Median holding period | 5 days |
| Trades skipped for capital | 45 of 551 (8%) |
| Max concurrent positions | 39 (median 13) |

Parameter stability: `day_of_split` ×9, `10_days_after_split` ×1; no stop ×10;
20% take-profit ×8, 60% ×2.

### 6.3 Head-to-head

| | Strategy A (return-max) | Strategy B (stability) |
|---|---:|---:|
| Win rate | 74.4% | **80.4%** |
| t-statistic | +9.88 | **+13.18** |
| Equity @100% cap | $58,307 | **$130,770** |
| **Max drawdown** | −34.8% | **−12.0%** |
| Holding period | 27 days | **5 days** |
| Capital efficiency | 49% of trades taken | **92% of trades taken** |
| Survives 200% borrow | −2.6% (breakeven) | **+104.9%** |

**Strategy B is better on essentially every risk-adjusted dimension.** It earns more
under a realistic capital cap despite a lower per-trade return, because its 5-day holding
period recycles capital ~5× faster — it simply gets to take far more of its signals. Its
shorter holding period also means less borrow-cost exposure and less overnight risk.

### 6.4 Baseline comparison

Trading the *original published* strategy (day_of_split / 40% stop / no TP / 30% gap
filter) frozen with no re-optimization, over the same period:

> 537 trades, 58.66% win rate, +1,123% total return, −16.6% max drawdown

Notably this is *also* strong once run on split-corrected data — the 40% stop was largely
a response to the phantom stop-outs the bug created. Strategy B still beats it on win
rate, drawdown, and t-stat.

---

## 7. Risk analysis

### 7.1 The no-stop-loss finding and single-trade tail risk

Every fold, both selection methods, chose **no stop-loss**. Given how counterintuitive
that is for a short strategy with theoretically unlimited downside, it was tested
directly (`scripts/tail_risk_test.py`) by forcing stops back in while holding all other
selected parameters fixed:

| Stop cap | Strategy A equity | Strategy B equity |
|---|---:|---:|
| **None (baseline)** | **$58,307** | **$130,770** |
| 300% | $49,932 | $59,755 |
| 200% | $56,367 | $74,765 |
| 150% | $42,121 | $43,053 |
| 100% | $28,714 | $37,183 |
| 50% | $24,117 | $36,842 |

**Every stop level reduced returns, and max drawdown did not reliably improve** (it moved
non-monotonically). The mechanism: these are thin, illiquid microcaps prone to temporary
spikes that revert before the exit date. A stop locks in the loss on the spike instead of
riding out the reversion — converting would-be winners into realized losses.

**But the tail risk is real.** The worst single trade in the pooled set was **SBET
(May 2025) at −709%**, roughly a −35% portfolio hit at 5% sizing. It survived, but a
2-year sample may simply not contain a true black-swan squeeze.

**Conclusion: position sizing, not a price stop, is the correct tail-risk lever.**

| Position size | Impact of a −709% trade | Impact of a hypothetical −2000% squeeze |
|---|---:|---:|
| 5% (current default) | −35.5% | −100% (account floored) |
| 3% | −21.3% | −60% |
| 2% | −14.2% | −40% |
| 1% | −7.1% | −20% |

### 7.2 Shortability — can you even place these trades?

Roughly **55% of pooled out-of-sample trades were flagged as likely unshortable** by the
historical proxy classifier (which uses exchange listing, sub-$1 price, liquidity,
warrant/unit detection, and post-split delisting).

Encouragingly, the shortable-only subsets still perform well:
- Strategy A shortable-only: 81.9% win rate, $10,000 → $58,573
- Strategy B shortable-only: 86.2% win rate, $10,000 → $44,520

**However — the proxy has already been contradicted by real data.** The first live Schwab
shortability check (2026-07-26) returned:

| Ticker | Price | Exchange | Proxy said | **Schwab said** | Borrow rate |
|---|---:|---|---|---|---:|
| WHLRL | — | Nasdaq | shortable | shortable | ~6.25% |
| SGLY | $0.26 | Nasdaq | **NOT shortable** | **shortable** | ~5.25% |
| PMI | $0.09 | NYSE | **NOT shortable** | **shortable** | ~16.25% |
| MVIS | $0.28 | Nasdaq | **NOT shortable** | **shortable** | ~9.00% |
| NEXRW | — | Nasdaq | shortable | shortable | ~10.00% |

All three sub-$1 names the proxy rejected were **actually shortable at Schwab**, at
modest borrow rates (5–16% annualized, well below the 50–200% worst case). This suggests
the proxy's "sub-$1 = unshortable" rule is too strict and the true tradeable fraction may
be considerably higher than 45%.

This is exactly why real ground-truth logging was built — see [§8.4](#84-shortability-ground-truth-logging).

### 7.3 Borrow costs

Hard-to-borrow micro-caps can cost 50–200%+ annualized. Sensitivity, at the 100%
exposure cap:

| Annual borrow rate | Strategy A equity | Strategy B equity |
|---|---:|---:|
| 0% | $58,307 | $130,770 |
| 10% | $55,321 | $119,798 |
| 30% | $69,333 | $103,328 |
| 50% | $47,467 | $88,757 |
| 100% | $28,254 | $56,987 |
| **200%** | **$9,736 (−2.6%)** | **$20,486 (+104.9%)** |

Strategy A goes to breakeven at 200% borrow; **Strategy B remains solidly positive** —
a direct consequence of its 5-day vs. 27-day holding period.

> *Note on non-monotonicity:* Strategy A's equity is not strictly decreasing in borrow
> rate (30% > 10%). This is a real artifact of the capital cap: changing returns changes
> portfolio value → changes bet sizes → changes which trades fit under the cap → changes
> which trades get taken. With half of A's signals being skipped for capital, small
> changes reshuffle the taken set substantially.

### 7.4 Capital constraints and concurrency

Trades overlap heavily. Strategy A peaked at **86 simultaneous open positions** (median
55); Strategy B at 39 (median 13). At 5% notional each, 86 concurrent positions would
require 430% of account equity — impossible in a cash-collateralized account.

This is why the exposure cap dramatically changes the equity curve, and why Strategy A
loses 51% of its signals to capital constraints while Strategy B loses only 8%.

---

## 8. Live automation architecture

### 8.1 Daily flow

```
5:00 AM EST — GitHub Actions (.github/workflows/nightly-scrape.yml)
   │
   ├─ 1. Scrapers          → reverse_splits          (python -m split_strategy.scrapers.runner)
   ├─ 2. EDGAR backfill    → reverse_splits_edgar    (scripts/nightly_job.py)
   ├─ 3. Daily EDGAR scan  → early_edgar_splits      (scripts/scan_early_edgar.py)
   └─ 4. Signal check      → console + SMS + JSON log (scripts/run_signals.py, DRY-RUN)
```

### 8.2 The signal run

`scripts/run_signals.py` performs, in order:

1. Load the open-positions ledger; free capital from positions past their planned exit
2. Generate ranked signals (§3.4)
3. Enrich with real Schwab shortability data if a token is cached
4. Construct `SELL_SHORT` orders for actionable signals
5. **Dry-run: log "would place…" and never send.** Live: submit via Schwab API
6. Persist newly-taken positions to the ledger
7. Write a JSON log; send SMS if there are `ENTER_NOW` signals

### 8.3 Dry-run vs. live

**Dry-run is the default.** It builds the real order object, runs every filter a live
order would face, and logs the intent — without calling Schwab's order endpoint.

**Why this matters:** Schwab's Trader API has **no paper-trading environment** — it only
connects to live accounts. Dry-run is the substitute.

Going live is `--live`. **There is currently no confirmation prompt** — see §11.

### 8.4 Shortability ground-truth logging

Schwab's quote endpoint exposes `isShortable`, `isHardToBorrow` and `htbRate` (the actual
live borrow fee). `src/split_strategy/broker/schwab_market_data.py` queries these, and
every run appends to `DATA/shortability_ground_truth.csv` — building a real dataset to
calibrate (or replace) the historical proxy classifier over time. This works in dry-run,
so it accumulates risk-free.

### 8.5 Schwab authentication

- OAuth via `schwab-py`; token cached to `.schwab_token.json` (gitignored)
- **Refresh tokens hard-expire every 7 days** with no extension possible
- Therefore: `python scripts/run_signals.py --login` must be re-run ~weekly
- **Fully unattended live trading is impossible on Schwab** — the CI job is dry-run only,
  because GitHub Actions has no browser for the interactive re-login

### 8.6 SMS alerts

Uses the carrier email-to-SMS gateway (Verizon: `<number>@vtext.com`) via SMTP. Fires
only when there is at least one `ENTER_NOW` signal. Configured through `SMTP_HOST`,
`SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL_TO` — locally in `.env`, and as
GitHub repository secrets for the scheduled run. Verified working 2026-07-26.

### 8.7 Known limitation of the CI run

The capital ledger **does not persist across CI runs** (each GitHub Actions run starts
from a clean checkout). The scheduled job therefore always assumes $0 committed. Its
alerts are informational; it is not a capital-aware system. Local runs do persist state.

---

## 9. What is proven vs. what is not

### ✅ Reasonably established

- **The critical split-jump bug was real, and is fixed.** Demonstrated empirically on
  specific tickers with before/after numbers.
- **The edge survives proper walk-forward validation.** Parameters selected only on past
  data, evaluated only on unseen future data, across 10 folds and 20 months. t-stats of
  +9.88 and +13.18 with bootstrap CIs well clear of zero are not marginal results.
- **Parameter stability is high.** Both methods converged on consistent hold rules and
  unanimously on "no stop-loss" — not a knife-edge optimum.
- **The no-stop finding is robust**, confirmed by direct sensitivity testing.
- **The edge survives realistic borrow costs** for Strategy B (still >+100% at 200%/yr).
- **The optimized grid engine is numerically correct** (verified against the reference
  implementation).

### ⚠️ Unresolved or only partially modeled

- **Survivorship bias.** Price data comes from yfinance, which drops delisted tickers.
  16 of 614 tickers had no data at all. Reverse-split compliance names delist
  frequently — and those are disproportionately the *losers* for a short (companies that
  went to zero) and sometimes the *winners*. Direction of bias is not fully characterized.
- **Slippage is a flat 1.5%.** For sub-$1 microcaps with wide bid-ask spreads and thin
  books, real execution cost could be materially higher — and it scales with position size.
- **Borrow is modeled as a flat annualized rate**, not real per-ticker, per-day historical
  borrow data (which requires a paid vendor like Ortex/S3). The early Schwab data is
  encouraging (5–16%) but is 5 data points.
- **The shortability proxy is demonstrably imperfect** — already contradicted on 3 of 5
  live checks.
- **Only ~2 years of data** (Jun 2024 – Jul 2026), one market regime. No 2008/2020-style
  stress period, and no true black-swan squeeze in the sample.
- **Fill assumptions are optimistic.** Entering/exiting at the exact Open assumes you get
  the open price on an illiquid name.
- **Zero live trades.** Everything here is simulation. No real fills, no real slippage, no
  real rejections.
- **The exchange/liquidity data used by the proxy is current, not point-in-time** — a
  mild look-ahead in the shortability classification itself.

### 📊 Honest summary

The statistical evidence for an edge is strong and was arrived at through the right
methodology. The remaining risk is concentrated in **execution reality** — whether you can
actually borrow these names, at what cost, and at what fill quality — rather than in
whether the price pattern exists.

---

## 10. Command reference

```bash
# --- Daily operation ---
python scripts/run_signals.py                              # dry-run (default), today's signals
python scripts/run_signals.py --min-confidence High --lookback 7
python scripts/run_signals.py --login                      # weekly Schwab OAuth re-login
python scripts/run_signals.py --live                       # LIVE ORDERS - real money

# --- Data refresh ---
python -m split_strategy.scrapers.runner                   # scrape split events
python scripts/nightly_job.py                              # EDGAR backfill
python scripts/scan_early_edgar.py [YYYY-MM-DD]            # daily forward scan

# --- Research / validation ---
python scripts/build_prices.py --from-events --days 90     # build price panel
python scripts/backtest_recent.py --days 60 --save-prices  # recent-window backtest
python scripts/walk_forward_analysis.py                    # full grid + walk-forward
python scripts/tail_risk_test.py                           # stop-loss sensitivity
python scripts/analyze_robustness.py --days 60             # bootstrap / borrow / multiple-testing
python scripts/verify_gridsearch.py                        # verify optimized grid == reference
python scripts/capital_constrained_equity.py               # exposure-cap equity curves

# --- Dashboard ---
streamlit run streamlit_entry.py
```

### Key configuration (`src/split_strategy/config.py` / `.env`)

| Setting | Default | Meaning |
|---|---|---|
| `ACCOUNT_SIZE` | 10000 | Equity used for sizing |
| `TRADE_PCT` | 0.05 | Notional per trade (5%) |
| `MAX_EXPOSURE` | 1.0 | Total concurrent exposure cap |
| `STOP_LOSS_PCT` | 0.40 | Legacy stop (validation says: don't use) |
| `MAX_GAP_UP_PCT` | 0.30 | Gap-up skip threshold |

### Key documents

| File | Contents |
|---|---|
| `REPORT.md` | This document |
| `docs/VALIDATION_REPORT.md` | Full code-quality/correctness audit |
| `analysis/walk_forward_results.md` | Walk-forward output, all folds |
| `analysis/tail_risk_analysis.md` | Stop-loss sensitivity |
| `analysis/robustness_analysis.md` | Bootstrap / borrow / multiple-testing |
| `analysis/recent_backtest_results.md` | Recent 60-day window |
| `docs/SCHWAB_SETUP.md` | Schwab API setup walkthrough |
| `analysis/strategy.md` | ⚠️ Original research — computed with the split bug |

---

## 11. Open questions and next steps

**Before considering live capital:**

1. **Add a live-trading confirmation gate.** `--live` currently places orders with no
   summary/confirmation step. It should print the full intended order set and require
   explicit confirmation, plus a hard dollar ceiling.
2. **Reduce default position size.** Validation says 5% is what was tested, but §7.1
   shows sizing is the *only* effective tail-risk control. 1–2% while establishing live
   behavior is the conservative read.
3. **Accumulate shortability ground truth.** Currently 5 data points. Weeks of daily
   logging would let the proxy be recalibrated against reality — and would directly
   answer "what fraction of signals can I actually trade?"
4. **Extended dry-run monitoring.** Compare live-generated signals against backtest
   expectations for several weeks.

**Research improvements:**

5. **Address survivorship bias** — wire in Polygon (config hook exists, `POLYGON_API_KEY`
   unused) or another source with delisted-security coverage.
6. **Model slippage realistically** — scale with spread and position size vs. average
   dollar volume rather than a flat 1.5%.
7. **Point-in-time shortability** — the current classifier uses today's exchange listing
   data for historical events.
8. **Re-run the original notebook** (`analysis/strategy.ipynb`) with the split fix so the
   published numbers are no longer misleading.
9. **Fix EDGAR pagination** (`processing.py:192` reads only `filings.recent`, silently
   dropping older filings for high-volume filers — can corrupt `t_ann`).
10. **Test coverage** — there is essentially none for the backtest math or scoring logic.

---

_Generated as part of the reverse-split strategy research project. All numbers traceable
to the scripts and analysis files referenced throughout._
