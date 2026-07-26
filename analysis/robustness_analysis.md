# Robustness Analysis

_Generated 2026-07-25 | window = last 60 days | n = 49 trades_

## 1. Is the sample big enough to conclude anything?

- Mean per-trade return: **+1.08%** (std 22.4%)
- t-statistic: **+0.34** (needs |t| > ~2 for significance)
- Bootstrap 95% CI on expectancy: **[-5.06%, +7.36%]**
- P(true expectancy > 0) given this data: **63%**
- Trades needed to detect an edge this size at 95% confidence: **~1,712**

## 2. What do borrow fees do to it?

- Median holding period: **5 days** (mean 7.8)

| Borrow rate (annual) | Mean per-trade return |
|---|---:|
| 0% (as backtested) | +1.08% |
| 10% | +0.87% |
| 30% | +0.44% |
| 50% | +0.01% |
| 100% | -1.06% |
| 200% | -3.20% |

_Hard-to-borrow micro-caps routinely cost 50-200%+ annualized._

## 3. What if stops fill on the gap, not at the stop price?

- Stop-loss exits: **5** of 49 trades
- Mean return, idealized stop fill: **+1.08%**
- Mean return, realistic gap-through fill: **+0.57%**
- Cost of this assumption: **-0.51%** per trade

## 4. The 4,620-permutation problem

The published strategy was the winner of a **4,620-permutation grid search** over 574 trades. If the strategy had **zero real edge** (true win rate 50%):
- Standard error of win rate at n=574: **2.09%**
- Expected *best* win rate across 4,620 trials by pure chance: **~58.6%**
- Published win rate: **60.97%**

The permutations are highly correlated (many share most parameters), so the true chance-adjusted threshold sits below this independent-tests upper bound - but 60.97% is not comfortably above it. **The selected parameters are plausibly overfit.**
