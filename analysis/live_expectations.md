# Live Expectations — Strategy B under real constraints

_Generated 2026-07-28 · 560 pooled out-of-sample trades_

The published +1208% figure assumes 5% sizing, no borrow cost, a flat 1.5% for all execution costs, and no spread filter. The live system uses 2% sizing, real borrow rates, marketable limits, and a spread veto. **The bottom line here — not the headline backtest — is what live performance should be measured against.**

## 1. Position sizing: 5% (backtest) vs 2% (live)

| Sizing | Trades taken | Skipped (no capital) | Final equity | Max DD |
|---|---:|---:|---:|---:|
| 5% (backtested) | 333 | 227 | $101,842 (+918%) | -3.5% |
| 2% (live default) | 514 | 46 | $42,863 (+329%) | -7.3% |

With no concurrent-position cap, 2% sizing means capital rarely binds — nearly every signal is taken. The lower return is purely less leverage per trade, and the drawdown falls correspondingly.

## 2. Estimated bid-ask spread at entry

- Median: **1.60%** · mean 1.75% · 90th pct 3.96%
- Trades where the estimated spread alone exceeds the flat 1.5% assumption: **291 of 559 (52%)**
- Not estimable (too few bars): 1

### Spread-veto sweep

Skipping names wider than the threshold, exactly as the live filter does:

| Max spread | Trades kept | Win rate | Mean/trade | Final equity (2%) |
|---|---:|---:|---:|---:|
| 2% | 353 | 80.2% | +15.46% | $24,725 (+147%) |
| 3% | 446 | 80.3% | +15.92% | $31,467 (+215%) |
| 5% | 544 | 81.8% | +17.49% | $41,440 (+314%) |
| 8% | 558 | 81.7% | +17.43% | $43,068 (+331%) |
| none | 560 | 81.4% | +17.33% | $42,863 (+329%) |

## 3. Replacing the flat 1.5% with estimated spread costs

- Flat 1.5% assumption: mean +17.33%/trade, $42,863
- Spread-estimated: mean +16.57%/trade, $39,999

## 4. Shortability and borrow cost

- Proxy says **361 of 560** trades were likely unshortable. Live Schwab data has already contradicted this proxy on sub-$1 names, so treat it as a pessimistic bound.
- Shortable-only subset: 199 trades, 85.9% win rate, $18,870

| Annual borrow | Mean/trade | Final equity (2%) |
|---|---:|---:|
| 0% | +17.33% | $42,863 (+329%) |
| 10% | +16.45% | $40,312 (+303%) |
| 30% | +14.71% | $35,064 (+251%) |
| 50% | +12.97% | $30,510 (+205%) |
| 100% | +8.62% | $21,006 (+110%) |

## 5. All constraints combined — the realistic target

2% sizing · shortable-only · 5% spread veto · spread-based execution costs · 30%/yr borrow:

- **195 candidate trades, 195 taken**
- Win rate **83.6%**, mean **+14.20%**/trade
- Equity **$10,000 -> $17,019 (+70%)**, max drawdown -1.7%

> This is the number to compare live results against. It is still optimistic in ways this project cannot fix without paid data: it assumes limit orders fill, and it inherits the survivorship bias of yfinance dropping delisted tickers.
