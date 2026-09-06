"""Which dollars a backtest price is denominated in.

This matters because the live $1.00 floor and the backtest's price filter read
different numbers off the same event, and the gap is the split ratio. A mistake here
does not crash anything - it silently moves the tradeable universe, which is how the
floor came to reject 100% of live candidates while the analysis expected a quarter.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from split_strategy.backtest.price_basis import entry_basis, summarize_basis
from tests.conftest import make_bars, make_events, make_panel

DAYS = pd.bdate_range("2025-01-06", periods=6)


def _panel(opens, ticker="ABC"):
    return make_panel({ticker: make_bars(DAYS, open_=opens)})


class TestEntryBasis:
    def test_raw_series_needs_no_conversion(self):
        """A visible 10x jump means pre-split bars are already real dollars."""
        prices = _panel([1.0, 1.0, 1.0, 10.0, 10.0, 10.0])
        events = make_events([("ABC", DAYS[0], DAYS[3], 10.0)])
        b = entry_basis(events, prices, entry_offset=1).iloc[0]
        assert b["back_adjusted"] is np.False_ or b["back_adjusted"] is False
        assert b["panel_price"] == 1.0
        assert b["quoted_price"] == 1.0        # unchanged
        assert b["jump_factor"] == 10.0

    def test_adjusted_series_divides_by_the_ratio(self):
        """No jump: the provider back-adjusted, so pre-split bars are inflated Nx."""
        prices = _panel([8.0, 8.0, 8.0, 8.0, 8.0, 8.0])
        events = make_events([("ABC", DAYS[0], DAYS[3], 8.0)])
        b = entry_basis(events, prices, entry_offset=1).iloc[0]
        assert b["back_adjusted"]
        assert b["panel_price"] == 8.0
        assert b["quoted_price"] == 1.0        # what a live quote would have shown

    def test_adjusted_without_a_ratio_is_unknown_not_guessed(self):
        """A wrong price here silently changes which trades are eligible."""
        prices = _panel([8.0] * 6)
        events = make_events([("ABC", DAYS[0], DAYS[3], np.nan)])
        b = entry_basis(events, prices, entry_offset=1).iloc[0]
        assert b["back_adjusted"]
        assert np.isnan(b["quoted_price"])

    def test_entry_offset_selects_the_session_after_the_announcement(self):
        prices = _panel([5.0, 2.0, 2.0, 2.0, 2.0, 2.0])
        events = make_events([("ABC", DAYS[0], DAYS[3], np.nan)])
        assert entry_basis(events, prices, entry_offset=0).iloc[0]["panel_price"] == 5.0
        assert entry_basis(events, prices, entry_offset=1).iloc[0]["panel_price"] == 2.0

    def test_ticker_missing_from_the_panel_is_not_evaluable(self):
        """The bug this column exists for.

        A missing ticker never reaches split_factor. Defaulting it to "not adjusted"
        counted it as a raw series and inflated the raw count from 43 to 151 -
        flattering the exact claim the module is used to make.
        """
        prices = _panel([1.0] * 6)
        events = make_events([("NOPE", DAYS[0], DAYS[3], 10.0)])
        b = entry_basis(events, prices).iloc[0]
        assert not b["evaluable"]
        assert np.isnan(b["panel_price"])

    def test_row_count_and_order_are_preserved(self):
        prices = make_panel({"ABC": make_bars(DAYS, open_=1.0),
                             "DEF": make_bars(DAYS, open_=2.0)})
        events = make_events([("ABC", DAYS[0], DAYS[3], 10.0),
                              ("NOPE", DAYS[0], DAYS[3], 10.0),
                              ("DEF", DAYS[0], DAYS[3], 10.0)])
        out = entry_basis(events, prices)
        assert list(out["ticker"]) == ["ABC", "NOPE", "DEF"]
        assert len(out) == 3


class TestSummarizeBasis:
    def test_counts_exclude_unevaluable_rows(self):
        prices = make_panel({"RAW": make_bars(DAYS, open_=[1, 1, 1, 10, 10, 10]),
                             "ADJ": make_bars(DAYS, open_=8.0)})
        events = make_events([("RAW", DAYS[0], DAYS[3], 10.0),
                              ("ADJ", DAYS[0], DAYS[3], 8.0),
                              ("NOPE", DAYS[0], DAYS[3], 5.0)])
        s = summarize_basis(entry_basis(events, prices))
        assert s["events"] == 3
        assert s["evaluable"] == 2
        assert s["with_jump"] == 1          # RAW only; NOPE must not count
        assert s["back_adjusted"] == 1      # ADJ

    def test_floor_counts_differ_between_the_two_price_bases(self):
        """The headline of the finding, in miniature.

        The same event is above the floor on panel prices and below it on quoted
        prices. That difference is the whole reason the live filter and the
        backtest disagree about which trades exist.
        """
        prices = make_panel({"ADJ": make_bars(DAYS, open_=8.0)})
        events = make_events([("ADJ", DAYS[0], DAYS[3], 20.0)])
        s = summarize_basis(entry_basis(events, prices), floor=1.00)
        assert s["panel_above_floor"] == 1     # $8.00 in the panel
        assert s["quoted_above_floor"] == 0    # $0.40 in real dollars
