# Walk-Forward Validation & Extensive Grid Search

_Generated 2026-07-28 | 958 historical events (2024-06-07 to 2026-07-22) | corrected engine (split-jump neutralized) | realistic entry (next-session open)_

**Method:** rolling 60-day out-of-sample windows. For each window, a full 4,620-permutation grid search runs on events announced strictly BEFORE that window (in-sample); the winning parameters are frozen and evaluated on the window itself (out-of-sample, never seen by the optimizer). Two selection philosophies are compared: picking the permutation with the best in-sample **return** (prone to overfitting to a few lucky trades) vs. the best in-sample **t-statistic** (favors an edge that shows up consistently, penalizes noisy small samples). Only the pooled out-of-sample numbers below should be trusted as an estimate of live performance — in-sample numbers are what the optimizer already knew and are not evidence of anything.

## Selection: Return-maximizing (`total_return_pct`)

11 folds, 658 pooled out-of-sample trades.

| Fold | Test window | In-N | Selected params | In-sample metric | OOS trades | OOS win% | OOS return% |
|---|---|---:|---|---:|---:|---:|---:|
| | 2024-11-22 -> 2025-01-21 | 50 | hold=day_before_split, stop=None, TP=None, gap=30%, ratio=(0,inf) | 169.10 | 21 | +90.48% | +75.76% |
| | 2025-01-21 -> 2025-03-22 | 80 | hold=day_before_split, stop=None, TP=None, gap=30%, ratio=(0,inf) | 372.96 | 41 | +80.49% | +107.27% |
| | 2025-03-22 -> 2025-05-21 | 141 | hold=14_days_after_split, stop=None, TP=60%, gap=None, ratio=(0,inf) | 944.06 | 82 | +63.41% | +16.86% |
| | 2025-05-21 -> 2025-07-20 | 227 | hold=day_before_split, stop=None, TP=None, gap=None, ratio=(0,inf) | 2264.13 | 78 | +74.36% | +159.06% |
| | 2025-07-20 -> 2025-09-18 | 332 | hold=day_before_split, stop=None, TP=None, gap=None, ratio=(0,inf) | 6024.53 | 77 | +83.12% | +258.30% |
| | 2025-09-18 -> 2025-11-17 | 437 | hold=day_before_split, stop=None, TP=None, gap=None, ratio=(0,inf) | 21844.05 | 38 | +94.74% | +238.92% |
| | 2025-11-17 -> 2026-01-16 | 482 | hold=day_before_split, stop=None, TP=None, gap=None, ratio=(0,inf) | 74272.20 | 114 | +83.33% | +1217.22% |
| | 2026-01-16 -> 2026-03-17 | 633 | hold=day_before_split, stop=None, TP=None, gap=None, ratio=(0,inf) | 979546.72 | 124 | +81.45% | +1337.09% |
| | 2026-03-17 -> 2026-05-16 | 797 | hold=day_before_split, stop=None, TP=None, gap=None, ratio=(0,inf) | 14078329.48 | 58 | +84.48% | +141.18% |
| | 2026-05-16 -> 2026-07-15 | 900 | hold=day_before_split, stop=None, TP=None, gap=None, ratio=(0,inf) | 33953747.04 | 24 | +37.50% | -0.92% |
| | 2026-07-15 -> 2026-09-13 | 954 | hold=day_before_split, stop=None, TP=None, gap=None, ratio=(0,inf) | 33640947.80 | 1 | +0.00% | -0.07% |

**Pooled out-of-sample: 658 trades, win rate 78.4%, mean return/trade +34.65%, t-stat +15.40**
- Bootstrap 95% CI on expectancy: [+30.13%, +38.92%], P(edge>0) = 100%
**Compounded equity under realistic capital constraints** (trades routinely overlap - see concurrency note below):

| Max exposure | Trades taken | Trades skipped (no capital) | Final equity | Max drawdown |
|---|---:|---:|---:|---:|
| 100% (cash-collateralized) | 182 | 476 | $10,000 -> $77,725 (+677.2%) | -14.3% |
| 200% | 343 | 315 | $10,000 -> $218,473 (+2084.7%) | -17.6% |
| Unconstrained (unrealistic) | 658 | 0 | $10,000 -> $771,576 (+7615.8%) | -30.2% |

**Parameter stability across folds** (how often each choice was selected):
- hold_rule: day_before_split×10, 14_days_after_split×1
- stop_loss: inf×11
- take_profit: inf×10, 0.6×1
- max_gap_up: inf×9, 0.3×2

**Shortability: 407 of 658 pooled OOS trades (62%) likely NOT shortable at Schwab.**
- Shortable-only, 100% exposure cap: 251 candidate trades, 148 taken, win rate 86.1%, mean return/trade +43.26%, compounded equity $10,000 -> $77,212 (+672.1%)

**Concurrency:** up to 192 positions open simultaneously (median 89 on days with any open) - this is why an exposure cap changes the equity curve so much above.

**Borrow-cost sensitivity** (100% exposure cap; median OOS holding period 77 days):

| Annual borrow rate | Mean return/trade | Compounded equity (100% cap) |
|---|---:|---:|
| 0% | +34.65% | $10,000 -> $77,725 (+677.2%) |
| 10% | +32.37% | $10,000 -> $59,621 (+496.2%) |
| 30% | +27.81% | $10,000 -> $54,078 (+440.8%) |
| 50% | +23.24% | $10,000 -> $39,878 (+298.8%) |
| 100% | +11.83% | $10,000 -> $23,438 (+134.4%) |
| 200% | -10.99% | $10,000 -> $4,478 (-55.2%) |

Exit reasons in pooled OOS trades: time_exit=630, take_profit=28

