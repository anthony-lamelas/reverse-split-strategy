# Deep review — Strategy B, the $1.00 floor, and the post-split hypothesis

_Generated 2026-09-05. Adversarial audit run against `docs/DEEP_ANALYSIS_PROMPT.md`.
Every figure below was recomputed from `DATA/` in this session; where a number differs
from a published one, both are shown and the difference is explained._

Reproduction artefacts: `scripts/price_basis_audit.py`,
`src/split_strategy/backtest/split_actions.py`, `tests/test_split_actions.py`,
`analysis/price_basis_audit.md`, `DATA/yahoo_split_actions.pkl`,
`DATA/yahoo_ticker_coverage.csv`, `DATA/survivorship_coverage.csv`. Test suite:
**515 passing** (505 before, 10 added).

---

## 1. Verdict

**There is a real, event-specific edge. It is about a third of the size the
published figures say, it is concentrated in exactly the names the live $1.00 floor
rejects, and at $4,944 of equity with the current caps it is worth about 1% a year.**

Three claims to separate, because the project keeps conflating them.

**The event effect is real.** Holding parameters fixed and reshuffling which ticker
each announcement belongs to, 200 placebo runs produced a mean per trade of +0.87%
(sd 0.84%, max +3.49%) against the real universe's +4.35%. Zero of 200 reached it.
The same test at the walk-forward's own chosen parameters gives +12.77% real against
a placebo mean of +2.89%, again 0 of 40. Shorting these announcements beats shorting
matched microcaps at the same times, and it is not an artefact of the machinery.

**The published magnitudes are not.** The headline +12.66% per out-of-sample trade
is not a measure of that effect. When the per-fold grid search is allowed to choose
the exit rule, twelve placebo universes where no split ever happened returned a mean
of **+10.03%** (sd 6.08%, max +18.85%), and **5 of the 12 matched or beat the real
+12.66%**. The walk-forward's out-of-sample discipline protects the *trades* it scores
but not the *statistic* it reports: mean per trade under a selected asymmetric exit
rule is upward-biased by roughly the size of the reported edge. The honest
fixed-parameter number for the deployed configuration is **+4.08% per trade over 783
trades (t = 4.96, 95% CI [+2.5%, +5.6%])**.

**The strategy as deployed is trading the wrong half of it.** On reconstructed quoted
prices the sub-$1 bucket earns +4.17% per trade over 526 trades (t = 4.22) with a
10-day mean hold; the above-$1 bucket earns +3.88% over 257 trades (t = 2.62) with a
25-day hold. Restrict to trades whose exit date was actually published before entry —
the only ones executable as specified — and the above-$1 bucket falls to 16 trades,
t = 0.77, CI [−5.2%, +10.1%]. **The universe the live system trades is unmeasured,
not proven.**

**At what size is it worth trading?** An event-driven simulation at $4,944 with FINRA
4210(c) margin, `MARGIN_EQUITY_PCT=0.5`, `MAX_TRADE_NOTIONAL=$50` and
`MAX_NEW_SHORTS_PER_DAY=1` returns:

| Borrow | Above-$1 universe, as deployed |
|---|---:|
| 0%/yr | +2.05%/yr |
| 30%/yr | +0.97%/yr |
| 50%/yr | +0.26%/yr |

Peak margin used: $747 of a $2,472 budget. The binding constraint is not margin and
not capital — it is candidate supply. Lifting the $50 cap and sizing at 2% of equity
raises this to +4.9%/yr at 30% borrow, still on a per-trade mean whose confidence
interval on the executable subset includes zero.

### The corrected benchmark, replacing `analysis/live_expectations.md`

`live_expectations.md` names **+86% over ~20 months at an 87.0% win rate** as the
number to judge live performance against. It is not usable: it was computed on
panel-basis prices, on the 683 walk-forward trades, at parameters (no stop-loss) the
live system does not run, through `backtest_mega`'s sequential compounding, which
assumes each trade resolves before the next opens when the median number of open
positions is 12.

