"""Tests for walk-forward validation and the capital-constrained equity replay.

`compounded_oos_curve` is the function that turns a pile of overlapping trades into the
headline equity number. Its two subtle rules — closes settle before opens on the same
date, and a position that doesn't fit under the exposure cap is *skipped* rather than
deferred — are exactly the kind of logic that regresses silently, so both are pinned.
"""
from __future__ import annotations

import pandas as pd
import pytest

from split_strategy.backtest.walkforward import (
    compounded_oos_curve,
    make_folds,
    pooled_oos_trades,
)
from tests.conftest import make_events


def pooled(rows):
    """Build a pooled-trades frame from (ticker, entry, exit, net_return) tuples."""
    return pd.DataFrame([
        {"ticker": t, "entry_date": pd.Timestamp(e), "exit_date": pd.Timestamp(x),
         "net_return": r}
        for t, e, x, r in rows
    ])


class TestMakeFolds:
    def test_no_folds_when_below_minimum_events(self):
        events = make_events([("A", f"2025-01-{d:02d}", "2025-03-01", 10.0)
                              for d in range(1, 6)])
        assert make_folds(events, min_initial_events=50) == []

    def test_folds_are_contiguous_and_non_overlapping(self):
        rows = [("A", ts, ts + pd.Timedelta(days=30), 10.0)
                for ts in pd.date_range("2025-01-01", periods=200, freq="D")]
        folds = make_folds(make_events(rows), test_window_days=60, min_initial_events=50)
        assert len(folds) >= 2
        for (_, prev_end), (next_start, _) in zip(folds, folds[1:]):
            assert prev_end == next_start

    def test_window_length_is_respected(self):
        rows = [("A", ts, ts + pd.Timedelta(days=30), 10.0)
                for ts in pd.date_range("2025-01-01", periods=200, freq="D")]
        folds = make_folds(make_events(rows), test_window_days=60, min_initial_events=50)
        start, end = folds[0]
        assert (end - start).days == 60


class TestPooledOosTrades:
    def test_concatenates_and_sorts_by_entry(self):
        fold_results = [
            {"oos_trades": pooled([("B", "2025-02-01", "2025-02-05", 0.1)])},
            {"oos_trades": pooled([("A", "2025-01-01", "2025-01-05", 0.2)])},
        ]
        out = pooled_oos_trades(fold_results)
        assert out["ticker"].tolist() == ["A", "B"]

    def test_ignores_empty_folds(self):
        fold_results = [
            {"oos_trades": pd.DataFrame()},
            {"oos_trades": pooled([("A", "2025-01-01", "2025-01-05", 0.2)])},
        ]
        assert len(pooled_oos_trades(fold_results)) == 1

    def test_all_empty_returns_empty(self):
        assert pooled_oos_trades([{"oos_trades": pd.DataFrame()}]).empty


class TestCompoundedOosCurve:
    def test_single_trade_applies_pnl_at_close(self):
        curve = compounded_oos_curve(
            pooled([("A", "2025-01-06", "2025-01-10", 0.20)]),
            initial_capital=10_000, trade_pct=0.05,
        )
        assert len(curve) == 1
        # 5% of 10k at +20%
        assert curve.iloc[0]["pnl"] == pytest.approx(100.0)
        assert curve.iloc[0]["portfolio_value"] == pytest.approx(10_100.0)

    def test_sequential_trades_compound(self):
        curve = compounded_oos_curve(
            pooled([
                ("A", "2025-01-06", "2025-01-10", 0.20),
                ("B", "2025-01-13", "2025-01-17", 0.20),
            ]),
            initial_capital=10_000, trade_pct=0.05,
        )
        # Second bet is 5% of 10,100, not of 10,000.
        assert curve.iloc[1]["pnl"] == pytest.approx(10_100 * 0.05 * 0.20)

    def test_exposure_cap_skips_trades_that_do_not_fit(self):
        """Cap of 10% with 5% bets leaves room for exactly two concurrent positions."""
        trades = pooled([
            ("A", "2025-01-06", "2025-01-31", 0.10),
            ("B", "2025-01-06", "2025-01-31", 0.10),
            ("C", "2025-01-06", "2025-01-31", 0.10),
        ])
        curve = compounded_oos_curve(trades, initial_capital=10_000,
                                     trade_pct=0.05, max_exposure=0.10)
        assert curve.attrs["n_taken"] == 2
        assert curve.attrs["n_skipped"] == 1
        assert len(curve) == 2

    def test_unconstrained_takes_everything(self):
        trades = pooled([
            ("A", "2025-01-06", "2025-01-31", 0.10),
            ("B", "2025-01-06", "2025-01-31", 0.10),
            ("C", "2025-01-06", "2025-01-31", 0.10),
        ])
        curve = compounded_oos_curve(trades, max_exposure=float("inf"))
        assert curve.attrs["n_taken"] == 3
        assert curve.attrs["n_skipped"] == 0

    def test_capital_frees_up_after_a_close(self):
        """A closes before B opens, so B fits under a cap that only allows one position."""
        trades = pooled([
            ("A", "2025-01-06", "2025-01-10", 0.10),
            ("B", "2025-01-13", "2025-01-17", 0.10),
        ])
        curve = compounded_oos_curve(trades, initial_capital=10_000,
                                     trade_pct=0.05, max_exposure=0.05)
        assert curve.attrs["n_taken"] == 2
        assert curve.attrs["n_skipped"] == 0

    def test_close_settles_before_open_on_same_date(self):
        """A closing on the same day B opens must free its capital first, or B is
        wrongly skipped under a tight cap."""
        trades = pooled([
            ("A", "2025-01-06", "2025-01-10", 0.10),
            ("B", "2025-01-10", "2025-01-15", 0.10),
        ])
        curve = compounded_oos_curve(trades, initial_capital=10_000,
                                     trade_pct=0.05, max_exposure=0.05)
        assert curve.attrs["n_taken"] == 2, "same-day close must release capital first"

    def test_rows_are_in_close_order(self):
        trades = pooled([
            ("SLOW", "2025-01-06", "2025-02-28", 0.10),
            ("FAST", "2025-01-07", "2025-01-08", 0.10),
        ])
        curve = compounded_oos_curve(trades, max_exposure=float("inf"))
        assert curve["ticker"].tolist() == ["FAST", "SLOW"]

    def test_empty_input_returns_empty(self):
        assert compounded_oos_curve(pd.DataFrame()).empty

    def test_losses_reduce_subsequent_bet_size(self):
        curve = compounded_oos_curve(
            pooled([
                ("A", "2025-01-06", "2025-01-10", -0.50),
                ("B", "2025-01-13", "2025-01-17", 0.10),
            ]),
            initial_capital=10_000, trade_pct=0.05,
        )
        value_after_loss = curve.iloc[0]["portfolio_value"]
        assert value_after_loss == pytest.approx(9_750.0)
        assert curve.iloc[1]["pnl"] == pytest.approx(9_750 * 0.05 * 0.10)
