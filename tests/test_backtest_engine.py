"""Tests for the backtest engine — the code that produced every number we trade on.

Deliberately explicit about arithmetic: each expected return is computed by hand in the
test so a silent change in fee handling, exit selection, or sizing shows up as a failure
rather than a slightly different report.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from split_strategy.backtest.engine import (
    INF,
    backtest_mega,
    neutralize_split,
    summarize_trades,
    _holding_window,
)
from tests.conftest import make_bars, make_events, make_panel

DAYS = pd.bdate_range("2025-01-06", periods=6)  # Mon .. Mon
SLIPPAGE = 0.015


# --------------------------------------------------------------------------------------
# neutralize_split — the fix for the bug that invalidated the original research
# --------------------------------------------------------------------------------------

class TestNeutralizeSplit:
    def test_declared_ratio_removes_mechanical_jump(self):
        """A 1-for-10 split 10x's the quoted price but must leave the series continuous."""
        bars = make_bars(DAYS, open_=[1.0, 1.0, 1.0, 10.0, 10.0, 10.0],
                         close=[1.0, 1.0, 1.0, 10.0, 10.0, 10.0])
        out = neutralize_split(bars, DAYS[3], ratio=10.0)
        # Post-split prices are expressed back in pre-split terms -> no apparent move.
        assert out["Open"].tolist() == pytest.approx([1.0] * 6)
        assert out["Close"].tolist() == pytest.approx([1.0] * 6)

    def test_volume_scales_inversely(self):
        bars = make_bars(DAYS, open_=[1, 1, 1, 10, 10, 10], close=[1, 1, 1, 10, 10, 10],
                         volume=[100] * 6)
        out = neutralize_split(bars, DAYS[3], ratio=10.0)
        assert out["Volume"].iloc[:3].tolist() == pytest.approx([100] * 3)
        assert out["Volume"].iloc[3:].tolist() == pytest.approx([1000] * 3)

    def test_observed_jump_fallback_when_ratio_missing(self):
        """No declared ratio, but an unmistakable jump (>=1.8x) -> use the observed factor."""
        bars = make_bars(DAYS, open_=[1, 1, 1, 5, 5, 5], close=[1, 1, 1, 5, 5, 5])
        out = neutralize_split(bars, DAYS[3], ratio=np.nan)
        assert out["Open"].tolist() == pytest.approx([1.0] * 6)

    def test_small_jump_without_ratio_is_left_alone(self):
        """A 1.5x move is a real price move, not a split artifact - must not be 'fixed'."""
        bars = make_bars(DAYS, open_=[1, 1, 1, 1.5, 1.5, 1.5], close=[1, 1, 1, 1.5, 1.5, 1.5])
        out = neutralize_split(bars, DAYS[3], ratio=np.nan)
        assert out["Open"].tolist() == pytest.approx([1, 1, 1, 1.5, 1.5, 1.5])

    def test_already_adjusted_series_is_not_double_adjusted(self):
        """Guard against the opposite error: data that already has the split removed."""
        bars = make_bars(DAYS, open_=1.0, close=1.0)
        out = neutralize_split(bars, DAYS[3], ratio=10.0)
        assert out["Open"].tolist() == pytest.approx([1.0] * 6)

    def test_does_not_mutate_input(self):
        bars = make_bars(DAYS, open_=[1, 1, 1, 10, 10, 10], close=[1, 1, 1, 10, 10, 10])
        before = bars.copy(deep=True)
        neutralize_split(bars, DAYS[3], ratio=10.0)
        pd.testing.assert_frame_equal(bars, before)

    @pytest.mark.parametrize("t_split", [pd.NaT, DAYS[0], DAYS[-1] + pd.Timedelta(days=30)])
    def test_returns_input_when_split_outside_range(self, t_split):
        """No bars before or after the split date -> nothing to anchor an adjustment on."""
        bars = make_bars(DAYS, open_=1.0, close=1.0)
        out = neutralize_split(bars, t_split, ratio=10.0)
        pd.testing.assert_frame_equal(out, bars)


# --------------------------------------------------------------------------------------
# _holding_window — which bars a trade is allowed to exit on
# --------------------------------------------------------------------------------------