Judge live performance against this instead, until something better is built:

| | Corrected |
|---|---|
| Expected trades per year, above-$1 universe | **20–40** (live has seen 1 candidate clear the floor in 12 sessions) |
| Mean per trade, deployed parameters | **+3.9%**, 95% CI [+1.0%, +6.7%], t = 2.62 |
| Mean per trade, executable subset only | **+3.2%**, 95% CI [−5.2%, +10.1%], n = 16 |
| Expected annual return on the $4,944 account | **+1.0%** at 30%/yr borrow, **+0.3%** at 50% |
| Borrow rate at which it stops being worth trading | **57%/yr** |
| Win rate to expect | **73%** at deployed parameters (62% on the 16 executable trades), not 87% |

The single most useful live datum will not be P&L. It will be the first ten fills:
whether the marketable limit fills at all, at what slippage, and at what borrow rate.

I would not increase size on this. I would first fix the two look-ahead defects in
section 3, then re-measure.

---

## 2. What survived the corrected price basis, and what didn't

The price-basis correction from 2026-09-04 was right in direction and incomplete in
size. `DATA/prices_full.pkl` is split-adjusted — that is now **measured, not
inferred** — but the adjustment is *cumulative over every later split*, so dividing
by the event's own ratio removes one factor out of several.

| Figure | Where it is published | Status | Corrected value |
|---|---|---|---|
| Panel is back-adjusted; 45 of 998 series show a jump | `HANDOFF.md`, `price_floor_corrected.md` | **Confirmed and upgraded to a measurement** | 968 of 984 provider splits inside the panel were absorbed; 908 of 913 dated before the panel's last 30 days (99.5%) |
| "Prices alone cannot separate adjusted from never-executed" | `price_basis.py` docstring, `HANDOFF.md` | **Superseded** | True of prices alone. The provider publishes its split table; fetching it answers the question |
| "prices are fetched with `auto_adjust=False` (raw/unadjusted)" | `backtest/prices.py` docstring | **Wrong** | Yahoo OHLC is always split-adjusted. Control: NVDA's 10-for-1 on 2024-06-10 returns $115.00 for 2024-06-03, not ~$1,150 |
| "yfinance frequently does NOT record micro-cap reverse splits" | `engine.neutralize_split` docstring | **Wrong for this dataset** | It recorded 1,111 splits across 754 event tickers, matching the declared ratio exactly on 808 of 816 |
| Median quoted entry $0.79, 43% ≥ $1.00 | `HANDOFF.md`, both analyses | **Superseded** | $0.70, **38.8%** ≥ $1.00 |
| Floor keeps 46% of priced events | `price_floor_corrected.md` | **Superseded** | 38.8% |
| Sub-$1: 174 OOS trades, +11.53%, t = 7.06 | `price_floor_corrected.md`, `HANDOFF.md` | **Not comparable** | Measured on the naive basis, which disagrees with the reconstruction on 45% of events and flips the floor decision for 220 of 896 |
| Above-$1: +17.15%/trade, t = 15.25 | `price_floor_corrected.md` | **Reproduced exactly, but not an edge estimate** | Reproduced (117 trades, t = 15.25). Placebo universes reach +17.4% under the same procedure |
| Above-$1 margin 1.00× vs sub-$1 7.39× | `price_floor_corrected.md` | **Survives, smaller** | 1.01× vs 6.34× on reconstructed prices |
| "one eleventh as much per dollar of capital" | `price_floor_corrected.md`, `HANDOFF.md` | **Superseded** | 6.9× per trade (+4.42% vs +0.64%), and it **reverses** once holding period is included — see finding 4 |
| 89% of cumulative return from ≥ $5 | prior bucketing, `analysis/strategy.md` lineage | **Refuted** | An artefact. On reconstructed prices the ≥$5 bucket is +3.41%, t = 1.34, and 48% of cumulative return comes from below $0.50 |
| "no measured edge below $1" (n=29, t=0.46) | `config.py:179`, `schwab_orders.py:167`, live skip messages | **Wrong, and still in the code** | The sub-$1 bucket is not the weaker half. At the deployed parameters it has twice the trades, a higher t-statistic (4.22 vs 2.62) and 10-day holds against 25. On the executable subset it is the only half that is measurable at all (185 trades vs 16). |
| +86% over ~20 months, 87.0% win rate | `analysis/live_expectations.md` | **Superseded** | Built on the 683 panel-basis trades, at parameters the live system does not run. See section 3, finding 2 |
| Sub-$1 margin ~5.9×, above-$1 ~0.4× | `config.py:179` | **Superseded** | 6.34× and 1.01×. The 0.4× came from panel prices with a $10.50 median |

