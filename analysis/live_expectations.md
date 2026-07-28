# Live Expectations — Strategy B under real constraints

_Generated 2026-07-27 · 551 pooled out-of-sample trades_

The published +1208% figure assumes 5% sizing, no borrow cost, a flat 1.5% for all execution costs, and no spread filter. The live system uses 2% sizing, real borrow rates, marketable limits, and a spread veto. **The bottom line here — not the headline backtest — is what live performance should be measured against.**

## 1. Position sizing: 5% (backtest) vs 2% (live)

| Sizing | Trades taken | Skipped (no capital) | Final equity | Max DD |
|---|---:|---:|---:|---:|
| 5% (backtested) | 506 | 45 | $130,770 (+1208%) | -12.0% |
| 2% (live default) | 551 | 0 | $32,304 (+223%) | -4.9% |

With no concurrent-position cap, 2% sizing means capital rarely binds — nearly every signal is taken. The lower return is purely less leverage per trade, and the drawdown falls correspondingly.

## 2. Estimated bid-ask spread at entry

- Median: **1.61%** · mean 1.76% · 90th pct 3.85%
- Trades where the estimated spread alone exceeds the flat 1.5% assumption: **288 of 550 (52%)**
- Not estimable (too few bars): 1

### Spread-veto sweep

Skipping names wider than the threshold, exactly as the live filter does:

| Max spread | Trades kept | Win rate | Mean/trade | Final equity (2%) |
|---|---:|---:|---:|---:|
| 2% | 350 | 78.3% | +10.47% | $20,481 (+105%) |
| 3% | 450 | 79.8% | +11.29% | $26,828 (+168%) |
| 5% | 533 | 80.5% | +11.50% | $32,769 (+228%) |
| 8% | 548 | 80.5% | +11.46% | $33,706 (+237%) |
| none | 551 | 80.4% | +11.01% | $32,304 (+223%) |

## 3. Replacing the flat 1.5% with estimated spread costs

- Flat 1.5% assumption: mean +11.01%/trade, $32,304
- Spread-estimated: mean +10.26%/trade, $29,874

## 4. Shortability and borrow cost

- Proxy says **304 of 551** trades were likely unshortable. Live Schwab data has already contradicted this proxy on sub-$1 names, so treat it as a pessimistic bound.
- Shortable-only subset: 247 trades, 86.2% win rate, $18,483

| Annual borrow | Mean/trade | Final equity (2%) |
|---|---:|---:|
| 0% | +11.01% | $32,304 (+223%) |
| 10% | +10.61% | $31,103 (+211%) |
| 30% | +9.80% | $28,803 (+188%) |
| 50% | +8.99% | $26,634 (+166%) |
| 100% | +6.97% | $21,747 (+117%) |

## 5. All constraints combined — the realistic target

2% sizing · shortable-only · 5% spread veto · spread-based execution costs · 30%/yr borrow:

- **243 candidate trades, 243 taken**
- Win rate **84.8%**, mean **+10.96%**/trade
- Equity **$10,000 -> $16,932 (+69%)**, max drawdown -1.8%

> This is the number to compare live results against. It is still optimistic in ways this project cannot fix without paid data: it assumes limit orders fill, and it inherits the survivorship bias of yfinance dropping delisted tickers.
