# Recent Backtest — Chosen 'Optimal Safe' Strategy

_Generated 2026-07-25 · window = last 60 days · source = `early_edgar_splits`_

**Strategy:** enter short the morning after the SEC announcement, exit at the open on the execution date; 40% stop-loss; no take-profit; skip if the entry gaps up >30%; all ratios. Position size = 5% of equity per trade; 1.5% flat slippage/fees.

> **Methodology note.** This uses the forward-looking `early_edgar_splits` scanner as the event source (what the live bot sees), not the original Tier A/B historical join. Numbers are therefore **not directly comparable** to the published 574-trade / 60.97% baseline. Sample is small; treat as directional, not statistically significant.

- Executed events in window: **79** (75 tickers)
- Tickers with no yfinance price data (delisted/OTC/unmapped): **3** — HSIGF, NVACW, OPADW

## 1. All signals (baseline)

- **Realistic (next-day entry):** trades=49, win_rate=55.1%, total_return=+2.38%, avg=+1.08%, median=+1.80%, maxDD=-9.18%
- Optimistic (same-day entry, look-ahead): trades=58, win_rate=53.4%, total_return=+8.33%, avg=+2.90%, median=+0.57%, maxDD=-8.71%

## 2. Shortability breakdown (Schwab realism)

**28 of 49 trades (57%) were likely NOT shortable at Schwab** (proxy classifier — see docs).

| Subset | Trades | Win rate | Sum P&L ($) | Avg return |
|---|---:|---:|---:|---:|
| Shortable (tradeable) | 21 | 61.9% | -110.08 | -1.07% |
| Likely unshortable | 28 | 50.0% | +347.86 | +2.70% |

Primary unshortable reason (by category):
- thin liquidity (no locate): 16
- sub-$1 price (non-marginable): 7
- OTC / non-major exchange: 5

## 3. Shortable-only (realistic expectation)

Re-run of the strategy restricted to the tradeable subset (portfolio compounding recomputed on just those trades):

- trades=21, win_rate=61.9%, total_return=-1.26%, avg=-1.07%, median=+1.80%, maxDD=-7.58%

## Trade log (realistic run)