Two figures are unchanged and worth keeping: returns themselves are ratios, so the
price basis does **not** corrupt any per-trade return. Only price-based *filtering*,
*bucketing* and *margin* were wrong. And `neutralize_split` is a no-op on 95% of
series, so it never introduced an error — it simply was not needed.

---

## 3. Findings, ranked by how much they change a decision

### Finding 1 — The panel entry price is a proxy for splits that had not happened yet

**This is the third overturned claim the brief asked me to assume exists.**

Yahoo's adjustment is cumulative: a bar is divided by the product of every split
after it. 265 of 658 event tickers reverse-split two or more times inside the
2024–2026 window, so the panel contains entry prices up to $791,000 (WOK), $114,000
(HUBC) and $75,600 (PAVS). 209 events price above $50 and 142 above $100.

| Splits absorbed after the entry bar | Events | Median panel price | Median quoted price |
|---:|---:|---:|---:|
| 0 | 153 | $3.11 | $3.11 |
| 1 | 537 | $8.01 | $0.59 |
| 2 | 161 | $74.25 | $0.48 |
| 3 | 40 | $1,097.71 | $0.95 |
| 4 | 7 | $1,289.06 | $0.49 |

Spearman correlation of log panel price with the number of *later* splits is
**+0.61**; with the reconstructed quoted price it is −0.14. A panel-price bucket is
therefore close to a bucket on "how many more times will this company reverse-split",
which is not knowable at entry and correlates with the collapse the strategy is
shorting.

That is what the "89% of cumulative return comes from ≥$5" result was measuring.
Re-bucketed on reconstructed prices the ordering inverts:

| Reconstructed entry price | Trades | Mean/trade | t | 95% CI | Share of cumulative return |
|---|---:|---:|---:|---|---:|
| < $0.50 | 332 | +4.62% | +3.80 | [+2.2%, +6.9%] | 48% |
| $0.50–$1.00 | 195 | +3.48% | +2.07 | [+0.2%, +6.7%] | 21% |
| $1.00–$2.50 | 129 | +1.61% | +0.70 | [−3.0%, +5.9%] | 7% |
| $2.50–$5.00 | 51 | +10.02% | +3.81 | [+4.9%, +14.7%] | 16% |
| ≥ $5.00 | 76 | +3.41% | +1.34 | [−1.7%, +8.2%] | 8% |

_Post-hoc bucketing, presented as hypothesis generation. The $2.50–$5.00 cell is the
best of five and should be treated as the ceiling of a search._

**Uncertainty.** The reconstruction depends on the provider's split table being
complete. It matched the declared ratio to within 1% on 808 of 816 events where both
exist, and 107 events have no provider split record at all — for those the
reconstruction leaves the panel price untouched, which is the conservative choice but
may be wrong if the provider simply lost the record.

### Finding 2 — The walk-forward's headline number is not an edge estimate

`walk_forward` selects parameters in-sample and scores disjoint out-of-sample events,
which is sound as far as it goes. But the statistic it reports — pooled mean per
trade — is upward-biased when the grid is free to pick an asymmetric exit rule, and
the bias is as large as the effect.

