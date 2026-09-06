"""Offline tests for the split-action price reconstruction.

No network: every fixture is a hand-built panel whose adjustment state is known,
so the tests check the classification rule rather than the provider.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from split_strategy.backtest.split_actions import (
    classify_applied,
    count_later_splits,
    quoted_entry_prices,
    quoted_factors,
)


def _panel(series: dict[str, pd.Series]) -> pd.DataFrame:
    """Build a (ticker, field) panel from per-ticker Close series."""
    frames = {}
    for t, s in series.items():
        frames[t] = pd.DataFrame({"Open": s, "High": s, "Low": s, "Close": s})
    return pd.concat(frames, axis=1)


DATES = pd.bdate_range("2025-01-01", periods=10)


def _adjusted_series():
    """A 1-for-10 on day 5 that the provider already absorbed: continuous at $2.00."""
    return pd.Series([2.0] * 10, index=DATES)


def _raw_series():
    """The same split, NOT absorbed: $0.20 before, $2.00 on and after."""
    return pd.Series([0.2] * 5 + [2.0] * 5, index=DATES)


ACTION = pd.DataFrame([dict(ticker="AAA", split_date=DATES[5], factor=0.1)])


def test_continuous_series_is_classified_applied():
    prices = _panel({"AAA": _adjusted_series()})
    out = classify_applied(prices, ACTION)
    assert out.loc[0, "verdict"] == "applied"
    assert out.loc[0, "jump"] == pytest.approx(1.0)


def test_jumping_series_is_classified_not_applied():
    prices = _panel({"AAA": _raw_series()})
    out = classify_applied(prices, ACTION)
    assert out.loc[0, "verdict"] == "not_applied"
    assert out.loc[0, "jump"] == pytest.approx(10.0)


def test_split_outside_the_panel_is_not_guessed_at():
    prices = _panel({"AAA": _adjusted_series()})
    late = pd.DataFrame([dict(ticker="AAA", split_date=pd.Timestamp("2030-01-01"),
                              factor=0.1)])
    assert classify_applied(prices, late).loc[0, "verdict"] == "outside_panel"
    missing = pd.DataFrame([dict(ticker="ZZZ", split_date=DATES[5], factor=0.1)])
    assert classify_applied(prices, missing).loc[0, "verdict"] == "outside_panel"


def test_a_real_price_move_is_not_mistaken_for_an_unapplied_split():
    """A 1-for-10 absorbed by the provider, with the stock halving on the day."""
    s = pd.Series([2.0] * 5 + [1.0] * 5, index=DATES)
    out = classify_applied(_panel({"AAA": s}), ACTION)
    assert out.loc[0, "verdict"] == "applied"


def test_quoted_factors_undo_only_absorbed_splits():
    prices = _panel({"AAA": _adjusted_series()})
    f = quoted_factors(prices, classify_applied(prices, ACTION))["AAA"]
    assert f.loc[DATES[0]] == pytest.approx(0.1)   # pre-split bar scaled back down
    assert f.loc[DATES[5]] == pytest.approx(1.0)   # post-split bar already real

    raw = _panel({"AAA": _raw_series()})
    assert "AAA" not in quoted_factors(raw, classify_applied(raw, ACTION))


def test_serial_splits_multiply():
    """Two reverse splits ahead of a bar scale it by the PRODUCT, not by one ratio."""
    s = pd.Series([50.0] * 10, index=DATES)
    prices = _panel({"AAA": s})
    actions = pd.DataFrame([
        dict(ticker="AAA", split_date=DATES[3], factor=0.1),   # 1-for-10
        dict(ticker="AAA", split_date=DATES[7], factor=0.05),  # 1-for-20
    ])
    f = quoted_factors(prices, classify_applied(prices, actions))["AAA"]
    assert f.loc[DATES[0]] == pytest.approx(0.005)             # 1/200
    assert 50.0 * f.loc[DATES[0]] == pytest.approx(0.25)
    assert f.loc[DATES[5]] == pytest.approx(0.05)              # only the later one left
    assert f.loc[DATES[8]] == pytest.approx(1.0)


def test_quoted_entry_prices_uses_the_bar_the_engine_trades():
    """entry_offset=1 is the SECOND session on or after t_ann, as backtest_mega does."""
    prices = _panel({"AAA": _adjusted_series()})
    cls = classify_applied(prices, ACTION)
    events = pd.DataFrame([dict(ticker="AAA", t_ann=DATES[0], t_split=DATES[5], ratio=10.0)])
    out = quoted_entry_prices(events, prices, quoted_factors(prices, cls),
                              entry_offset=1, classified=cls)
    assert out.loc[0, "panel_price"] == pytest.approx(2.0)
    assert out.loc[0, "quoted_price"] == pytest.approx(0.2)
    assert out.loc[0, "later_splits"] == 1


def test_quoted_entry_prices_tolerates_missing_tickers():
    prices = _panel({"AAA": _adjusted_series()})
    events = pd.DataFrame([dict(ticker="ZZZ", t_ann=DATES[0], t_split=DATES[5], ratio=10.0)])
    out = quoted_entry_prices(events, prices, {}, entry_offset=1)
    assert np.isnan(out.loc[0, "panel_price"])
    assert np.isnan(out.loc[0, "quoted_price"])


def test_count_later_splits():
    prices = _panel({"AAA": _adjusted_series()})
    cls = classify_applied(prices, ACTION)
    assert count_later_splits(cls, "AAA", DATES[0]) == 1
    assert count_later_splits(cls, "AAA", DATES[6]) == 0
    assert count_later_splits(cls, "ZZZ", DATES[0]) == 0


def test_empty_inputs_do_not_raise():
    prices = _panel({"AAA": _adjusted_series()})
    empty = pd.DataFrame(columns=["ticker", "split_date", "factor"])
    assert classify_applied(prices, empty).empty
    assert quoted_factors(prices, empty) == {}