| Ticker | Entry | Exit | Entry $ | Exit $ | Reason | Net % | Shortable? |
|---|---|---|---:|---:|---|---:|:--:|
| USAS | 2026-05-21 | 2026-06-23 | 5.44 | 5.00 | time_exit | +6.6% | yes |
| JBDI | 2026-05-22 | 2026-05-28 | 1.04 | 0.98 | time_exit | +4.3% | yes |
| TJGC | 2026-05-22 | 2026-05-26 | 6.57 | 2.19 | time_exit | +65.2% | yes |
| WETO | 2026-05-22 | 2026-06-02 | 0.88 | 0.99 | time_exit | -14.0% | no |
| MEHA | 2026-05-26 | 2026-05-28 | 0.09 | 0.08 | time_exit | +15.5% | no |
| SCYX | 2026-05-26 | 2026-05-29 | 5.43 | 5.92 | time_exit | -10.5% | no |
| VRAX | 2026-05-26 | 2026-07-09 | 6.72 | 9.41 | stop_loss | -41.5% | yes |
| SDOT | 2026-05-26 | 2026-05-27 | 2.98 | 3.25 | time_exit | -10.6% | no |
| DD | 2026-05-27 | 2026-06-24 | 149.04 | 144.75 | time_exit | +1.4% | yes |
| HKIT | 2026-05-27 | 2026-05-29 | 40.65 | 42.50 | time_exit | -6.1% | yes |
| SMSI | 2026-05-27 | 2026-06-04 | 3.95 | 4.00 | time_exit | -2.8% | no |
| CURLF | 2026-05-28 | 2026-06-05 | 9.63 | 10.77 | time_exit | -13.3% | no |
| JDZG | 2026-05-28 | 2026-06-01 | 40.40 | 56.56 | stop_loss | -41.5% | yes |
| CDTG | 2026-05-29 | 2026-06-01 | 7.03 | 5.49 | time_exit | +20.4% | no |
| JBDI | 2026-06-01 | 2026-06-11 | 0.94 | 1.32 | stop_loss | -41.5% | no |
| VRNO | 2026-06-02 | 2026-06-11 | 6.00 | 6.10 | time_exit | -3.2% | no |
| WXM | 2026-06-02 | 2026-06-04 | 4.40 | 6.16 | stop_loss | -41.5% | no |
| CETXP | 2026-06-03 | 2026-06-05 | 0.46 | 0.36 | time_exit | +19.4% | no |
| UPLD | 2026-06-05 | 2026-06-17 | 7.50 | 6.22 | time_exit | +15.6% | no |
| HON | 2026-06-08 | 2026-06-29 | 224.22 | 240.65 | time_exit | -8.8% | yes |
| GNTOF | 2026-06-09 | 2026-06-26 | 0.02 | 0.02 | time_exit | -1.5% | no |
| MNDR | 2026-06-10 | 2026-06-22 | 4.26 | 3.60 | time_exit | +14.0% | no |
| XXII | 2026-06-10 | 2026-06-12 | 6.72 | 6.42 | time_exit | +3.0% | no |
| BMGL | 2026-06-11 | 2026-06-22 | 6.36 | 6.92 | time_exit | -10.3% | yes |
| MQ | 2026-06-12 | 2026-06-30 | 15.12 | 16.68 | time_exit | -11.8% | yes |
| AIFU | 2026-06-15 | 2026-06-16 | 55.60 | 56.55 | time_exit | -3.2% | no |
| JBDI | 2026-06-18 | 2026-06-25 | 1.22 | 0.59 | time_exit | +49.8% | no |
| UVIX | 2026-06-18 | 2026-07-01 | 66.20 | 63.11 | time_exit | +3.2% | yes |
| MNDR | 2026-06-22 | 2026-06-29 | 3.60 | 3.00 | time_exit | +15.2% | no |
| ALIT | 2026-06-22 | 2026-06-30 | 11.36 | 11.10 | time_exit | +0.8% | yes |
| FCUV | 2026-06-22 | 2026-06-23 | 2.40 | 3.35 | stop_loss | -41.5% | yes |
| JBDI | 2026-06-24 | 2026-06-29 | 1.14 | 0.55 | time_exit | +50.3% | no |
| NAMI | 2026-06-24 | 2026-06-25 | 4.80 | 4.61 | time_exit | +2.5% | yes |
| RUBI | 2026-06-24 | 2026-06-26 | 7.80 | 5.84 | time_exit | +23.6% | yes |
| SRXH | 2026-06-25 | 2026-07-06 | 5.46 | 4.90 | time_exit | +8.8% | yes |
| GMEX | 2026-06-30 | 2026-07-02 | 3.42 | 3.87 | time_exit | -14.7% | yes |
| CGTL | 2026-07-02 | 2026-07-06 | 5.45 | 5.10 | time_exit | +4.8% | yes |
| NVVE | 2026-07-02 | 2026-07-06 | 6.39 | 5.97 | time_exit | +5.1% | no |
| VALE | 2026-07-07 | 2026-07-13 | 14.86 | 14.37 | time_exit | +1.8% | yes |
| ENLV | 2026-07-08 | 2026-07-09 | 7.09 | 6.86 | time_exit | +1.8% | no |
| YMAT | 2026-07-09 | 2026-07-10 | 2.38 | 2.18 | time_exit | +6.7% | yes |
| JEM | 2026-07-10 | 2026-07-14 | 8.50 | 6.32 | time_exit | +24.1% | yes |
| APUS | 2026-07-14 | 2026-07-24 | 0.72 | 0.66 | time_exit | +7.4% | no |
| AMZE | 2026-07-16 | 2026-07-23 | 0.09 | 0.09 | time_exit | -1.5% | no |
| QH | 2026-07-16 | 2026-07-17 | 6.14 | 7.05 | time_exit | -16.3% | no |
| BANL | 2026-07-17 | 2026-07-20 | 0.36 | 0.29 | time_exit | +16.9% | no |
| XPON | 2026-07-17 | 2026-07-21 | 3.88 | 2.77 | time_exit | +27.0% | no |
| SBEV | 2026-07-17 | 2026-07-23 | 0.09 | 0.09 | time_exit | -1.5% | no |
| MSS | 2026-07-21 | 2026-07-22 | 2.15 | 2.64 | time_exit | -24.3% | no |

_Shortability is a proxy estimate; exact historical Schwab borrow data is not retrievable. See docs/VALIDATION_REPORT.md and the plan for caveats._