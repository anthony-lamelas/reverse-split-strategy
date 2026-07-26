# Walk-Forward Validation & Extensive Grid Search

_Generated 2026-07-26 | 745 historical events (2024-06-07 to 2026-07-20) | corrected engine (split-jump neutralized) | realistic entry (next-session open)_

**Method:** rolling 60-day out-of-sample windows. For each window, a full 4,620-permutation grid search runs on events announced strictly BEFORE that window (in-sample); the winning parameters are frozen and evaluated on the window itself (out-of-sample, never seen by the optimizer). Two selection philosophies are compared: picking the permutation with the best in-sample **return** (prone to overfitting to a few lucky trades) vs. the best in-sample **t-statistic** (favors an edge that shows up consistently, penalizes noisy small samples). Only the pooled out-of-sample numbers below should be trusted as an estimate of live performance — in-sample numbers are what the optimizer already knew and are not evidence of anything.

## Selection: Return-maximizing (`total_return_pct`)

10 folds, 469 pooled out-of-sample trades.

| Fold | Test window | In-N | Selected params | In-sample metric | OOS trades | OOS win% | OOS return% |
|---|---|---:|---|---:|---:|---:|---:|
| | 2024-12-27 -> 2025-02-25 | 50 | hold=day_before_split, stop=None, TP=None, gap=30%, ratio=(0,inf) | 192.67 | 34 | +79.41% | +72.63% |
| | 2025-02-25 -> 2025-04-26 | 103 | hold=14_days_after_split, stop=None, TP=60%, gap=30%, ratio=(0,inf) | 410.40 | 62 | +64.52% | +65.28% |
| | 2025-04-26 -> 2025-06-25 | 168 | hold=14_days_after_split, stop=None, TP=60%, gap=None, ratio=(0,inf) | 789.51 | 76 | +65.79% | -2.20% |
| | 2025-06-25 -> 2025-08-24 | 247 | hold=day_before_split, stop=None, TP=None, gap=None, ratio=(0,inf) | 1716.98 | 68 | +80.88% | +195.18% |
| | 2025-08-24 -> 2025-10-23 | 348 | hold=day_before_split, stop=None, TP=None, gap=None, ratio=(0,inf) | 5263.28 | 40 | +90.00% | +202.62% |
| | 2025-10-23 -> 2025-12-22 | 398 | hold=day_before_split, stop=None, TP=None, gap=None, ratio=(0,inf) | 16130.41 | 59 | +83.05% | +320.40% |
| | 2025-12-22 -> 2026-02-20 | 485 | hold=day_before_split, stop=None, TP=None, gap=None, ratio=(0,inf) | 68132.61 | 43 | +86.05% | +118.62% |
| | 2026-02-20 -> 2026-04-21 | 557 | hold=day_before_split, stop=None, TP=None, gap=None, ratio=(0,inf) | 149067.70 | 41 | +73.17% | +33.68% |
| | 2026-04-21 -> 2026-06-20 | 638 | hold=day_before_split, stop=None, TP=None, gap=None, ratio=(0,inf) | 199303.16 | 37 | +48.65% | +6.69% |
| | 2026-06-20 -> 2026-08-19 | 716 | hold=day_before_split, stop=None, TP=None, gap=None, ratio=(0,inf) | 212652.73 | 9 | +77.78% | +3.31% |

**Pooled out-of-sample: 469 trades, win rate 74.4%, mean return/trade +25.88%, t-stat +9.88**
- Bootstrap 95% CI on expectancy: [+20.51%, +30.66%], P(edge>0) = 100%
**Compounded equity under realistic capital constraints** (trades routinely overlap - see concurrency note below):

| Max exposure | Trades taken | Trades skipped (no capital) | Final equity | Max drawdown |
|---|---:|---:|---:|---:|
| 100% (cash-collateralized) | 230 | 239 | $10,000 -> $58,307 (+483.1%) | -34.8% |
| 200% | 378 | 91 | $10,000 -> $129,753 (+1197.5%) | -35.9% |
| Unconstrained (unrealistic) | 469 | 0 | $10,000 -> $211,761 (+2017.6%) | -34.7% |

**Parameter stability across folds** (how often each choice was selected):
- hold_rule: day_before_split×8, 14_days_after_split×2
- stop_loss: inf×10
- take_profit: inf×8, 0.6×2
- max_gap_up: inf×8, 0.3×2

**Shortability: 259 of 469 pooled OOS trades (55%) likely NOT shortable at Schwab.**
- Shortable-only, 100% exposure cap: 210 candidate trades, 154 taken, win rate 81.9%, mean return/trade +35.74%, compounded equity $10,000 -> $58,573 (+485.7%)

**Concurrency:** up to 86 positions open simultaneously (median 55 on days with any open) - this is why an exposure cap changes the equity curve so much above.

**Borrow-cost sensitivity** (100% exposure cap; median OOS holding period 27 days):

| Annual borrow rate | Mean return/trade | Compounded equity (100% cap) |
|---|---:|---:|
| 0% | +25.88% | $10,000 -> $58,307 (+483.1%) |
| 10% | +24.35% | $10,000 -> $55,321 (+453.2%) |
| 30% | +21.31% | $10,000 -> $69,333 (+593.3%) |
| 50% | +18.26% | $10,000 -> $47,467 (+374.7%) |
| 100% | +10.65% | $10,000 -> $28,254 (+182.5%) |
| 200% | -4.58% | $10,000 -> $9,736 (-2.6%) |

Exit reasons in pooled OOS trades: time_exit=427, take_profit=42

