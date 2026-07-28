"""Tests for Corwin-Schultz spread estimation.

We have no historical bid/ask data, so the spread is inferred from daily high/low
ranges. These tests check the estimator behaves sensibly in the directions we rely on:
wider intraday ranges imply wider spreads, and degenerate inputs return NaN rather than
a confidently wrong number.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from split_strategy.backtest.slippage import (
    MAX_PLAUSIBLE_SPREAD,
    corwin_schultz_spread,
    estimate_spread,
    round_trip_cost,
)
from tests.conftest import make_bars

DAYS = pd.bdate_range("2025-01-06", periods=40)


def bars_with_range(range_pct: float, price: float = 1.0):
    """A series where each day's high/low straddle `price` by range_pct."""
    high = price * (1 + range_pct / 2)
    low = price * (1 - range_pct / 2)
    return make_bars(DAYS, open_=price, high=high, low=low, close=price)


class TestCorwinSchultz:
    def test_returns_series_aligned_to_input(self):
        bars = bars_with_range(0.02)
        out = corwin_schultz_spread(bars["High"], bars["Low"])
        assert len(out) == len(bars)
        assert out.index.equals(bars.index)

    def test_first_observation_is_nan(self):
        """The estimator needs consecutive-day pairs."""
        bars = bars_with_range(0.02)
        assert pd.isna(corwin_schultz_spread(bars["High"], bars["Low"]).iloc[0])

    def test_never_returns_negative(self):
        """Quiet days can produce negative raw estimates; those floor at zero."""
        bars = bars_with_range(0.0001)
        out = corwin_schultz_spread(bars["High"], bars["Low"]).dropna()
        assert (out >= 0).all()

    def test_wider_ranges_imply_wider_spreads(self):
        narrow = bars_with_range(0.01)
        wide = bars_with_range(0.20)
        narrow_est = corwin_schultz_spread(narrow["High"], narrow["Low"]).dropna().median()
        wide_est = corwin_schultz_spread(wide["High"], wide["Low"]).dropna().median()
        assert wide_est > narrow_est

    def test_handles_zero_and_inverted_prices(self):
        bars = make_bars(DAYS, open_=1.0, high=[0.0] * 40, low=[1.0] * 40, close=1.0)
        out = corwin_schultz_spread(bars["High"], bars["Low"])
        assert out.dropna().empty or (out.dropna() >= 0).all()


class TestEstimateSpread:
    def test_uses_only_bars_before_entry(self):
        """A post-entry volatility spike must not leak into the entry-time estimate."""
        highs = [1.01] * 40
        lows = [0.99] * 40
        highs[30:] = [5.0] * 10   # violent moves only AFTER the entry date
        lows[30:] = [0.1] * 10
        bars = make_bars(DAYS, open_=1.0, high=highs, low=lows, close=1.0)
        quiet = estimate_spread(bars, DAYS[25], lookback=20)
        assert quiet < 0.10

    def test_returns_nan_without_enough_history(self):
        bars = make_bars(DAYS[:2], open_=1.0, high=1.01, low=0.99, close=1.0)
        assert np.isnan(estimate_spread(bars, DAYS[1]))

    def test_returns_nan_on_empty_frame(self):
        assert np.isnan(estimate_spread(pd.DataFrame(), DAYS[10]))

    def test_returns_nan_when_columns_missing(self):
        bars = pd.DataFrame({"Close": [1.0] * 5}, index=DAYS[:5])
        assert np.isnan(estimate_spread(bars, DAYS[4]))

    def test_implausible_estimates_are_discarded(self):
        """Halts and gaps produce absurd values that would otherwise dominate."""
        bars = bars_with_range(1.9)  # ~190% daily range
        out = estimate_spread(bars, DAYS[-1])
        assert np.isnan(out) or out <= MAX_PLAUSIBLE_SPREAD

    def test_penny_stock_spread_exceeds_flat_assumption(self):
        """The motivating case: a wide-ranging sub-$1 name costs far more than 1.5%."""
        bars = bars_with_range(0.12, price=0.09)
        assert estimate_spread(bars, DAYS[-1]) > 0.015


class TestRoundTripCost:
    def test_adds_one_full_spread(self):
        assert round_trip_cost(0.04, base_cost=0.001) == pytest.approx(0.041)

    def test_nan_spread_falls_back_to_base(self):
        assert round_trip_cost(float("nan"), base_cost=0.015) == pytest.approx(0.015)


class TestEngineIntegration:
    """The spread veto must actually drop trades in the engine."""

    def test_wide_spread_trade_is_skipped(self):
        from split_strategy.backtest.engine import backtest_mega
        from tests.conftest import make_events, make_panel

        bars = bars_with_range(0.30, price=0.10)   # very wide ranges
        prices = make_panel({"WIDE": bars})
        events = make_events([("WIDE", DAYS[25], DAYS[30], np.nan)])

        unfiltered = backtest_mega(events, prices, hold_rule="day_of_split", entry_offset=1)
        filtered = backtest_mega(events, prices, hold_rule="day_of_split", entry_offset=1,
                                 max_spread_pct=0.01)
        assert len(unfiltered) == 1
        assert filtered.empty

    def test_flat_model_is_unchanged_by_default(self):
        """Default behavior must stay byte-identical so published results reproduce."""
        from split_strategy.backtest.engine import backtest_mega
        from tests.conftest import make_events, make_panel

        bars = make_bars(DAYS, open_=1.0, high=1.02, low=0.98, close=1.0)
        prices = make_panel({"ABC": bars})
        events = make_events([("ABC", DAYS[25], DAYS[30], np.nan)])
        trades = backtest_mega(events, prices, hold_rule="day_of_split", entry_offset=1)
        # Flat 1.5% deducted, price flat -> exactly -1.5%.
        assert trades.iloc[0]["net_return"] == pytest.approx(-0.015)
