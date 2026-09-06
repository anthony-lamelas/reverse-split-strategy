# The $1.00 floor, re-validated on corrected prices

_Generated 2026-09-04 · `scripts/price_floor_walkforward.py --price-basis quoted` ·
events `DATA/events_combined_cache.pkl` (958) · prices `DATA/prices_full.pkl`_

## Why this was re-run

`price_floor_walkforward.py` read entry prices straight off `DATA/prices_full.pkl`.
That panel is **back-adjusted**: of 998 evaluable events only 45 still show a price
jump at the effective date, and back-adjustment for a 1-for-N reverse split
multiplies pre-split bars by N. So a stock really quoted at $0.11 appears at $0.88.

Two medians make the direction unambiguous:

| | Median entry price | Share >= $1.00 |
|---|---:|---:|
| Panel (what the analysis used) | **$9.80** | 94% |
| Quoted (what the live filter sees) | **$0.79** | 43% |

Companies reverse-split because they are under $1 and face delisting. A population
median of $9.80 has no reason to split. $0.79 does.

## The walk-forward, both bases

Identical method throughout: parameter selection inside each fold, 60-day test
windows, `t_stat` selection, `entry_offset=1`, split-adjusted returns. Only the
dollars the floor is measured in change.

| Run | Universe kept | OOS trades | Win rate | Mean/trade | t | 95% CI |
|---|---:|---:|---:|---:|---:|---|
| Floor $1.00, **panel** prices | 862 / 926 (93%) | 683 | 84.6% | +12.66% | 16.79 | [+11.18%, +14.14%] |
| Floor $1.00, **quoted** prices | 408 / 896 (46%) | 117 | 94.0% | **+17.15%** | 15.25 | [+14.95%, +19.35%] |
| **Below** $1.00, quoted prices | 488 / 896 (54%) | 174 | 77.6% | **+11.53%** | 7.06 | [+8.33%, +14.73%] |

Two things follow immediately.

**The floor is roughly twice as restrictive as believed.** It keeps 46% of priced
events, not 93%. That is the explanation for the live system rejecting 100% of
candidates through late August while the analysis expected it to reject about a
quarter.

**The original justification for the floor was wrong.** It rested on the sub-$1
bucket showing "no measured edge" — n=29, t=0.46, CI [-20.6%, +32.3%]. Measured on
quoted prices with a full walk-forward, that bucket has **174 out-of-sample trades,
+11.53% per trade, t=7.06**. That is not an absence of edge. The earlier figure was
a thin post-hoc slice of adjusted-price buckets, and it was measuring a different
population than the one the live filter rejects.

## But the floor is still correct — for a different reason

Return per trade is the wrong denominator. Margin is the binding constraint, and
FINRA 4210(c) floors a short's requirement at $2.50/share below $5 — so a cheap
stock consumes many times its own notional in collateral.

| Bucket (quoted) | Events | Median price | Margin per $1 notional | Mean/trade | **Return per margin dollar** |
|---|---:|---:|---:|---:|---:|
| >= $1.00 | 408 | $4.72 | **1.00x** | +17.15% | **+17.15%** |
| < $1.00 | 488 | $0.34 | **7.39x** | +11.53% | **+1.56%** |

The rejected bucket earns about **one eleventh as much per dollar of capital tied
up**. On a ~$4,900 account that difference is the whole game: the same collateral
either funds one sub-$1 short or eleven ordinary ones.

So the floor survives, and should stay — but the reason written in
`schwab_orders.py` ("no measured edge below $1") is not the reason. The reason is
capital efficiency, and it is much stronger than the one currently recorded.

> Anything justified by a wrong argument invites being relitigated on the wrong
> grounds. That is exactly how this re-run started.

## Caveats

- The quoted conversion assumes a continuous series means the provider adjusted it.
  A continuous series could also mean the split never executed; prices alone cannot
  separate those. 953 of 998 series are continuous, and the medians above support
  the adjusted reading, but it is an inference.
- Events with no usable ratio cannot be converted and drop out (896 of 958 priced).
- Margin multiples here use `margin.margin_multiple` at the FINRA floor with
  `house_multiple=1.0`. Schwab may require more.
- The margin line printed by `price_floor_walkforward.py` itself is computed from
  the trades frame, which carries **panel** prices — so it reports 0.30x/0.96x and
  understates the true cost. The table above recomputes it on quoted prices.
- Borrow cost is not in these returns. Sub-$1 names are the hardest to borrow, so
  the gap between the buckets is, if anything, wider than shown.
