# Deep-analysis brief — paste into a fresh Fable 5.1 session

Open a new Claude Code session in `C:\Coding\reverse-split-strategy` with model
`claude-fable-5-1`, then paste everything below the line.

Use the project venv: `.\venv\Scripts\python.exe` (it has `modal`; `.venv` does not).

---

You are auditing a live micro-cap short-selling system that trades real money. I want
an adversarial, quantitative analysis — not a summary and not encouragement. Your job
is to find what is wrong with these results before the market does.

**Read `HANDOFF.md` first.** It is current and explains the system, the state, and two
findings that recently overturned confident prior conclusions. Then read
`analysis/price_floor_corrected.md` and `analysis/postsplit_entry.md`.

## Context you need up front

The system shorts micro-caps between the SEC confirmation of a reverse stock split and
the split's effective date. It is live on Modal, trades at 09:25 ET, and **has never
opened a position** — every candidate has been rejected by a filter, mostly a $1.00
minimum entry price.

Two things were established in the last session, both of which you should try to break:

1. **`DATA/prices_full.pkl` is back-adjusted.** Only 45 of 998 events still show a
   price jump at the effective date. For a 1-for-N reverse split, back-adjustment
   multiplies pre-split bars by N — so the panel's median entry price is $9.80 while
   the implied real quoted price is $0.79. Since companies reverse-split *because*
   they are under $1, the $9.80 population makes no sense. See
   `src/split_strategy/backtest/price_basis.py`.
2. **The $1.00 floor's stated justification is wrong, but the floor is right anyway.**
   On corrected prices the sub-$1 bucket has a real walk-forward edge (174 OOS trades,
   +11.53%/trade, t=7.06) — but it costs 7.39× its notional in margin versus 1.00×
   above $1, so it earns +1.56% per margin dollar against +17.15%.

## What I want analysed

### A. The old strategy (announcement → effective date), on corrected prices

Every published figure for this strategy was computed on back-adjusted prices. Work
out which of them survive and which don't.

1. Re-derive the headline out-of-sample result with `--price-basis quoted` and compare
   against `--price-basis panel`. Where the two disagree, say which is right and why.
2. `analysis/live_expectations.md` claims **+86% over ~20 months, 87.0% win rate** as
   the number to judge live performance against. It was generated from panel prices.
   Is it still the right benchmark? If not, produce the corrected one.
3. The strategy's edge is concentrated in expensive names (89% of cumulative return
   came from ≥$5 in the old bucketing). On quoted prices, does that concentration
   hold, move, or vanish? A conclusion that only survives in one price basis is not a
   conclusion.
4. **Survivorship.** yfinance drops delisted tickers, and this strategy shorts
   companies at serious risk of delisting — so the missing names are plausibly the
   ones the strategy would have profited from most. This is currently **unmeasured**:
   `build_prices.py:27` defines `DATA/survivorship_coverage.csv` but the file has
   never been generated (only the older `DATA/prices_full_missing.txt` exists, and
   `build_events_combined()` yields 1104 events against 998 with price data). Quantify
   the gap first, then bound the bias: how much of the edge could be missing names,
   and does the sign favour or hurt the reported result?
5. **Costs.** Returns exclude borrow. `analysis/stats.py::borrow_adjusted` applies a
   sensitivity. At what borrow rate does the above-$1 strategy stop being worth
   trading, and how does that compare with observed rates in the audit log
   (`current_htb_rate` in the ledger, and skip reasons in the trading audit)?

### B. The new hypothesis (enter after the split) — verify the rejection

I concluded this does not work. Confirm or overturn it. Do not take the conclusion on
trust; the reasoning could be wrong in either direction.

1. Reproduce `scripts/postsplit_entry_test.py`. Check the arms are constructed as
   claimed, especially that `entry_anchor="t_split"` interacts correctly with
   `neutralize_split`.
