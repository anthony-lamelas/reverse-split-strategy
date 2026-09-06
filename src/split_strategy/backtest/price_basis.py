"""Which dollars is a backtest price denominated in?

The live system filters on the price Schwab quotes right now. The backtest filters
on whatever is in `DATA/prices_full.pkl`. Those are not the same number, and the
difference is the split ratio.

The evidence
------------
Of 851 evaluable events in the current panel, only 43 show a price jump at the
effective date. The other 806 are continuous - the provider back-adjusted them.
Back-adjustment for a 1-for-N reverse split MULTIPLIES pre-split bars by N, so a
stock that really traded at $0.11 appears in the panel at $0.88.

Two independent checks say the panel is adjusted rather than simply full of splits
that never happened:

  median panel entry price   $9.90   (94% of events >= $1.00)
  median panel price / ratio $0.71   (43% of events >= $1.00)

Companies do reverse splits because they are under $1 and the exchange is
threatening to delist them. A company trading at $9.90 has no reason to do a
1-for-10. The second column is where reverse-splitters actually live.

Why it matters
--------------
`scripts/price_floor_walkforward.py` reads entry prices straight off this panel, so
the $1.00 floor - the rule now rejecting live candidates - was calibrated against
adjusted dollars. That is the most likely explanation for the live system rejecting
100% of candidates while the analysis expected it to reject about a quarter.

The honest caveat
-----------------
A continuous series means EITHER the provider adjusted it OR the split never
executed, and prices alone cannot separate those. Under the first reading the
quoted price is panel/ratio; under the second it is the panel price itself. Callers
get both and should report the sensitivity rather than pick one silently.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from .engine import split_factor


def entry_basis(
    events: pd.DataFrame,
    prices: pd.DataFrame,
    entry_offset: int = 1,
) -> pd.DataFrame:
    """Per event: the panel entry price and the price a live filter would have seen.

    `entry_offset=1` matches Strategy B's real entry - the Open of the first session
    strictly after the announcement - which is the price the live $1.00 floor tests.

    Returns a frame with one row per input event and these columns:

        panel_price     the Open the backtest sizes and filters on
        back_adjusted   True when no split jump is present at t_split
        quoted_price    panel_price / ratio when back_adjusted, else panel_price
                        (NaN when back_adjusted and no usable ratio exists)
        jump_factor     the detected jump, or NaN

    Rows whose ticker is missing from the panel, or which have too few bars, are
    returned with NaN prices rather than dropped, so a caller can count them.
    """
    try:
        available = set(prices.columns.levels[0])
    except AttributeError:
        available = set(prices.columns.get_level_values(0))

    rows = []
    for _, ev in events.iterrows():
        ticker = ev["ticker"]
        # `evaluable` guards the counts: a ticker missing from the panel never
        # reaches split_factor, and defaulting it to "not adjusted" would report it
        # as a raw series. That inflated the raw count by the number of missing
        # tickers - 151 instead of 43 - and flattered the very claim this module
        # exists to make.
        rec = dict(panel_price=np.nan, quoted_price=np.nan, evaluable=False,
                   back_adjusted=False, jump_factor=np.nan)
        if ticker in available:
            td = prices[ticker].dropna(how="all")
            if not td.empty:
                rec["evaluable"] = True
                after_ann = td[td.index >= ev["t_ann"]]
                if len(after_ann) > entry_offset:
                    panel = after_ann.iloc[entry_offset]["Open"]
                    if pd.notna(panel) and panel > 0:
                        rec["panel_price"] = float(panel)
                factor = split_factor(td, ev["t_split"], ev.get("ratio", np.nan))
                rec["jump_factor"] = np.nan if factor is None else float(factor)
                rec["back_adjusted"] = factor is None
                rec["quoted_price"] = _quoted(rec["panel_price"],
                                              ev.get("ratio", np.nan),
                                              rec["back_adjusted"])
        rows.append(rec)

    out = pd.DataFrame(rows, index=events.index)
    return pd.concat([events, out], axis=1)


def _quoted(panel_price: float, ratio, back_adjusted: bool) -> float:
    """Convert a panel price into the dollars a live quote would have shown."""
    if pd.isna(panel_price):
        return np.nan
    if not back_adjusted:
        # The jump is still in the series, so pre-split bars are already real dollars.
        return float(panel_price)
    if pd.isna(ratio) or not ratio or ratio <= 1:
        # Adjusted, but we cannot undo it without a ratio. NaN, not a guess: this
        # feeds a price filter, and a wrong price here silently moves the universe.
        return np.nan
    return float(panel_price) / float(ratio)


def summarize_basis(basis: pd.DataFrame, floor: float = 1.00) -> dict:
    """Counts a report can quote directly, for both readings of the price."""
    panel = basis["panel_price"].dropna()
    quoted = basis["quoted_price"].dropna()
    # Only rows we could actually inspect count toward the raw/adjusted split.
    ev = basis[basis["evaluable"]]
    return {
        "events": int(len(basis)),
        "evaluable": int(len(ev)),
        "priced": int(len(panel)),
        "back_adjusted": int(ev["back_adjusted"].sum()),
        "with_jump": int((~ev["back_adjusted"]).sum()),
        "panel_median": float(panel.median()) if len(panel) else float("nan"),
        "quoted_median": float(quoted.median()) if len(quoted) else float("nan"),
        "panel_above_floor": int((panel >= floor).sum()),
        "quoted_above_floor": int((quoted >= floor).sum()),
        "quoted_resolvable": int(len(quoted)),
    }
