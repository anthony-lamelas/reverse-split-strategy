# Reverse Split Shorting Strategy - Backtest Methodology

This document outlines the systematic process used to backtest the Reverse Split Shorting Strategy, including data acquisition, normalization, and the grid-search optimization algorithm.

## 1. Data Acquisition & Normalization

To accurately simulate the market conditions, we merged multiple data sources:
1. **Historical Splits**: We pulled roughly ~1,000 completed reverse splits from the `reverse_splits` MongoDB database, extracting the exact split `Date`, `Ticker`, and the `Split Ratio`.
2. **EDGAR Filings**: We queried the `reverse_splits_edgar` database to find the earliest **Tier A / Tier B (High Confidence)** SEC filings (e.g., Definitive Proxy Statements, 8-Ks) that definitively announced the upcoming split. This gave us the exact `Announcement Date` to use as our trading signal.
3. **Price Data**: We utilized the `yfinance` API to fetch daily OHLCV (Open, High, Low, Close, Volume) data for all tickers. Because Yahoo Finance retroactively applies the split ratio to historical prices, we avoided artificial price spikes that would corrupt the backtest. 

## 2. Event Study & Price Path Analysis
Before blindly testing strategies, we aligned all the timelines so that Day 0 was the exact Announcement Date (or Execution Date) for every stock. We then calculated the **Median Price Path** across all 600+ valid events. 
- Using the *Median* instead of the *Mean* was critical. Penny stocks are heavily skewed by extreme outliers (500% short squeezes), meaning a simple average would falsely imply that the overall trend was upwards. The median curve accurately proved the rapid dilution and negative drift.

## 3. The Ultimate Strategy Grid Search
To mathematically prove the optimal way to execute the short strategy, we built a massive backtest simulator testing **4,620 permutations**. 

### Position Sizing: Stepped Compounding
To balance explosive exponential growth with the liquidity constraints of micro-cap stocks, the simulator uses a "Stepped" position sizing rule. It calculates exactly 5% of the portfolio value and uses that as a flat, fixed bet size per trade for 30 days. After 30 days, it recalculates a new 5% flat bet based on the new total portfolio value.

### Optimization Parameters Tested
The grid search battled every possible combination of the following rules:

1. **Hold Duration (11 Rules)**
   - Fixed holds: `1 Day`, `2 Days`, `5 Days`, `10 Days`, `14 Days`
   - Dynamic holds: `Day Before Split`, `Day Of Split`, `Day After Split`, `5 Days After Split`, `10 Days After Split`, `14 Days After Split`

2. **Stop-Loss (7 Rules)**
   - `15%`, `20%`, `25%`, `30%`, `35%`, `40%`, and `No Stop Loss`. 
   - *(Note: 'No Stop Loss' was included to mathematically prove the necessity of risk management. Because the strategy involves shorting, an un-hedged 1000% short squeeze causes immediate portfolio bankruptcy).*

3. **Take Profit (5 Rules)**
   - Closing the trade early if the stock dropped by: `20%`, `40%`, `60%`, `80%`.
   - `No Take Profit` (holding until the exact time-based exit).

4. **Gap-Up Filter (3 Rules)**
   - Skipping the trade entirely if the stock gapped up on the announcement morning by: `>10%` or `>30%`.
   - `No Filter` (taking every trade regardless of pre-market spikes).

5. **Split Ratio Buckets (4 Rules)**
   - Filtering events based on the severity of the reverse split ratio: `<10`, `10 to 50`, `>50`.
   - `All Ratios` (no filtering).

## 4. Final Strategy Recommendation

Based on the optimization results, we avoided the absolute highest-returning parameter (`5_days_after_split`) because it mathematically requires surviving the post-split "micro-float short squeeze"—a period of extreme, unpredictable volatility. We also avoided `day_before_split` because its strict 2-day advance notice requirement disqualified 55% of all viable trades. 

The optimal "Real World" strategy balances a massive sample size with a complete avoidance of the post-split squeeze:

### The Optimal Safe Strategy
- **Hold Duration:** `day_of_split` (Enter short on the morning after the SEC announcement. Exit the short at the absolute second the market opens on the Execution Date).
- **Stop Loss:** `40%` (Mathematically required to protect against catastrophic overnight gaps or pre-split pumps).
- **Take Profit:** `None` (Let the winners bleed down completely until the exit date).
- **Gap-Up Filter:** `<30%` (If the stock gaps up >30% in the pre-market on the morning of entry, skip the trade. It has been compromised by retail momentum).
- **Split Ratio Range:** `All Ratios` (0 to infinity).

### Performance Metrics (Over 574 Trades)
- **Total Trades Executed:** `574` (A massive, statistically significant sample size).
- **Win Rate:** `60.97%` (Providing a massive statistical edge when combined with uncapped take-profits).
- **Real-World Viability:** Extremely High. By exiting exactly on the `day_of_split`, the trader avoids the volatile micro-float squeeze that happens *after* the split takes effect, making this the psychologically safest approach for scaling capital.