2. Two traps were identified and guarded. Verify the guards actually hold:
   - Anchoring entry to the split makes every split-relative `hold_rule` select an
     empty window, which would report zero trades as "hypothesis failed". The engine
     now raises. Is the guard complete?
   - `estimate_spread` uses a 20-bar lookback that spans the split. On a raw series
     that is a discontinuity. Does this corrupt any arm?
3. The test used a fixed hold of N sessions. Is there a better exit rule for a
   post-split entry that I did not try — and if so, is the improvement real or the
   product of having searched?
4. I reported the result as negative partly on **sign instability across half-years**.
   Is that the right test at this sample size, or am I reading noise as instability?

### C. Cross-cutting methodology

1. **Multiple testing.** Across this project's history, many parameter combinations
   have been searched. `scripts/analyze_robustness.py::expected_max_winrate` exists for
   this. Quantify how much of the reported edge could be selection.
2. **Walk-forward integrity.** `src/split_strategy/backtest/walkforward.py` selects
   parameters in-sample per fold and tests out-of-sample. Audit it for leakage —
   specifically whether anything about a fold's test window can influence selection.
3. **The live/backtest gap.** The backtest reports 80%+ win rates. Live has taken zero
   trades in two weeks. Beyond the price-basis issue, what else differs between the
   simulated and real universes? Consider the spread veto, shortability, borrow-cost
   cap, `MAX_NEW_SHORTS_PER_DAY=1` and `MAX_TRADE_NOTIONAL=$50`.
4. **Is this strategy implementable at $4,944 of equity at all?** Given FINRA
   4210(c)'s $2.50/share floor and the observed candidate distribution, compute the
   realistic number of concurrent positions and the expected annual return on
   *capital*, not on notional. If the answer is "not meaningfully", say so plainly.

## Standards

- **Quantify.** Every claim gets a number, a sample size, and an uncertainty. `n`,
  mean, t, and a bootstrap CI — `src/split_strategy/analysis/stats.py` has the tools.
- **No post-hoc bucketing** presented as validation. If you slice after seeing results,
  label it as hypothesis generation and say what would test it out-of-sample.
- **Reproduce before you trust.** Re-run the scripts rather than quoting my numbers.
  If your numbers differ from mine, that is a finding — report it prominently.
- **Distinguish "no edge" from "unmeasured."** A wide confidence interval around a
  positive mean is ignorance, not evidence of absence. This exact confusion produced
  the wrong justification for the $1.00 floor.
- **Say when the data cannot answer the question.** The back-adjustment ambiguity is
  a real example: a continuous series means either the provider adjusted it or the
  split never executed, and prices alone cannot separate those.

## Constraints

- **Do not modify anything under the live trading path** — `scripts/run_trading.py`,
  `src/split_strategy/live/`, `src/split_strategy/broker/`, `modal_app.py`. Research
  code and new scripts are fair game.
- **Do not deploy to Modal** and do not run `scripts/run_trading.py --live`.
- **Do not authenticate to Schwab from this machine.** The OAuth refresh token exists
  in two copies (local and on the Modal Volume) and refreshing one may invalidate the
  other, which would halt live trading at 09:25. Read-only account data comes from
  `modal_app.py::account_snapshot`.
- `.\venv\Scripts\python.exe -m pytest` must stay green (505 tests at time of writing).

## Deliverable

A written analysis at `analysis/deep_review_fable.md` covering A, B and C, structured
as:

1. **Verdict** — is the old strategy's edge real, and at what size is it worth trading?
2. **What survived the corrected price basis, and what didn't** — table form, with the
   superseded figures named explicitly so stale numbers stop circulating.
3. **Findings**, ranked by how much they would change a decision, each with its
   evidence and its uncertainty.
4. **What you could not determine** and what data would settle it.
5. **Recommended next tests**, in priority order.

Push back on my conclusions where the evidence supports it. Two confident claims in
this project have already been overturned by exactly this kind of scrutiny — the
split-jump bug that invalidated the original backtest, and the price-basis error that
invalidated the floor's rationale. Assume there is a third.
