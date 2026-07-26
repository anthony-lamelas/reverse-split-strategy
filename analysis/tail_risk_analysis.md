# Tail-Risk Mitigation Test

_Generated 2026-07-26. Isolates the effect of forcing a stop-loss back in, holding
each fold's walk-forward-selected hold_rule/take_profit/gap/ratio fixed._

## Why this test exists

Walk-forward validation (`analysis/walk_forward_results.md`) found **"no stop-loss"
was selected in every single fold**, both selection philosophies, across 20 months of
out-of-sample testing. That's a real, consistent finding — but it also means every
trade carries open-ended downside. The worst historical trade (SBET, May 2025) moved
-709%, a ~-35% single-trade hit on the whole portfolio at 5% position sizing. A
2-year sample may not contain a true black-swan squeeze. This test checks whether
adding a wide, "catastrophic-only" stop (well above where the optimizer would ever
trigger on normal volatility) is cheap insurance or an expensive mistake.

## Result: stops hurt more than they help, and don't reliably reduce drawdown

| Stop cap | Return-max: trades stopped | Return-max equity (100%-cap) | Stability: trades stopped | Stability equity (100%-cap) |
|---|---:|---:|---:|---:|
| None (baseline) | 0 | $58,307 (+483%) | 0 | $130,770 (+1208%) |
| 300% | 15 | $49,932 (+399%) | 6 | $59,755 (+498%) |
| 200% | 22 | $56,367 (+464%) | 8 | $74,765 (+648%) |
| 150% | 35 | $42,121 (+321%) | 17 | $43,053 (+331%) |
| 100% | 67 | $28,714 (+187%) | 37 | $37,183 (+272%) |
| 50% | 124 | $24,117 (+141%) | 71 | $36,842 (+268%) |

Full detail (win rate, mean return, max drawdown) in `scripts/tail_risk_test.py` output.

**Every stop level tested reduced expectancy monotonically** (mean return/trade fell
as the cap tightened, for both strategies). More surprisingly, **max drawdown did not
reliably improve either** — it moved non-monotonically as the cap tightened (e.g.
return-max: -34.8% at no-stop, -21.6% at 300%, back to -34.0% at 150%). The
mechanism: these are thin, illiquid microcaps prone to temporary spikes that reverse
before the planned exit date. A stop locks in the loss on the spike instead of
letting the position ride out the reversion — actively converting winning or
breakeven trades into losses, not just capping genuine catastrophic squeezes.

## Conclusion: position sizing, not a price stop, is the right tail-risk lever

The strategy's edge appears to depend on NOT reacting to intra-trade volatility. A
price-based stop fights that directly. The worst historical trade (-709%) only cost
~35% of the portfolio because of 5% position sizing, not because of a stop — sizing
is what actually bounds single-trade impact:

| Position size (trade_pct) | Worst-case portfolio impact from a -709%-return trade | Impact from a hypothetical -2000% squeeze |
|---|---:|---:|
| 5% (current) | -35.5% | -100% (would floor the account) |
| 3% | -21.3% | -60% |
| 2% | -14.2% | -40% |
| 1% | -7.1% | -20% |

**Recommendation:** do not add a price-based stop-loss to the live strategy — the
data argues against it. Instead, size positions conservatively (start well below the
backtested 5%, e.g. 1-2%, especially while live-testing) so that even an
unprecedented single-trade squeeze is a bruise, not a portfolio-ending event. This
also directly caps the impact of the capital-overcommitment issue fixed alongside
this test (many concurrent open positions compound the same single-name risk).

See `scripts/tail_risk_test.py` for the reproducible test.