## Selection: Stability-favoring (t-stat) (`t_stat`)

11 folds, 560 pooled out-of-sample trades.

| Fold | Test window | In-N | Selected params | In-sample metric | OOS trades | OOS win% | OOS return% |
|---|---|---:|---|---:|---:|---:|---:|
| | 2024-11-22 -> 2025-01-21 | 50 | hold=day_of_split, stop=None, TP=20%, gap=None, ratio=(0,inf) | 62.74 | 24 | +87.50% | +17.66% |
| | 2025-01-21 -> 2025-03-22 | 80 | hold=day_of_split, stop=None, TP=20%, gap=30%, ratio=(10,50) | 49.07 | 36 | +88.89% | +20.90% |
| | 2025-03-22 -> 2025-05-21 | 141 | hold=day_before_split, stop=None, TP=20%, gap=None, ratio=(0,inf) | 20.68 | 55 | +78.18% | +29.81% |
| | 2025-05-21 -> 2025-07-20 | 227 | hold=day_of_split, stop=None, TP=20%, gap=None, ratio=(0,inf) | 15.77 | 88 | +87.50% | +68.65% |
| | 2025-07-20 -> 2025-09-18 | 332 | hold=day_of_split, stop=None, TP=20%, gap=None, ratio=(0,inf) | 16.31 | 85 | +88.24% | +44.10% |
| | 2025-09-18 -> 2025-11-17 | 437 | hold=day_of_split, stop=None, TP=20%, gap=None, ratio=(50,inf) | 19.03 | 4 | +100.00% | +3.75% |
| | 2025-11-17 -> 2026-01-16 | 482 | hold=day_of_split, stop=None, TP=20%, gap=None, ratio=(50,inf) | 20.48 | 15 | +93.33% | +12.25% |
| | 2026-01-16 -> 2026-03-17 | 633 | hold=day_of_split, stop=None, TP=60%, gap=None, ratio=(0,inf) | 21.78 | 143 | +77.62% | +713.53% |
| | 2026-03-17 -> 2026-05-16 | 797 | hold=day_of_split, stop=None, TP=60%, gap=None, ratio=(0,inf) | 24.63 | 82 | +76.83% | +155.05% |
| | 2026-05-16 -> 2026-07-15 | 900 | hold=day_of_split, stop=None, TP=60%, gap=None, ratio=(0,inf) | 25.38 | 26 | +61.54% | +10.05% |
| | 2026-07-15 -> 2026-09-13 | 954 | hold=day_of_split, stop=None, TP=60%, gap=None, ratio=(0,inf) | 25.16 | 2 | +0.00% | -0.89% |

**Pooled out-of-sample: 560 trades, win rate 81.4%, mean return/trade +17.33%, t-stat +14.43**
- Bootstrap 95% CI on expectancy: [+14.87%, +19.61%], P(edge>0) = 100%
**Compounded equity under realistic capital constraints** (trades routinely overlap - see concurrency note below):

| Max exposure | Trades taken | Trades skipped (no capital) | Final equity | Max drawdown |
|---|---:|---:|---:|---:|
| 100% (cash-collateralized) | 333 | 227 | $10,000 -> $101,842 (+918.4%) | -3.5% |
| 200% | 494 | 66 | $10,000 -> $208,447 (+1984.5%) | -16.5% |
| Unconstrained (unrealistic) | 560 | 0 | $10,000 -> $305,149 (+2951.5%) | -16.5% |

**Parameter stability across folds** (how often each choice was selected):
- hold_rule: day_of_split×10, day_before_split×1
- stop_loss: inf×11
- take_profit: 0.2×7, 0.6×4
- max_gap_up: inf×10, 0.3×1

**Shortability: 361 of 560 pooled OOS trades (64%) likely NOT shortable at Schwab.**
- Shortable-only, 100% exposure cap: 199 candidate trades, 199 taken, win rate 85.9%, mean return/trade +16.87%, compounded equity $10,000 -> $44,175 (+341.8%)

**Concurrency:** up to 84 positions open simultaneously (median 24 on days with any open) - this is why an exposure cap changes the equity curve so much above.

**Borrow-cost sensitivity** (100% exposure cap; median OOS holding period 11 days):

| Annual borrow rate | Mean return/trade | Compounded equity (100% cap) |
|---|---:|---:|
| 0% | +17.33% | $10,000 -> $101,842 (+918.4%) |
| 10% | +16.45% | $10,000 -> $90,548 (+805.5%) |
| 30% | +14.71% | $10,000 -> $81,144 (+711.4%) |
| 50% | +12.97% | $10,000 -> $67,668 (+576.7%) |
| 100% | +8.62% | $10,000 -> $44,545 (+345.5%) |
| 200% | -0.09% | $10,000 -> $13,875 (+38.7%) |

Exit reasons in pooled OOS trades: take_profit=324, time_exit=236

## Baseline: published strategy, frozen, no re-optimization

Same period (2024-11-22 -> 2026-09-13), applying the original published 'Optimal Safe' strategy (day_of_split / 40% stop / no TP / 30% gap filter) with NO per-fold re-optimization — i.e., what happens if you just trade the original recipe blindly:

- trades=729, win_rate=+58.30%, total_return=+11035.57%, maxDD=-18.53%

## Bottom line

See the pooled out-of-sample stats above for both selection methods. If both the return-maximizing AND the t-stat-selection out-of-sample results show a win rate meaningfully above 50% with a positive, statistically resolved expectancy (t-stat comfortably > 2, tight bootstrap CI excluding zero), the edge is real. If the in-sample numbers look great but pooled out-of-sample collapses toward breakeven, that confirms the strategy was overfit to its own history.
