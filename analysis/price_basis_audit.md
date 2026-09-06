# Price basis, settled against the provider's split table

_Generated 2026-09-05 · `scripts/price_basis_audit.py` · events 958 · panel 2024-05-28 → 2026-07-28_

## 1. Is the panel split-adjusted?

- Split records from the provider: **1111**, 984 of them inside the panel's date range.
- Panel is continuous across the split (**adjusted**): **968**
- Panel jumps at the split (**not adjusted**): **16**
- Of the unadjusted ones, **11** fall in the panel's final 30 days — provider ingestion lag, not a property of the company.
- Before that cutoff: 908 adjusted vs 5 not (99.5% adjusted).

This is the question `backtest/price_basis.py` records as unanswerable ("prices alone cannot separate those"). Prices alone cannot. The provider publishes the split it applied, and that does.

## 2. The adjustment is cumulative

| Splits absorbed AFTER the entry bar | Events | Median panel price | Median quoted price |
|---:|---:|---:|---:|
| 0 | 153 | $3.11 | $3.110 |
| 1 | 537 | $8.01 | $0.590 |
| 2 | 161 | $74.25 | $0.480 |
| 3 | 40 | $1,097.71 | $0.950 |
| 4 | 7 | $1,289.06 | $0.485 |
| 5 | 3 | $738.00 | $0.820 |

The panel price climbs with the number of splits still to come; the quoted price does not. A filter on the panel price is a filter on the future.

## 3. Entry price by basis

| Basis | Events priced | Median | Share ≥ $1.00 | Share ≥ $5.00 |
|---|---:|---:|---:|---:|
| Panel (what every backtest reads) | 901 | $10.50 | 94.0% | 72.6% |
| Quoted (reconstructed) | 901 | $0.70 | 38.8% | 13.1% |
| Naive quoted (panel ÷ event ratio) | 896 | $0.74 | 43.9% | 21.1% |

The naive conversion agrees with the reconstruction on only **55%** of events; it is more than 2x too high on 24% and more than 2x too low on 17%. It flips the $1.00 floor decision for **220** of 896 events.

## 4. Survivorship coverage

- Event tickers: 754; present in the panel: 729
- Events whose ticker is absent from the panel: **29** of 958
- Tickers in the panel the provider no longer serves: **13** of 729 (1.8%), measured 39 days after the panel's last bar.

_Coverage row appended to `DATA/survivorship_coverage.csv`._

