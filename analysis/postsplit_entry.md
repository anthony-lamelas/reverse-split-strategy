# Shorting reverse splits AFTER the split executes

_Generated 2026-09-03 · events 2024-06-07 → 2026-08-28 · prices 2024-05-28 → 2026-07-28_

Live parameters: stop-loss 40%, take-profit 20%, no gap-up filter, 1.5% flat slippage.

## 0. The price basis problem (found while building this test)

- Events: **1104**, with price data: 998, priced at entry: 960

- Series showing a split jump (raw): **45**

- Series with NO jump (back-adjusted, or the split never executed): **953**

- Median panel entry price: **$9.80** — 897 of 960 are ≥ $1.00

- Median implied *quoted* price: **$0.79** — 427 of 954 are ≥ $1.00


Companies reverse-split because they are under $1 and face delisting. A median entry price of $9.80 is not a population that needs to split; $0.79 is. The panel is back-adjusted, so `price_floor_walkforward.py` — which reads entry prices straight off it — calibrated the $1.00 floor against adjusted dollars.


> Caveat: a continuous series means either the provider adjusted it or the split never executed, and prices alone cannot separate those. Buckets below use the adjusted reading, which the medians above support.


## 1. Baseline — the current strategy (enter after the announcement)

| Arm | Trades | Mean/trade | Win rate | t | 95% CI (mean) | Avg hold |
|---|---:|---:|---:|---:|---|---:|
| Quoted ≥ $1.00 (live trades these) | 398 | +5.22% | 75% | +4.47 | [+2.89%, +7.46%] | 19d |
| Quoted < $1.00 (live REJECTS these) | 440 | +3.43% | 71% | +3.16 | [+1.29%, +5.52%] | 11d |

## 2. The hypothesis — enter at the effective date instead

Entry: Open of session +1 on/after the effective date. Exit: after N sessions, or on the stop/target.

| Arm | Trades | Mean/trade | Win rate | t | 95% CI (mean) | Avg hold |
|---|---:|---:|---:|---:|---|---:|
| Quoted < $1.00, hold 5d | 465 | -0.84% | 57% | -0.85 | [-2.78%, +1.06%] | 5d |
| Quoted < $1.00, hold 10d | 465 | -0.68% | 62% | -0.62 | [-2.84%, +1.40%] | 8d |
| Quoted < $1.00, hold 20d | 465 | -0.46% | 62% | -0.39 | [-2.80%, +1.81%] | 11d |
| Quoted ≥ $1.00, hold 5d | 393 | +1.27% | 63% | +1.26 | [-0.75%, +3.22%] | 5d |
| Quoted ≥ $1.00, hold 10d | 393 | +1.30% | 64% | +1.17 | [-0.90%, +3.41%] | 8d |
| Quoted ≥ $1.00, hold 20d | 393 | +0.99% | 67% | +0.81 | [-1.42%, +3.37%] | 12d |
| Quoted all, hold 5d | 858 | +0.13% | 60% | +0.18 | [-1.27%, +1.50%] | 5d |
| Quoted all, hold 10d | 858 | +0.23% | 63% | +0.29 | [-1.31%, +1.75%] | 8d |
| Quoted all, hold 20d | 858 | +0.20% | 64% | +0.24 | [-1.45%, +1.86%] | 11d |

> Several (bucket, hold) cells are reported. The best-looking cell is selected by having looked at all of them, so treat it as the ceiling of a search, not an estimate. The period table below is the honest check.


## 3. Consistency over time — quoted < $1.00, hold 10d

| Period | Trades | Mean/trade |
|---|---:|---:|
| 2024-H2 | 14 | +6.37% |
| 2025-H1 | 107 | -4.73% |
| 2025-H2 | 116 | +0.75% |
| 2026-H1 | 190 | -0.64% |
| 2026-H2 | 38 | +3.52% |


## 4. What borrow costs do to it

| Annual borrow | Mean/trade (< $1.00) | Mean/trade (≥ $1.00) |
|---|---:|---:|
| 0% | -0.68% | +1.30% |
| 25% | -1.20% | +0.75% |
| 50% | -1.72% | +0.19% |
| 100% | -2.75% | -0.92% |
| 200% | -4.82% | -3.14% |

_A reverse split shrinks the float, so borrow on these names tends to rise right when the position is opened. Rates of 50-200% are routine here._

