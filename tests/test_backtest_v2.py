"""Tests for backtest v2: the price loader's parsing and the trade simulator's rules.

The simulator's job is to be boring and right: every number the strategy is judged on
comes out of `simulate_trade`, so each exit path and each cost is pinned here.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from split_strategy.backtest import sim
from split_strategy.backtest.prices_v2 import PolygonPrices, bars_to_frame, splits_to_frame

FREE = sim.Costs().scaled(0)
ENTRY, SPLIT = "2026-10-05", "2026-10-13"   # Mon; cover T-1 = Mon 2026-10-12


def bars(rows, start="2026-10-05"):
    """rows: (open, high, low) per consecutive business day."""
    idx = pd.bdate_range(start, periods=len(rows))
    return pd.DataFrame([(o, h, l, o, 1000) for o, h, l in rows], index=idx,
                        columns=["Open", "High", "Low", "Close", "Volume"])


FLAT = [(2.0, 2.1, 1.9)] * 5 + [(1.9, 2.0, 1.8)] + [(1.9, 2.0, 1.8)] * 3
RULE = sim.Rule(entry="live", exit_sessions_before=1, stop=0.40, target=0.20, min_price=1.0)


def trade(rows, rule=RULE, costs=FREE, splits=None, entry=ENTRY, split=SPLIT,
          ratio=float("nan")):
    return sim.simulate_trade(bars(rows), splits, entry, split, rule, costs, ratio)


class TestSimulateTrade:
    def test_time_exit_covers_at_the_open_of_the_session_before_the_split(self):
        t, why = trade(FLAT)
        assert why is None and t["exit_reason"] == "time"
        assert t["exit_date"] == pd.Timestamp("2026-10-12") and t["exit_px"] == 1.9
        assert t["gross_return"] == pytest.approx(0.05)
        # Zero spread and borrow still leaves the regulatory fee on the entry.
        assert t["net_return"] == pytest.approx(0.05, abs=2e-4) and t["net_return"] < 0.05

    def test_take_profit_fills_at_the_target(self):
        t, _ = trade([(2.0, 2.1, 1.9), (1.9, 1.95, 1.55)] + [(1.6, 1.7, 1.5)] * 7)
        assert t["exit_reason"] == "target" and t["exit_px"] == pytest.approx(1.6)
        assert t["gross_return"] == pytest.approx(0.20)

    def test_stop_fills_at_the_stop(self):
        t, _ = trade([(2.0, 2.1, 1.9), (2.2, 2.9, 2.1)] + [(2.5, 2.6, 2.4)] * 7)
        assert t["exit_reason"] == "stop" and t["gross_return"] == pytest.approx(-0.40)

    def test_gap_through_the_stop_fills_at_the_open_not_the_stop(self):
        t, _ = trade([(2.0, 2.1, 1.9), (3.5, 3.6, 3.4)] + [(3.5, 3.6, 3.4)] * 7)
        assert t["exit_reason"] == "stop" and t["gross_return"] == pytest.approx(-0.75)

    def test_bar_touching_both_levels_is_scored_as_the_stop(self):
        t, _ = trade([(2.0, 2.1, 1.9), (2.0, 2.9, 1.5)] + [(2.0, 2.1, 1.9)] * 7)
        assert t["exit_reason"] == "stop"

    def test_no_stop_and_no_target_holds_to_the_cover(self):
        rule = sim.Rule(stop=None, target=None, min_price=None)
        t, _ = trade([(2.0, 2.1, 1.9), (2.0, 9.0, 0.5)] + [(2.0, 2.1, 1.9)] * 7, rule=rule)
        assert t["exit_reason"] == "time"

    def test_price_floor_uses_the_real_entry_price(self):
        cheap = [(0.4, 0.42, 0.38)] * 9
        assert trade(cheap)[1] == "below_floor"
        assert trade(cheap, rule=sim.Rule(min_price=None))[1] is None

    def test_split_too_soon_leaves_no_holding_period(self):
        assert trade(FLAT, split="2026-10-06")[1] == "no_holding_period"

    def test_missing_entry_bar_is_skipped_not_shifted(self):
        assert sim.simulate_trade(bars(FLAT, start="2026-10-06"), None, ENTRY, SPLIT,
                                  RULE, FREE)[1] == "no_entry_bar"

    def test_delisted_before_the_cover_is_counted_not_dropped(self):
        assert trade(FLAT[:3])[1] == "no_exit_bar"

    def test_costs_reduce_the_return(self):
        costs = sim.Costs(spread=lambda px: 0.04, borrow=lambda px: 0.365, notional=50.0)
        t, _ = trade(FLAT, costs=costs)
        # sell at 2.0*(1-0.02), cover at 1.9*(1+0.02), 7 days of 36.5%/yr borrow
        expected = (2.0 * 0.98 - 1.9 * 1.02) / 2.0 - 0.365 * 7 / 365
        assert t["net_return"] == pytest.approx(expected, abs=2e-4)
        assert t["net_return"] < t["gross_return"]


class TestSplitDayExit:
    RULE0 = sim.Rule(exit_sessions_before=0, stop=None, target=None, min_price=None)
    # 1-for-10 executes 2026-10-13: the unadjusted price jumps ten-fold that morning.
    ROWS = [(2.0, 2.1, 1.9)] * 6 + [(18.0, 19.0, 17.0)] * 3

    def test_post_split_price_is_restated_in_entry_dollars(self):
        splits = splits_to_frame([{"execution_date": "2026-10-13", "split_from": 10, "split_to": 1}])
        t, _ = trade(self.ROWS, rule=self.RULE0, splits=splits)
        assert t["exit_date"] == pd.Timestamp("2026-10-13")
        assert t["exit_px"] == pytest.approx(1.8) and t["gross_return"] == pytest.approx(0.10)

    def test_split_sized_jump_with_no_record_is_refused_not_booked(self):
        assert trade(self.ROWS, rule=self.RULE0, ratio=10.0)[1] == "unverified_jump"

    def test_cancelled_split_is_still_traded(self):
        # Nothing happened on the stated date: no split, no jump. A trader was in it.
        t, why = trade(FLAT, rule=self.RULE0, ratio=10.0)
        assert why is None and t["exit_date"] == pd.Timestamp("2026-10-13")

    def test_cover_before_the_split_never_reads_a_post_split_bar(self):
        t, _ = trade(self.ROWS, rule=sim.Rule(stop=None, target=None, min_price=None))
        assert t["exit_date"] == pd.Timestamp("2026-10-12") and t["exit_px"] == 2.0


def events(*rows):
    return pd.DataFrame([dict(ticker=t, entry_live=pd.Timestamp(e), entry_first_open=pd.Timestamp(e),
                              t_split=pd.Timestamp(s)) for t, e, s in rows])


class TestRunRule:
    def test_one_position_per_ticker(self):
        evs = events(("ABC", ENTRY, SPLIT), ("ABC", "2026-10-07", "2026-10-14"))
        trades, skipped = sim.run_rule(evs, lambda t: bars(FLAT), lambda t: None, RULE, FREE)
        assert len(trades) == 1 and skipped["already_short"] == 1

    def test_unpriced_ticker_is_counted(self):
        trades, skipped = sim.run_rule(events(("GONE", ENTRY, SPLIT)), lambda t: pd.DataFrame(),
                                       lambda t: None, RULE, FREE)
        assert trades.empty and skipped["no_entry_bar"] == 1

    def test_placebo_keeps_dates_and_moves_tickers(self):
        evs = events(*[(f"T{i}", ENTRY, SPLIT) for i in range(30)])
        fake = sim.placebo_events(evs, np.random.default_rng(1))
        assert sorted(fake["ticker"]) == sorted(evs["ticker"])
        assert (fake["ticker"] != evs["ticker"]).any()
        assert (fake["t_split"] == evs["t_split"]).all()


class TestPortfolio:
    def trades(self, n, px=2.0, ret=0.10, same_day=True):
        days = [pd.Timestamp(ENTRY)] * n if same_day else list(pd.bdate_range(ENTRY, periods=n))
        return pd.DataFrame([dict(ticker=f"T{i}", entry_date=d, exit_date=d + pd.Timedelta(days=7),
                                  entry_px=px, net_return=ret) for i, d in enumerate(days)])

    def test_daily_cap_limits_new_shorts(self):
        p = sim.portfolio(self.trades(3), max_new_per_day=1)
        assert p["taken"] == 1 and p["skipped_daily_cap"] == 2

    def test_pnl_is_notional_times_return(self):
        p = sim.portfolio(self.trades(1), equity=5000, max_notional=50)
        assert p["final_equity"] == pytest.approx(5000 + 25 * 2.0 * 0.10)

    def test_margin_rule_blocks_cheap_names(self):
        # $0.02 stock: 2,500 shares for $50, needing $6,250 of margin at $2.50/share.
        p = sim.portfolio(self.trades(1, px=0.02), equity=5000, max_notional=50)
        assert p["taken"] == 0 and p["skipped_margin"] == 1


class TestPolygonParsing:
    def test_bar_timestamps_become_eastern_session_dates(self):
        frame = bars_to_frame([{"t": 1790913600000, "o": 0.153, "h": 0.158, "l": 0.1476,
                                "c": 0.151, "v": 1305541}])
        assert frame.index[0] == pd.Timestamp("2026-10-02")
        assert frame.iloc[0]["Open"] == 0.153

    def test_empty_response_is_an_empty_frame(self):
        assert bars_to_frame([]).empty

    def test_reverse_split_factor_is_the_price_multiple(self):
        frame = splits_to_frame([{"execution_date": "2026-09-28", "split_from": 10, "split_to": 1}])
        assert frame.iloc[0]["factor"] == 10.0

    def test_cache_is_reused_only_when_it_covers_the_request(self, tmp_path):
        calls = []

        class Session:
            def get(self, url, timeout=None, params=None):
                calls.append(url)

                class R:
                    status_code = 200

                    def raise_for_status(self):
                        pass

                    def json(self):
                        return {"results": [{"t": 1790913600000, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}]}
                return R()

        src = PolygonPrices(api_key="k", cache_dir=tmp_path, min_interval=0, session=Session())
        src.daily("ABC", "2026-10-01", "2026-10-02")
        src.daily("ABC", "2026-10-01", "2026-10-02")
        assert len(calls) == 1
        src.daily("ABC", "2026-09-01", "2026-10-02")   # wider: must re-fetch
        assert len(calls) == 2


class TestLastSession:
    # Effective Tue 2026-10-13, so the last session is Mon 2026-10-12.
    # bars: (open, high, low), close == open in the helper; set closes explicitly.
    def frame(self, rows):
        f = bars(rows, start="2026-10-08")   # Thu 8, Fri 9, Mon 12, Tue 13
        return f

    def run(self, f, rule=sim.LastSessionRule(), known="2026-10-06", splits=None, ratio=10.0):
        return sim.simulate_last_session(f, splits, known, "2026-10-13", rule, FREE, ratio)

    def test_shorts_the_open_and_covers_the_close_of_the_session_before_the_split(self):
        f = self.frame([(2.0, 2.1, 1.9)] * 4)
        f.loc["2026-10-12", ["Open", "Close"]] = [2.0, 1.8]
        t, why = self.run(f)
        assert why is None and t["entry_date"] == t["exit_date"] == pd.Timestamp("2026-10-12")
        assert t["gross_return"] == pytest.approx(0.10) and t["days"] == 1

    def test_filing_not_yet_known_that_morning_is_not_traded(self):
        f = self.frame([(2.0, 2.1, 1.9)] * 4)
        assert self.run(f, known="2026-10-13")[1] == "announced_too_late"

    def test_next_open_cover_undoes_a_recorded_split(self):
        f = self.frame([(2.0, 2.1, 1.9)] * 3 + [(17.0, 18.0, 16.0)])
        splits = splits_to_frame([{"execution_date": "2026-10-13", "split_from": 10, "split_to": 1}])
        t, _ = self.run(f, sim.LastSessionRule(cover="next_open"), splits=splits)
        assert t["exit_px"] == pytest.approx(1.7) and t["gross_return"] == pytest.approx(0.15)

    def test_split_sized_jump_with_no_record_is_refused_not_booked(self):
        f = self.frame([(2.0, 2.1, 1.9)] * 3 + [(17.0, 18.0, 16.0)])
        assert self.run(f, sim.LastSessionRule(cover="next_open"))[1] == "unverified_jump"

    def test_postponed_split_is_still_a_trade(self):
        # No split executed and no jump: an ordinary overnight move, kept in the sample.
        f = self.frame([(2.0, 2.1, 1.9)] * 3 + [(2.2, 2.3, 2.1)])
        t, why = self.run(f, sim.LastSessionRule(cover="next_open"))
        assert why is None and t["gross_return"] == pytest.approx(-0.10)

    def test_intraday_stop(self):
        f = self.frame([(2.0, 2.1, 1.9)] * 2 + [(2.0, 3.0, 1.9), (2.0, 2.1, 1.9)])
        t, _ = self.run(f, sim.LastSessionRule(stop=0.40))
        assert t["exit_reason"] == "stop" and t["gross_return"] == pytest.approx(-0.40)

    def test_floor(self):
        f = self.frame([(0.3, 0.31, 0.29)] * 4)
        assert self.run(f, sim.LastSessionRule(min_price=0.5))[1] == "below_floor"


class TestWaitForSplit:
    RULE = sim.Rule(entry="first_open", wait_for_split=True, exit_sessions_before=0,
                    stop=None, target=None, min_price=None)

    def test_covers_the_morning_the_split_takes_effect_even_if_late(self):
        # Stated 2026-10-13; the provider records it one session later.
        rows = [(2.0, 2.1, 1.9)] * 7 + [(17.0, 18.0, 16.0)] * 3
        splits = splits_to_frame([{"execution_date": "2026-10-14", "split_from": 10, "split_to": 1}])
        t, why = trade(rows, rule=self.RULE, splits=splits, ratio=10.0)
        assert why is None and t["exit_date"] == pd.Timestamp("2026-10-14")
        assert t["exit_px"] == pytest.approx(1.7)

    def test_cancelled_split_is_covered_after_the_wait(self):
        rows = [(2.0, 2.1, 1.9)] * 30
        t, why = trade(rows, rule=self.RULE, ratio=10.0)
        # Stated 2026-10-13 is the 7th bar; ten sessions later is 2026-10-27.
        assert why is None and t["exit_date"] == pd.Timestamp("2026-10-27")

    def test_history_ending_before_the_stated_date_cannot_be_closed(self):
        assert trade([(2.0, 2.1, 1.9)] * 3, rule=self.RULE)[1] == "no_exit_bar"
