"""Tests for the optimized grid search.

`gridsearch.run_grid` reimplements the engine's inner loop in vectorized form for speed
(~400x). That's only safe if it stays numerically identical to the reference
implementation — so the central test here is an equivalence property, formalizing what
`scripts/verify_gridsearch.py` checks ad hoc.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from split_strategy.backtest.engine import INF, backtest_mega, summarize_trades
from split_strategy.backtest.gridsearch import (
    GAP_UPS,
    HOLD_RULES,
    RATIO_BUCKETS,
    STOP_LOSSES,
    TAKE_PROFITS,
    run_grid,
)
from tests.conftest import make_bars, make_events, make_panel

DAYS = pd.bdate_range("2025-01-06", periods=12)


@pytest.fixture
def scenario():
    """Three tickers with distinct shapes so different exits get exercised."""
    rng = np.random.default_rng(7)
    frames = {}
    for i, tkr in enumerate(["AAA", "BBB", "CCC"]):
        drift = np.linspace(1.0, 0.6 + 0.2 * i, len(DAYS))
        noise = rng.normal(0, 0.03, len(DAYS))
        opens = np.clip(drift + noise, 0.05, None)
        frames[tkr] = make_bars(
            DAYS, open_=opens, high=opens * 1.15, low=opens * 0.85, close=opens
        )
    prices = make_panel(frames)
    events = make_events([
        ("AAA", DAYS[0], DAYS[5], 10.0),
        ("BBB", DAYS[1], DAYS[7], 25.0),
        ("CCC", DAYS[2], DAYS[9], 60.0),
    ])
    return events, prices


class TestGridMatchesEngine:
    """The equivalence property: a one-cell grid must equal a direct engine run."""

    @pytest.mark.parametrize("hold_rule", ["day_of_split", "day_before_split", 5])
    @pytest.mark.parametrize("stop_loss", [0.40, INF])
    @pytest.mark.parametrize("take_profit", [0.20, INF])
    def test_single_permutation_equals_backtest_mega(
        self, scenario, hold_rule, stop_loss, take_profit
    ):
        events, prices = scenario
        params = dict(
            hold_rule=hold_rule, stop_loss=stop_loss, take_profit=take_profit,
            max_gap_up=INF, min_ratio=0, max_ratio=INF,
        )

        reference = summarize_trades(
            backtest_mega(events, prices, entry_offset=1, adjust_for_split=True, **params)
        )
        grid = run_grid(
            events, prices,
            hold_rules=[hold_rule], stop_losses=[stop_loss], take_profits=[take_profit],
            gap_ups=[INF], ratio_buckets=[(0, INF)],
            entry_offset=1, adjust_for_split=True,
        )

        if reference["total_trades"] == 0:
            assert grid.empty or grid.iloc[0]["total_trades"] == 0
            return

        row = grid.iloc[0]
        assert row["total_trades"] == reference["total_trades"]
        assert row["win_rate"] == pytest.approx(reference["win_rate"])
        assert row["total_return_pct"] == pytest.approx(reference["total_return_pct"])
        assert row["max_drawdown_pct"] == pytest.approx(reference["max_drawdown_pct"])

    def test_ratio_bucket_matches_engine(self, scenario):
        events, prices = scenario
        params = dict(hold_rule="day_of_split", stop_loss=INF, take_profit=INF,
                      max_gap_up=INF, min_ratio=10, max_ratio=50)
        reference = summarize_trades(backtest_mega(events, prices, entry_offset=1, **params))
        grid = run_grid(events, prices, hold_rules=["day_of_split"], stop_losses=[INF],
                        take_profits=[INF], gap_ups=[INF], ratio_buckets=[(10, 50)],
                        entry_offset=1)
        assert grid.iloc[0]["total_trades"] == reference["total_trades"]


class TestGridShape:
    def test_full_grid_is_4620_permutations(self):
        """The published headline number - guard against silently changing the space."""
        assert (len(HOLD_RULES) * len(STOP_LOSSES) * len(TAKE_PROFITS)
                * len(GAP_UPS) * len(RATIO_BUCKETS)) == 4620

    def test_row_count_matches_requested_grid(self, scenario):
        events, prices = scenario
        grid = run_grid(events, prices, hold_rules=["day_of_split", 5],
                        stop_losses=[0.40, INF], take_profits=[INF],
                        gap_ups=[INF], ratio_buckets=[(0, INF)], entry_offset=1)
        assert len(grid) == 4

    def test_min_trades_filters_thin_permutations(self, scenario):
        events, prices = scenario
        grid = run_grid(events, prices, hold_rules=["day_of_split"], stop_losses=[INF],
                        take_profits=[INF], gap_ups=[INF], ratio_buckets=[(0, INF)],
                        entry_offset=1, min_trades=99)
        assert grid.empty

    def test_empty_events_returns_empty(self):
        prices = make_panel({"AAA": make_bars(DAYS, open_=1.0)})
        assert run_grid(make_events([]), prices).empty


class TestTStatVarianceFloor:
    """A few trades exiting at a fixed take-profit have near-zero variance, which would
    blow the t-stat toward infinity. The floor keeps that artifact out of selection."""

    def test_identical_returns_do_not_produce_infinite_tstat(self):
        # Every trade exits at exactly the same take-profit level -> ~zero variance.
        frames, rows = {}, []
        for i, tkr in enumerate(["T1", "T2", "T3", "T4"]):
            opens = np.full(len(DAYS), 1.0)
            lows = np.full(len(DAYS), 1.0)
            lows[3:] = 0.5  # deep enough to trigger a 20% take-profit
            frames[tkr] = make_bars(DAYS, open_=opens, high=opens, low=lows, close=opens)
            rows.append((tkr, DAYS[0], DAYS[8], 10.0))

        grid = run_grid(make_events(rows), make_panel(frames),
                        hold_rules=["day_of_split"], stop_losses=[INF],
                        take_profits=[0.20], gap_ups=[INF], ratio_buckets=[(0, INF)],
                        entry_offset=1)

        t_stat = grid.iloc[0]["t_stat"]
        assert np.isfinite(t_stat)
        # With a 2% std floor and n=4: 0.185 / (0.02/2) = 18.5
        assert abs(t_stat) < 100