| Universe | Pooled OOS mean/trade |
|---|---:|
| Real events (reproduced exactly: 683 trades, t = 16.79) | **+12.66%** |
| Placebo, ticker-shuffled, 12 runs | mean **+10.03%**, sd 6.08%, max +18.85% |
| Placebo runs at or above the real result | **5 of 12** (empirical p ≈ 0.42) |

Individual placebo runs: +10.5, +12.9, +14.7, +8.0, +6.6, +17.4, +7.5, +18.9, +15.5,
−3.7, +4.0, +8.2. Same folds, same 4,620-cell grid, same `t_stat` selection; only the
ticker↔event pairing is randomised, so no placebo trade is near a real split.

Holding parameters fixed removes the bias and the effect reappears cleanly:

| Parameters (panel ≥ $1.00 universe, 746 trades) | Real | Placebo mean (40 runs) | Placebo p95 | Runs ≥ real |
|---|---:|---:|---:|---:|
| Live: stop 40%, target 20% | +4.35% | +0.62% | +1.74% | 0/40 |
| Walk-forward's own pick: no stop, target 20% | +12.77% | +2.89% | +7.31% | 0/40 |

So the correct reading is: the edge is real, and the right benchmark for the
walk-forward number is its own placebo distribution rather than zero. The excess is
roughly **+3.5 to +10 percentage points per trade** depending on the exit rule, not
+12.66%.

Two further defects in the same machinery:

- **The walk-forward validated a strategy the live system does not run.** It selected
  `stop_loss=inf` in **10 of 10 folds**. Live runs a 40% stop. That single difference
  takes the pooled fixed-parameter mean from +12.06% to +4.08%. The stop is the right
  call for tail risk — without it the worst trade in the sample is −299.5% and three
  exceed −100% — but the +12.66% benchmark does not describe the deployed system.
- **Selection sees 24% of its own test window.** In-sample is "announced before
  test_start", but 48% of events have a split more than 60 days after the
  announcement, so their trades run into the test window and are scored on prices
  from it. Across the 10 folds, 976 of 4,057 in-sample event-instances (24.1%) leak
  this way; in the first fold it is 60%. The out-of-sample *events* stay disjoint, so
  this is a soft leak, but it is real and it is largest exactly where the training set
  is smallest.

### Finding 3 — The exit rule is not executable for 45% of the backtest

Strategy B covers at the open on the effective date. That requires knowing the
effective date at entry. It frequently was not known.

`build_events_from_tier_ab` sets `t_ann = min(filing_date)` across *every* tier A/B
EDGAR filing linked to a split. Those filings include 3,394 FWPs, 865 424B3/424B5s,
482 10-K/10-Qs, 379 proxies and 160 S-1s. In 30.7% of events the announcement date
comes from a form that is not an 8-K or 6-K at all.

Checking the filings' own `effective_date` field: of the 548 splits where some filing
publishes the correct effective date, **45.1% have the backtest entering before that
filing existed** — 35.9% by more than 30 days, 27.2% by more than 90 days.

The consequences are measurable:

| Subset | Trades | Mean/trade | t | Mean hold |
|---|---:|---:|---:|---:|
| All events, `t_ann` as recorded | 783 | +4.08% | +4.96 | 15.1d |
| Exit date published on or before entry | 201 | +4.53% | +3.39 | 3.9d |
| Exit date **not** published at entry | 582 | +3.92% | +3.90 | 19.0d |
| The latter, re-entered on the publication date | 138 | **+0.83%** | +0.54 | 4.8d |

The last row is the honest version of the non-executable half: enter when you could
actually have known the exit date, and the edge is gone. The apparent edge in those
582 trades is bought with an entry you could not have taken.

This also explains the live/backtest universe gap. The live signal source
(`early_edgar_splits`) has a median announcement-to-effective gap of **5 days**;
`tier_ab`, which supplies 93% of the backtest, has **84 days**. They are not the same
population.