class TestHoldingWindow:
    @pytest.fixture
    def future(self):
        return make_bars(DAYS, open_=1.0, close=1.0)

    def test_integer_rule_takes_next_n_bars(self, future):
        out = _holding_window(future, DAYS[0], DAYS[4], hold_rule=2)
        assert out.index.tolist() == [DAYS[1], DAYS[2]]

    def test_day_before_split_excludes_split_day(self, future):
        out = _holding_window(future, DAYS[0], DAYS[3], hold_rule="day_before_split")
        assert out.index.tolist() == [DAYS[1], DAYS[2]]

    def test_day_of_split_includes_split_day(self, future):
        out = _holding_window(future, DAYS[0], DAYS[3], hold_rule="day_of_split")
        assert out.index.tolist() == [DAYS[1], DAYS[2], DAYS[3]]

    def test_day_after_split_extends_one_bar_past(self, future):
        out = _holding_window(future, DAYS[0], DAYS[3], hold_rule="day_after_split")
        assert out.index.tolist() == [DAYS[1], DAYS[2], DAYS[3], DAYS[4]]

    def test_n_days_after_split(self, future):
        out = _holding_window(future, DAYS[0], DAYS[1], hold_rule="5_days_after_split")
        # Only 5 bars exist at/after t_split, so it falls back to everything after entry.
        assert out.index.tolist() == DAYS[1:].tolist()

    def test_unknown_rule_returns_none(self, future):
        assert _holding_window(future, DAYS[0], DAYS[3], hold_rule="nonsense") is None


# --------------------------------------------------------------------------------------
# backtest_mega — entry, exit selection, filters, sizing
# --------------------------------------------------------------------------------------

def _single_trade_setup(opens, highs=None, lows=None, closes=None, ratio=np.nan):
    """One ticker, announced on day0, split on day3."""
    bars = make_bars(DAYS, open_=opens, high=highs, low=lows, close=closes)
    prices = make_panel({"ABC": bars})
    events = make_events([("ABC", DAYS[0], DAYS[3], ratio)])
    return events, prices