## Selection: Stability-favoring (t-stat) (`t_stat`)

10 folds, 551 pooled out-of-sample trades.

| Fold | Test window | In-N | Selected params | In-sample metric | OOS trades | OOS win% | OOS return% |
|---|---|---:|---|---:|---:|---:|---:|
| | 2024-12-27 -> 2025-02-25 | 50 | hold=10_days_after_split, stop=None, TP=20%, gap=30%, ratio=(0,inf) | 58.98 | 50 | +90.00% | +25.58% |
| | 2025-02-25 -> 2025-04-26 | 103 | hold=day_of_split, stop=None, TP=20%, gap=None, ratio=(0,inf) | 18.25 | 51 | +82.35% | +29.17% |
| | 2025-04-26 -> 2025-06-25 | 168 | hold=day_of_split, stop=None, TP=20%, gap=None, ratio=(0,inf) | 12.98 | 66 | +84.85% | +48.37% |
| | 2025-06-25 -> 2025-08-24 | 247 | hold=day_of_split, stop=None, TP=20%, gap=None, ratio=(0,inf) | 14.97 | 80 | +90.00% | +65.88% |
| | 2025-08-24 -> 2025-10-23 | 348 | hold=day_of_split, stop=None, TP=20%, gap=None, ratio=(0,inf) | 16.15 | 42 | +92.86% | +38.33% |
| | 2025-10-23 -> 2025-12-22 | 398 | hold=day_of_split, stop=None, TP=20%, gap=None, ratio=(0,inf) | 18.10 | 72 | +81.94% | +54.22% |
| | 2025-12-22 -> 2026-02-20 | 485 | hold=day_of_split, stop=None, TP=20%, gap=None, ratio=(0,inf) | 19.71 | 57 | +77.19% | +31.20% |
| | 2026-02-20 -> 2026-04-21 | 557 | hold=day_of_split, stop=None, TP=60%, gap=None, ratio=(0,inf) | 20.25 | 63 | +66.67% | +48.41% |
| | 2026-04-21 -> 2026-06-20 | 638 | hold=day_of_split, stop=None, TP=20%, gap=None, ratio=(0,inf) | 20.58 | 52 | +57.69% | +10.72% |
| | 2026-06-20 -> 2026-08-19 | 716 | hold=day_of_split, stop=None, TP=60%, gap=None, ratio=(0,inf) | 20.03 | 18 | +77.78% | +9.13% |

**Pooled out-of-sample: 551 trades, win rate 80.4%, mean return/trade +11.01%, t-stat +13.18**
- Bootstrap 95% CI on expectancy: [+9.32%, +12.56%], P(edge>0) = 100%
**Compounded equity under realistic capital constraints** (trades routinely overlap - see concurrency note below):

| Max exposure | Trades taken | Trades skipped (no capital) | Final equity | Max drawdown |
|---|---:|---:|---:|---:|
| 100% (cash-collateralized) | 506 | 45 | $10,000 -> $130,770 (+1207.7%) | -12.0% |
| 200% | 551 | 0 | $10,000 -> $165,763 (+1557.6%) | -12.0% |
| Unconstrained (unrealistic) | 551 | 0 | $10,000 -> $165,763 (+1557.6%) | -12.0% |

**Parameter stability across folds** (how often each choice was selected):
- hold_rule: day_of_split×9, 10_days_after_split×1
- stop_loss: inf×10
- take_profit: 0.2×8, 0.6×2
- max_gap_up: inf×9, 0.3×1

**Shortability: 304 of 551 pooled OOS trades (55%) likely NOT shortable at Schwab.**
- Shortable-only, 100% exposure cap: 247 candidate trades, 247 taken, win rate 86.2%, mean return/trade +12.70%, compounded equity $10,000 -> $44,520 (+345.2%)

**Concurrency:** up to 39 positions open simultaneously (median 13 on days with any open) - this is why an exposure cap changes the equity curve so much above.

**Borrow-cost sensitivity** (100% exposure cap; median OOS holding period 5 days):

| Annual borrow rate | Mean return/trade | Compounded equity (100% cap) |
|---|---:|---:|
| 0% | +11.01% | $10,000 -> $130,770 (+1207.7%) |
| 10% | +10.61% | $10,000 -> $119,798 (+1098.0%) |
| 30% | +9.80% | $10,000 -> $103,328 (+933.3%) |
| 50% | +8.99% | $10,000 -> $88,757 (+787.6%) |
| 100% | +6.97% | $10,000 -> $56,987 (+469.9%) |
| 200% | +2.92% | $10,000 -> $20,486 (+104.9%) |

Exit reasons in pooled OOS trades: take_profit=346, time_exit=205

## Baseline: published strategy, frozen, no re-optimization

Same period (2024-12-27 -> 2026-08-19), applying the original published 'Optimal Safe' strategy (day_of_split / 40% stop / no TP / 30% gap filter) with NO per-fold re-optimization — i.e., what happens if you just trade the original recipe blindly:

- trades=537, win_rate=+58.66%, total_return=+1123.46%, maxDD=-16.59%

## Bottom line

See the pooled out-of-sample stats above for both selection methods. If both the return-maximizing AND the t-stat-selection out-of-sample results show a win rate meaningfully above 50% with a positive, statistically resolved expectancy (t-stat comfortably > 2, tight bootstrap CI excluding zero), the edge is real. If the in-sample numbers look great but pooled out-of-sample collapses toward breakeven, that confirms the strategy was overfit to its own history.