**Uncertainty.** The `effective_date` field is itself an LLM/parser product and is
absent for 47% of splits, so "45.1%" is measured on the subset where it can be
checked. The direction is not in doubt; the exact fraction is.

### Finding 4 — The floor's margin argument survives per trade and reverses per year

`price_floor_corrected.md` compares return per margin dollar *per trade*. On
reconstructed prices that comparison still favours the floor, by 6.9× rather than 11×:

| Bucket (reconstructed) | Trades | Median price | Margin/notional | Mean/trade | Return per margin $ |
|---|---:|---:|---:|---:|---:|
| ≥ $1.00 | 257 | $2.47 | 1.01× | +3.88% | **+4.42%** |
| < $1.00 | 526 | $0.39 | 6.34× | +4.17% | **+0.64%** |

But a margin dollar is not consumed once a year. The above-$1 names hold 25 days on
average, the sub-$1 names 10, and there are twice as many of the latter. In an
event-driven simulation at $4,944 with the real margin rule, the ranking flips:

| Universe (sized at 10% of equity, no daily cap) | 0% borrow | 50% | 100% |
|---|---:|---:|---:|
| Reconstructed ≥ $1.00 | +11.8%/yr | −2.8%/yr | — |
| Reconstructed < $1.00 | **+21.1%/yr** | **+15.2%/yr** | +9.7%/yr |

The above-$1 book cannot keep its margin budget busy: it took 152 trades in 2.09
years, against 526 signals on the other side of the floor. The sub-$1 book skipped 415 of
526 signals for want of margin and still earned more, because it recycles capital
three times as fast.

**I am not recommending removing the floor on this evidence.** Two things cut the
other way and neither is measured here: sub-$1 borrow rates are the worst in the
market and could plausibly exceed the 149%/yr break-even this bucket implies, and
the Corwin-Schultz spread estimates for these names (medians of 1.2–2.0%) are not
credible — the estimator returns a median of exactly 0.00% for the ≥$5 bucket, which
means it is clipping, not measuring. What I am saying is that the recorded
justification is now wrong twice over, and the decision deserves re-derivation rather
than inheritance.

### Finding 5 — Borrow, not margin, is the binding cost above $1

| Annual borrow | Reconstructed ≥ $1.00 | Reconstructed < $1.00 |
|---:|---:|---:|
| 0% | +3.88% | +4.17% |
| 25% | +2.17% | +3.47% |
| 50% | +0.45% | +2.77% |
| 100% | −2.97% | +1.37% |
| 200% | −9.82% | −1.44% |

**The above-$1 strategy breaks even at 57%/yr borrow; the sub-$1 bucket at 149%/yr;
the two together at 99%/yr.** The cheap names are *more* borrow-robust, because they
are held for 10 days instead of 25.

This is not hypothetical. The one candidate ever to clear the floor, DPU on
2026-09-04, was rejected at 33%/yr over ~104 days — 9.4% of notional, against a
`MAX_BORROW_COST_PCT` of 5%. If the above-$1 candidates keep being long-dated splits,
the borrow cap will bind more often than the price floor, exactly as the handoff
predicted. On the historical set the above-$1 bucket's holds run to 66 days at the
90th percentile.

Regulatory fees are not a factor: at a $50 notional they are 0.33% at $0.05/share and
under 0.02% above $1.

### Finding 6 — Survivorship is real, measured, and smaller than feared

`DATA/survivorship_coverage.csv` had never been generated. It exists now.

Of 1,082 events whose effective date has passed, **45 (4.2%) have no price history at
all** and 8 more (0.7%) have history that stops before the effective date. The missing
names are systematically worse companies: median ratio 20.0 against 14.0, and 23% have
a ratio ≥ 50 against 14% for the usable set. Direction of bias therefore favours the
strategy — the vanished names are the deeper collapses.

Bounding it: filling all 53 holes at various assumed returns moves the pooled mean
from +4.08% to