class TestBacktestMegaMechanics:
    def test_time_exit_return_is_exact(self):
        """Entry at day1 open (1.00), cover at day3 open (0.80) => +20% gross, -1.5% fees."""
        events, prices = _single_trade_setup(opens=[1.0, 1.0, 0.9, 0.8, 0.8, 0.8])
        trades = backtest_mega(events, prices, hold_rule="day_of_split", entry_offset=1)

        assert len(trades) == 1
        t = trades.iloc[0]
        assert t["entry_date"] == DAYS[1]
        assert t["entry_price"] == pytest.approx(1.0)
        assert t["exit_date"] == DAYS[3]
        assert t["exit_price"] == pytest.approx(0.8)
        assert t["exit_reason"] == "time_exit"
        assert t["net_return"] == pytest.approx(0.20 - SLIPPAGE)
        # 5% of 10_000 at +18.5%
        assert t["pnl"] == pytest.approx(10_000 * 0.05 * (0.20 - SLIPPAGE))
        assert t["portfolio_value"] == pytest.approx(10_000 + 10_000 * 0.05 * 0.185)

    def test_entry_offset_zero_uses_announcement_day(self):
        """entry_offset=0 is the original (look-ahead) behavior - still reachable."""
        events, prices = _single_trade_setup(opens=[2.0, 1.0, 1.0, 1.0, 1.0, 1.0])
        trades = backtest_mega(events, prices, hold_rule="day_of_split", entry_offset=0)
        assert trades.iloc[0]["entry_date"] == DAYS[0]
        assert trades.iloc[0]["entry_price"] == pytest.approx(2.0)

    def test_stop_loss_fires_on_intrabar_high(self):
        events, prices = _single_trade_setup(
            opens=[1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
            highs=[1.0, 1.0, 1.5, 1.0, 1.0, 1.0],  # day2 spikes through a 40% stop
        )
        trades = backtest_mega(events, prices, hold_rule="day_of_split",
                               stop_loss=0.40, entry_offset=1)
        t = trades.iloc[0]
        assert t["exit_reason"] == "stop_loss"
        assert t["exit_date"] == DAYS[2]
        assert t["exit_price"] == pytest.approx(1.40)
        assert t["net_return"] == pytest.approx(-0.40 - SLIPPAGE)

    def test_take_profit_fires_on_intrabar_low(self):
        events, prices = _single_trade_setup(
            opens=[1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
            lows=[1.0, 1.0, 0.7, 1.0, 1.0, 1.0],  # day2 dips through a 20% take-profit
        )
        trades = backtest_mega(events, prices, hold_rule="day_of_split",
                               take_profit=0.20, entry_offset=1)
        t = trades.iloc[0]
        assert t["exit_reason"] == "take_profit"
        assert t["exit_date"] == DAYS[2]
        assert t["exit_price"] == pytest.approx(0.80)
        assert t["net_return"] == pytest.approx(0.20 - SLIPPAGE)

    def test_stop_loss_wins_when_both_trigger_same_bar(self):
        """Pins a real ordering decision: the stop is checked before the take-profit."""
        events, prices = _single_trade_setup(
            opens=[1.0] * 6,
            highs=[1.0, 1.0, 1.5, 1.0, 1.0, 1.0],
            lows=[1.0, 1.0, 0.7, 1.0, 1.0, 1.0],
        )
        trades = backtest_mega(events, prices, hold_rule="day_of_split",
                               stop_loss=0.40, take_profit=0.20, entry_offset=1)
        assert trades.iloc[0]["exit_reason"] == "stop_loss"

    def test_infinite_stop_and_tp_never_trigger(self):
        """Strategy B runs with no stop; make sure INF really means 'never'."""
        events, prices = _single_trade_setup(
            opens=[1.0, 1.0, 1.0, 0.5, 0.5, 0.5],
            highs=[1.0, 1.0, 99.0, 0.5, 0.5, 0.5],
            lows=[1.0, 1.0, 0.01, 0.5, 0.5, 0.5],
        )
        trades = backtest_mega(events, prices, hold_rule="day_of_split",
                               stop_loss=INF, take_profit=INF, entry_offset=1)
        assert trades.iloc[0]["exit_reason"] == "time_exit"


class TestBacktestMegaFilters:
    def test_gap_up_filter_skips_trade(self):
        """Entry opens 50% above the prior close; a 30% gap filter must reject it."""
        events, prices = _single_trade_setup(
            opens=[1.0, 1.5, 1.5, 1.5, 1.5, 1.5],
            closes=[1.0, 1.5, 1.5, 1.5, 1.5, 1.5],
        )
        trades = backtest_mega(events, prices, hold_rule="day_of_split",
                               max_gap_up=0.30, entry_offset=1)
        assert trades.empty

    def test_gap_up_within_limit_is_kept(self):
        events, prices = _single_trade_setup(
            opens=[1.0, 1.2, 1.2, 1.0, 1.0, 1.0],
            closes=[1.0, 1.2, 1.2, 1.0, 1.0, 1.0],
        )
        trades = backtest_mega(events, prices, hold_rule="day_of_split",
                               max_gap_up=0.30, entry_offset=1)
        assert len(trades) == 1

    def test_ratio_filter_inactive_by_default_keeps_nan_ratio(self):
        """Default (0, inf) must not silently drop events with an unparseable ratio."""
        events, prices = _single_trade_setup(opens=[1.0] * 6, ratio=np.nan)
        trades = backtest_mega(events, prices, hold_rule="day_of_split", entry_offset=1)
        assert len(trades) == 1

    def test_ratio_bucket_excludes_out_of_range(self):
        events, prices = _single_trade_setup(opens=[1.0] * 6, ratio=5.0)
        trades = backtest_mega(events, prices, hold_rule="day_of_split",
                               min_ratio=10, max_ratio=50, entry_offset=1)
        assert trades.empty

    def test_ratio_bucket_includes_in_range(self):
        events, prices = _single_trade_setup(opens=[1.0] * 6, ratio=25.0)
        trades = backtest_mega(events, prices, hold_rule="day_of_split",
                               min_ratio=10, max_ratio=50, entry_offset=1)
        assert len(trades) == 1

    def test_ticker_missing_from_price_panel_is_skipped(self):
        events = make_events([("MISSING", DAYS[0], DAYS[3], np.nan)])
        prices = make_panel({"ABC": make_bars(DAYS, open_=1.0)})
        assert backtest_mega(events, prices, entry_offset=1).empty

    def test_split_adjustment_changes_outcome(self):
        """The regression guard for the original bug: with the raw 10x jump left in,
        a 40% stop fires; with it neutralized, the trade is a normal winner."""
        events, prices = _single_trade_setup(
            opens=[1.0, 1.0, 1.0, 10.0, 10.0, 10.0],
            highs=[1.0, 1.0, 1.0, 10.0, 10.0, 10.0],
            closes=[1.0, 1.0, 1.0, 10.0, 10.0, 10.0],
            ratio=10.0,
        )
        buggy = backtest_mega(events, prices, hold_rule="day_of_split", stop_loss=0.40,
                              entry_offset=1, adjust_for_split=False)
        fixed = backtest_mega(events, prices, hold_rule="day_of_split", stop_loss=0.40,
                              entry_offset=1, adjust_for_split=True)
        assert buggy.iloc[0]["exit_reason"] == "stop_loss"
        assert fixed.iloc[0]["exit_reason"] == "time_exit"
        assert fixed.iloc[0]["net_return"] > buggy.iloc[0]["net_return"]


class TestBacktestMegaSizing:
    def test_position_size_compounds_off_portfolio_value(self):
        """Second trade must be sized off the post-first-trade balance."""
        bars = make_bars(DAYS, open_=[1.0, 1.0, 0.5, 0.5, 0.5, 0.5])
        prices = make_panel({"AAA": bars, "BBB": bars})
        events = make_events([
            ("AAA", DAYS[0], DAYS[3], np.nan),
            ("BBB", DAYS[0], DAYS[3], np.nan),
        ])
        trades = backtest_mega(events, prices, hold_rule="day_of_split",
                               entry_offset=1, trade_pct=0.05)
        assert len(trades) == 2
        first_value = trades.iloc[0]["portfolio_value"]
        # Second bet is 5% of the *grown* balance, not of the original 10k.
        expected_second_pnl = first_value * 0.05 * trades.iloc[1]["net_return"]
        assert trades.iloc[1]["pnl"] == pytest.approx(expected_second_pnl)

    def test_trade_pct_scales_pnl_linearly(self):
        events, prices = _single_trade_setup(opens=[1.0, 1.0, 0.8, 0.8, 0.8, 0.8])
        small = backtest_mega(events, prices, hold_rule="day_of_split",
                              entry_offset=1, trade_pct=0.02)
        big = backtest_mega(events, prices, hold_rule="day_of_split",
                            entry_offset=1, trade_pct=0.05)
        assert big.iloc[0]["pnl"] == pytest.approx(small.iloc[0]["pnl"] * 2.5)

    def test_empty_events_returns_empty_frame(self):
        prices = make_panel({"ABC": make_bars(DAYS, open_=1.0)})
        assert backtest_mega(make_events([]), prices).empty


# --------------------------------------------------------------------------------------
# summarize_trades
# --------------------------------------------------------------------------------------

class TestSummarizeTrades:
    def test_empty_trades(self):
        s = summarize_trades(pd.DataFrame())
        assert s["total_trades"] == 0
        assert s["final_value"] == 10_000
        assert np.isnan(s["win_rate"])

    def test_metrics_and_drawdown(self):
        trades = pd.DataFrame({
            "net_return": [0.10, -0.20],
            "portfolio_value": [11_000.0, 9_000.0],
        })
        s = summarize_trades(trades, initial_capital=10_000)
        assert s["total_trades"] == 2
        assert s["win_rate"] == pytest.approx(50.0)
        assert s["total_return_pct"] == pytest.approx(-10.0)
        # Peak 11k -> trough 9k
        assert s["max_drawdown_pct"] == pytest.approx((9_000 - 11_000) / 11_000 * 100)

    def test_drawdown_is_zero_for_monotonic_gains(self):
        trades = pd.DataFrame({
            "net_return": [0.1, 0.1],
            "portfolio_value": [11_000.0, 12_000.0],
        })
        assert summarize_trades(trades)["max_drawdown_pct"] == pytest.approx(0.0)
