"""Tests for the shortability proxy classifier.

This proxy decides which historical trades were realistically tradeable. Live Schwab
data has already contradicted it on sub-$1 names, so these tests pin *current* behavior
rather than asserting it is correct — they exist so a deliberate recalibration shows up
as an intentional change, not an accident.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from split_strategy.backtest.shortability import (
    MIN_AVG_DOLLAR_VOLUME,
    MIN_SHORTABLE_PRICE,
    _avg_dollar_volume,
    _looks_like_warrant,
    classify_shortability,
    load_exchange_map,
)
from tests.conftest import make_bars, make_panel

DAYS = pd.bdate_range("2025-01-06", periods=20)


class TestLooksLikeWarrant:
    @pytest.mark.parametrize("ticker", ["NEXRW", "EDBLW", "DFNSW", "ABCDU", "ABCDR"])
    def test_five_letter_suffixes_flagged(self, ticker):
        assert _looks_like_warrant(ticker) is True

    @pytest.mark.parametrize("ticker", ["ABC.WS", "ABC-WT", "ABC.U"])
    def test_punctuated_suffixes_flagged(self, ticker):
        assert _looks_like_warrant(ticker) is True

    @pytest.mark.parametrize("ticker", ["MVIS", "SGLY", "PMI", "AAPL", "VALE"])
    def test_ordinary_tickers_not_flagged(self, ticker):
        assert _looks_like_warrant(ticker) is False

    def test_is_case_insensitive(self):
        assert _looks_like_warrant("nexrw") is True


class TestAvgDollarVolume:
    def test_averages_close_times_volume_before_entry(self):
        bars = make_bars(DAYS, open_=2.0, close=2.0, volume=1000)
        out = _avg_dollar_volume(bars, DAYS[10], lookback=5)
        assert out == pytest.approx(2000.0)

    def test_excludes_the_entry_bar_itself(self):
        vols = [1000] * 20
        vols[10] = 999_999_999  # entry-day spike must not count
        bars = make_bars(DAYS, open_=2.0, close=2.0, volume=vols)
        assert _avg_dollar_volume(bars, DAYS[10], lookback=5) == pytest.approx(2000.0)

    def test_no_prior_bars_returns_nan(self):
        bars = make_bars(DAYS, open_=2.0, close=2.0, volume=1000)
        assert pd.isna(_avg_dollar_volume(bars, DAYS[0], lookback=5))


class TestLoadExchangeMap:
    def test_reads_sec_cache_shape(self, tmp_path):
        cache = tmp_path / "exch.json"
        cache.write_text(json.dumps({
            "fields": ["cik", "name", "ticker", "exchange"],
            "data": [[1, "Nvidia", "NVDA", "Nasdaq"], [2, "Some OTC Co", "OTCX", "OTC"]],
        }))
        out = load_exchange_map(cache)
        assert out == {"NVDA": "Nasdaq", "OTCX": "OTC"}

    def test_missing_file_returns_empty_dict(self, tmp_path):
        assert load_exchange_map(tmp_path / "nope.json") == {}

    def test_malformed_file_returns_empty_dict(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        assert load_exchange_map(bad) == {}


def trades_frame(ticker="ABC", entry_price=5.0, t_split=DAYS[10]):
    """Default leaves 9 bars after the split, comfortably clear of the
    POST_SPLIT_SURVIVE_DAYS=5 delisting check, so tests isolate one rule at a time."""
    return pd.DataFrame([{
        "ticker": ticker,
        "entry_date": DAYS[5],
        "entry_price": entry_price,
        "t_split": t_split,
    }])


def liquid_panel(ticker="ABC", price=5.0, volume=1_000_000, days=DAYS):
    return make_panel({ticker: make_bars(days, open_=price, close=price, volume=volume)})


class TestClassifyShortability:
    def test_major_exchange_liquid_is_shortable(self):
        out = classify_shortability(trades_frame(), liquid_panel(),
                                    exchange_map={"ABC": "Nasdaq"})
        assert not bool(out.iloc[0]["likely_unshortable"])
        assert out.iloc[0]["unshortable_reasons"] == ""

    def test_otc_is_flagged(self):
        out = classify_shortability(trades_frame(), liquid_panel(),
                                    exchange_map={"ABC": "OTC"})
        assert bool(out.iloc[0]["likely_unshortable"])
        assert "non-major exchange" in out.iloc[0]["primary_reason"]

    def test_unlisted_ticker_is_flagged(self):
        out = classify_shortability(trades_frame(), liquid_panel(), exchange_map={})
        assert bool(out.iloc[0]["likely_unshortable"])

    def test_sub_dollar_price_is_flagged(self):
        out = classify_shortability(
            trades_frame(entry_price=0.50),
            liquid_panel(price=0.50),
            exchange_map={"ABC": "Nasdaq"},
        )
        assert bool(out.iloc[0]["likely_unshortable"])
        assert any("non-marginable" in r for r in [out.iloc[0]["unshortable_reasons"]])

    def test_price_at_threshold_is_not_flagged(self):
        out = classify_shortability(
            trades_frame(entry_price=MIN_SHORTABLE_PRICE),
            liquid_panel(price=MIN_SHORTABLE_PRICE),
            exchange_map={"ABC": "Nasdaq"},
        )
        assert not bool(out.iloc[0]["likely_unshortable"])

    def test_thin_liquidity_is_flagged(self):
        thin_volume = int(MIN_AVG_DOLLAR_VOLUME / 5.0 / 10)  # well under the floor
        out = classify_shortability(
            trades_frame(),
            liquid_panel(volume=thin_volume),
            exchange_map={"ABC": "Nasdaq"},
        )
        assert bool(out.iloc[0]["likely_unshortable"])
        assert "thin liquidity" in out.iloc[0]["unshortable_reasons"]

    def test_delisting_after_split_is_flagged(self):
        """Stops trading right after the split -> effectively untradeable."""
        short_days = DAYS[:13]  # only 2 bars after t_split at DAYS[10]
        out = classify_shortability(
            trades_frame(t_split=DAYS[10]),
            liquid_panel(days=short_days),
            exchange_map={"ABC": "Nasdaq"},
        )
        assert bool(out.iloc[0]["likely_unshortable"])
        assert "delisted" in out.iloc[0]["unshortable_reasons"]

    def test_warrant_is_flagged(self):
        out = classify_shortability(
            trades_frame(ticker="ABCDW"),
            liquid_panel(ticker="ABCDW"),
            exchange_map={"ABCDW": "Nasdaq"},
        )
        assert bool(out.iloc[0]["likely_unshortable"])
        assert "warrant" in out.iloc[0]["unshortable_reasons"]

    def test_reasons_accumulate(self):
        out = classify_shortability(
            trades_frame(entry_price=0.10),
            liquid_panel(price=0.10, volume=10),
            exchange_map={"ABC": "OTC"},
        )
        reasons = out.iloc[0]["unshortable_reasons"]
        assert reasons.count(";") >= 2, f"expected multiple reasons, got: {reasons}"

    def test_empty_trades_passthrough(self):
        empty = pd.DataFrame()
        assert classify_shortability(empty, liquid_panel(), exchange_map={}).empty

    def test_does_not_mutate_input(self):
        trades = trades_frame()
        before = trades.copy(deep=True)
        classify_shortability(trades, liquid_panel(), exchange_map={"ABC": "Nasdaq"})
        pd.testing.assert_frame_equal(trades, before)
