# Backtest v2 — pre-registration

_Written 2026-10-03, before any v2 result existed. If a later analysis departs from
this file, the departure is the finding to explain, not the file to edit._

## Why this exists

The first backtest reported +12.66% per trade. A grid search over 4,620 cells picked the
exit rule, and ticker-shuffled placebo data reproduced that number in 5 of 12 runs. The
rule set below is small, fixed in advance, and every headline is reported beside its
placebo.

## Data

- **Events:** `events_v2`, built by the live classifier over EDGAR history
  (`scripts/backfill_events.py`), reduced to executable events by
  `backtest/events.executable_events` — a definitive, future, dated split, all in one filing.
- **Prices:** unadjusted daily bars and the split table from Polygon
  (`backtest/prices_v2.py`), including delisted tickers.
- **Window:** the five most recent years the price subscription covers.
- **Holdout:** every event whose live entry date is on or after **2025-10-01**. It is
  excluded from all runs until the rule below is final, then evaluated **once**.

## The rule under test (primary)

The deployed configuration, exactly:

| Parameter | Value |
|---|---|
| Entry | open of the session after the filing date (`entry=live`) |
| Exit | open of the last session before the effective date (`exit=T-1`) |
| Take-profit | 20% below entry |
| Stop | 40% above entry |
| Price floor | $1.00 |

## Sensitivity grid (reported in full, never searched for a headline)

72 cells: entry {live, first_open} × exit {T-1, T-0} × stop {none, 40%, 25%} ×
target {20%, none} × floor {none, $0.50, $1.00}.

Three comparisons in it are questions asked in advance:

1. **first_open vs live entry** — does entering at the first open after EDGAR accepts the
   filing beat entering a session later? (Decides whether to build a real-time feed.)
2. **T-1 vs T-0 exit** — what does covering before the split cost or gain?
3. **Floor none / $0.50 / $1.00** — where is the edge after margin and borrow?

Choosing any cell other than the primary as the strategy requires the holdout to confirm it.

## Costs

`sim.Costs`: half the quoted spread on each market fill, borrow for the days held,
regulatory fees on entry. The default spread and borrow tables are **assumptions**
(spread 6% / 4% / 2.5% / 1% for price < $0.50 / < $1 / < $5 / ≥ $5; borrow 50%/yr under
$1, 30%/yr above). Every result is reported at 0×, 1× and 2× those costs, and the tables
are replaced by measured values from the shadow book (`shadow_trades`) once it has a
month of data.

## Statistics

- Per-trade net return: mean, t-statistic, bootstrap 95% CI (`analysis/stats.py`), win rate,
  count, and the count of events skipped by reason.
- Placebo: the same rule on 200 ticker-shuffled event sets (`sim.placebo_events`);
  report the placebo mean, its 95th percentile, and how many runs reach the real mean.
- Portfolio: `sim.portfolio` at $5,000 with FINRA short margin, 50% margin budget, and
  both the deployed caps ($50, 1/day) and 2%-of-equity sizing with no daily cap.
- Trades that cannot be closed at a known price (`no_exit_bar`) are counted and reported,
  with the result re-stated assuming each lost 100%.

## Go / no-go (Gate 3)

Go only if, for the primary rule on the development window at 1× costs:

1. the bootstrap 95% CI lower bound on mean net return per trade is above zero;
2. fewer than 5% of placebo runs reach the real mean;
3. the result survives 2× costs with a positive mean;
4. the holdout year, evaluated once, has a positive mean net return;
5. projected annual return on the $5,000 account at 2% sizing exceeds 10%.

Anything less is a no-go for adding capital. A no-go on the primary with a clear,
pre-asked advantage in one of the three comparisons above is a reason to re-register
that variant and test it on new data — not to adopt it.
