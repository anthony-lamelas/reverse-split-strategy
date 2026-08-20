# Live Expectations — Strategy B under real constraints

_Generated 2026-08-19 · 683 pooled out-of-sample trades_

_Universe filtered to entry price >= **$1.00**, matching `config.MIN_ENTRY_PRICE`. Below that the strategy has no measured edge (29 trades, t=0.46, 95% CI [-20.6%, +32.3%]) and margin costs ~5.9x notional under FINRA 4210(c)._

The published +1208% figure assumes 5% sizing, no borrow cost, a flat 1.5% for all execution costs, and no spread filter. The live system uses 2% sizing, real borrow rates, marketable limits, and a spread veto. **The bottom line here — not the headline backtest — is what live performance should be measured against.**

## 1. Position sizing: 5% (backtest) vs 2% (live)

| Sizing | Trades taken | Skipped (no capital) | Final equity | Max DD |
|---|---:|---:|---:|---:|
| 5% (backtested) | 482 | 201 | $140,761 (+1308%) | -11.8% |
| 2% (live default) | 683 | 0 | $50,916 (+409%) | -4.9% |

With no concurrent-position cap, 2% sizing means capital rarely binds — nearly every signal is taken. The lower return is purely less leverage per trade, and the drawdown falls correspondingly.

## 2. Estimated bid-ask spread at entry

- Median: **1.46%** · mean 1.64% · 90th pct 3.83%
- Trades where the estimated spread alone exceeds the flat 1.5% assumption: **333 of 680 (49%)**
- Not estimable (too few bars): 3

### Spread-veto sweep

Skipping names wider than the threshold, exactly as the live filter does:

| Max spread | Trades kept | Win rate | Mean/trade | Final equity (2%) |
|---|---:|---:|---:|---:|
| 2% | 443 | 83.7% | +12.04% | $27,855 (+179%) |
| 3% | 564 | 84.8% | +12.83% | $39,547 (+295%) |
| 5% | 665 | 85.0% | +13.15% | $52,049 (+420%) |
| 8% | 681 | 84.7% | +13.04% | $53,273 (+433%) |
| none | 683 | 84.6% | +12.66% | $50,916 (+409%) |

## 3. Replacing the flat 1.5% with estimated spread costs

- Flat 1.5% assumption: mean +12.66%/trade, $50,916
- Spread-estimated: mean +11.98%/trade, $46,770

## 4. Shortability and borrow cost

- Proxy says **403 of 683** trades were likely unshortable. Live Schwab data has already contradicted this proxy on sub-$1 names, so treat it as a pessimistic bound.
- Shortable-only subset: 280 trades, 88.2% win rate, $20,871

| Annual borrow | Mean/trade | Final equity (2%) |
|---|---:|---:|
| 0% | +12.66% | $50,916 (+409%) |
| 10% | +12.07% | $47,819 (+378%) |
| 30% | +10.88% | $42,056 (+321%) |
| 50% | +9.69% | $36,835 (+268%) |
| 100% | +6.72% | $25,900 (+159%) |

## 5. All constraints combined — the realistic target

2% sizing · shortable-only · 5% spread veto · spread-based execution costs · 30%/yr borrow:

- **276 candidate trades, 276 taken**
- Win rate **87.0%**, mean **+11.43%**/trade
- Equity **$10,000 -> $18,620 (+86%)**, max drawdown -1.8%

> This is the number to compare live results against. It is still optimistic in ways this project cannot fix without paid data: it assumes limit orders fill, and it inherits the survivorship bias of yfinance dropping delisted tickers.