| Assumption for every missing event | Pooled mean |
|---|---:|
| 10th percentile of observed (−41.5%) | +1.19% |
| median of observed (+18.5%) | +4.99% |
| a flat +60% (total collapse) | +7.62% |
| a flat −40% (every one a squeeze) | +1.28% |

So the whole survivorship question is worth **at most ±3.5 percentage points**. There
is a structural reason it cannot be worth more: the 20% take-profit caps a trade at
+18.5%, and 64% of trades exit there. The strategy cannot collect the collapses that
survivorship bias hides. Separately, 13 of 729 panel tickers (1.8%) stopped being
served in the 39 days after the panel was built, which is the ongoing attrition rate.

### Finding 7 — Smaller defects, each worth fixing

- **Two different entry bars.** `price_floor_walkforward.entry_prices` takes the first
  Open strictly after `t_ann`; the engine and `price_basis.entry_basis` take
  `iloc[1]` of the bars on or after `t_ann`. They differ for 21 of 901 priced events
  (2.3%), so the panel-basis floor filters on a bar the engine does not trade.
- **Duplicated trades.** 59 of 783 trades (7.5%) share a ticker and entry date, in 28
  groups, 22 of which have identical returns. The event set de-duplicates on
  `(ticker, t_split)` but one company can have several effective dates. The mean moves
  +4.08% → +4.29% and n falls to 752; the real cost is understated standard errors.
  Live already enforces one position per ticker (`session.py:249`); the backtest does
  not.
- **66 of 958 events have `t_ann ≥ t_split`.** `build_events_from_early_edgar` rejects
  these; `build_events_from_tier_ab` does not. They mostly produce no trade under
  split-relative hold rules, but they do trade under integer ones.
- **`stop_loss=inf, take_profit=inf` silently collapses.** The portfolio goes negative,
  `backtest_mega` breaks the loop, and the run reports 8 trades and a −995% drawdown
  as if it were a result. A short can lose more than its notional; the engine should
  say so rather than return a truncated frame.
- **Sequential compounding is unachievable.** `backtest_mega` sizes each trade at
  `trade_pct` of the running portfolio value in `t_ann` order, but the median number
  of simultaneously open positions is 12 and the maximum is 47.
  `compounded_oos_curve` exists for this and the headline path does not use it.
- **Integer hold rules are off by one session.** `_holding_window` returns
  `iloc[1:n+1]` and the exit takes that bar's Open, so "hold 10 days" exposes 9
  sessions of price action.

---

## 4. Section B — the post-split hypothesis: rejection confirmed, reasoning corrected

I reproduced `scripts/postsplit_entry_test.py` against the current MongoDB build
(1,115 events vs the report's 1,104) and got the same table to within rounding: sub-$1
hold-10d gives 465 trades at −0.68%, t = −0.62.

**The arms are constructed as claimed.** `entry_anchor="t_split"` interacts correctly
with `neutralize_split`: the neutralisation runs first and divides all post-split bars
by the same constant, and since entry is after the split every bar in the trade shares
that constant, so returns are unaffected. For the 95% of series already adjusted it is
a no-op.

**Guard 1 holds.** All six split-relative hold rules raise; all five integer rules run.
One cosmetic hole: `hold_rule=True` passes `isinstance(..., int)` and silently becomes
a one-day hold. A more consequential gap is that neither `walk_forward` nor `run_grid`
accepts `entry_anchor`, so a post-split hypothesis cannot be walk-forward validated
with the existing tooling at all.

**Guard 2 is not needed, and was not tested.** I instrumented `estimate_spread`:
across the arms the test actually runs it is called **zero times**, because the
defaults are `slippage_model="flat"` and `max_spread_pct=inf`. With
`slippage_model="spread_estimated"` it is called 473 times, and even then only 16 of
1,111 provider splits left an unabsorbed discontinuity in the panel, so the exposure
would be about 1.4% of trades. The trap is real in principle and inert in practice.

**B.3 — is there a better exit rule?** I searched 110 cells: entry offsets 0–3, holds
of 2/3/5/10/20/40 sessions, and five stop/target combinations, on the sub-$1 bucket.

- Best cell: **t = +0.28** (offset 1, hold 10, stop 40%, no target).
- Cells with t > +2: **0**. Cells with t < −2: **48**.

Under noise, the best of 110 independent tests would be near |t| = 3.1. Getting +0.28
as the *maximum* is much stronger evidence of no post-split drift than the original
nine-cell table. The rejection is confirmed and strengthened.

**B.4 — the instability argument was wrong.** The report rejected the hypothesis
partly on half-year means flipping sign (+6.4, −4.7, +0.8, −0.6, +3.5). At this
sample size that is not evidence. Simulating 20,000 replicas under a *constant* true
mean equal to the pooled one, with the observed per-trade sd of 23.6% and the observed
per-period counts, the expected number of sign flips is 1.90 and **P(≥ 4 flips) =
0.056**. A chi-square homogeneity test across the five periods gives **p = 0.198**.
There is no detectable heterogeneity; the sign flips are what a zero-mean series looks
like.

So: right conclusion, invalid reason. The defensible reason to reject is that the
pooled mean is indistinguishable from zero and a 110-cell search cannot find a single
positive t-statistic.

---

## 5. Section C — remaining methodology answers

**Multiple testing.** `expected_max_winrate(574, 4620)` gives 58.6% against a
published 60.97%, and the module's own caveat that correlated permutations lower the
true bar is correct. But this analytic bound is the weaker tool. The placebo
distribution in finding 2 measures the same thing end to end, including the fold
structure and the selection metric, and it says the grid search can manufacture the
entire reported walk-forward magnitude on data with no events in it. Use the placebo,
not the formula. Note also that the small-sample arms are far more exposed: at n = 89
(the reconstructed above-$1 walk-forward) the chance-adjusted win-rate bound is 71.8%.

**Walk-forward integrity.** Selection cannot see out-of-sample *events*: `in_sample`
is `t_ann < test_start` and `out_sample` is the complement within the window, disjoint
by construction, and the grid summary is computed only on in-sample trades. The leak
is through prices, not events, and is quantified in finding 2 (24.1%). One cosmetic
issue: `make_folds` derives its end boundary from the global `t_ann.max()`.

**The live/backtest gap** is now fully accounted for:

| Cause | Size |
|---|---|
| Panel prices made the backtest think 94% of candidates clear $1.00 | Real share is 38.8% |
| The live feed is a different, cheaper population than the backtest's | early_edgar median reconstructed price $0.47 (27.4% ≥ $1) vs tier_ab $0.74 (41.0%) |
| Live filters on the **bid**, not the last | Pushes marginal names below the floor |
| Small live sample | 1 of 15 candidates cleared the floor; at a true 27% rate, P(≤1 of 15) ≈ 0.06 |
| Borrow cap | Already binding: DPU, the only name ever to clear the floor, failed it |

The observed live candidate prices — 12 rejections between $0.044 and $0.893, median
$0.196 — are an independent confirmation of the reconstruction and a flat
contradiction of the panel.

**Is this implementable at $4,944?** Not meaningfully, as configured. The numbers are
in section 1. Peak margin usage under the deployed caps was $747 of a $2,472 budget,
so neither FINRA 4210(c) nor account size is the limit; the limit is that the
above-$1 universe produces roughly 20–40 executable candidates a year and the $50
notional cap turns each into about $2 of expected profit. The honest expected annual
return on capital is **+1% at 30% borrow**, against a per-trade mean whose executable
confidence interval spans zero.

---

## 6. What I could not determine

- **Whether the sub-$1 edge survives real execution.** The two things that would kill
  it — borrow rates on sub-$1 hard-to-borrow microcaps, and the true spread — are both
  unmeasured. Corwin-Schultz on daily bars returns a median spread of 0.00% for the
  ≥$5 bucket, which is a clipping artefact, so I do not trust any of its estimates
  here. Live Schwab quotes would settle both in weeks.
- **The true fraction of non-executable events.** The `effective_date` field exists on
  975 of 1,032 splits but publishes the *correct* date for only 548. The 45.1%
  look-ahead figure is measured on those 548.
- **What happened to the 45 vanished tickers.** Delisting, acquisition, and ticker
  reassignment are indistinguishable from a missing yfinance series. Their high median
  ratio suggests collapse, which favours the strategy, but that is an inference.
- **Whether the 107 events with no provider split record ever split.** For these the
  reconstruction leaves the panel price alone, which is conservative but could be
  wrong in either direction.
- **Whether ticker reuse contaminates the panel.** 48 events have a price series that
  begins after their effective date, which is what a reassigned symbol looks like. I
  did not chase these individually.

**What would settle it:** a point-in-time data source with delisted names and
historical NBBO (Polygon's flat files, or CRSP), plus a few weeks of logged Schwab
borrow rates and quoted spreads on the sub-$1 candidates the system is currently
rejecting. The latter costs nothing but logging.

---

## 7. Recommended next tests, in priority order

1. **Rebuild the event table on definitive announcements only.** Use the pipeline's own
   `is_definitive_split_announcement` flag, or restrict to 8-K/6-K, and require that
   the effective date was published on or before `t_ann`. Then re-run everything. This
   is the single change most likely to move the verdict, in either direction, and the
   current backtest cannot be trusted until it is made.
2. **Start logging what the system rejects.** Every skipped candidate's Schwab bid,
   ask, borrow rate and shortability, every morning. Two months of that answers the
   spread and borrow questions that no amount of daily-bar analysis can. It requires
   no change to the trading path and no new capital.
3. **Re-derive the $1.00 floor on reconstructed prices, with holding period in the
   denominator.** The margin argument is correct per trade and reverses per year. The
   answer may still be "keep the floor" — but on the borrow and spread evidence from
   step 2, not on the per-trade margin ratio.
4. **Fix the benchmark.** Regenerate `analysis/live_expectations.md` at the deployed
   parameters (40% stop, 20% target, `day_of_split`), on reconstructed prices, on the
   executable universe, through the margin-constrained simulator rather than
   `backtest_mega`'s sequential compounding. Report the placebo distribution alongside
   the headline. The current +86% is not a target the live system can hit.
5. **Correct the three places that record the wrong reason for the floor** —
   `config.py:179`, `schwab_orders.py:167`, and the live skip message, which currently
   tells the audit log "no measured edge" about the better-measured half of the
   strategy.
6. **Add `entry_anchor` to `run_grid` and `walk_forward`** so alternative entry
   hypotheses can be validated with the same rigour as the main one, and make
   `backtest_mega` raise rather than truncate when the simulated portfolio is wiped
   out.
7. **Only then consider size.** Nothing here justifies lifting `MAX_TRADE_NOTIONAL`
   while the above-$1 universe's executable sample is 16 trades.

---

## 8. Reproducing this

```bash
.\venv\Scripts\python.exe scripts\price_basis_audit.py            # fetches the split table
.\venv\Scripts\python.exe scripts\price_basis_audit.py --no-fetch # reuses the cache
.\venv\Scripts\python.exe -m pytest -q                            # 515 passing
```

The walk-forward, placebo, executable-universe and capital simulations were run from
scratch scripts in this session; their inputs are `DATA/events_combined_cache.pkl`,
`DATA/prices_full.pkl` and `DATA/yahoo_split_actions.pkl`, and the methods are
described inline above in enough detail to rebuild them. Nothing under
`scripts/run_trading.py`, `src/split_strategy/live/`, `src/split_strategy/broker/` or
`modal_app.py` was modified, no Modal deploy was run, and no Schwab authentication was
performed.
