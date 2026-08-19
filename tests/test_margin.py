"""Tests for FINRA 4210(c) short margin requirements.

This is the constraint that decides whether the strategy is implementable at all.
The $2.50-per-share floor below $5 INVERTS the economics of shorting cheap stocks:
the lower the price, the more margin a dollar of notional consumes. Since ~71% of
this strategy's signals price under $1.00, margin - not capital - is what caps
concurrency, and nothing in the system modelled it before.

    https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210
"""
from __future__ import annotations

import pytest

from split_strategy import margin as mgn


class TestMaintenanceRequirement:
    def test_sub_five_dollar_uses_the_two_fifty_floor(self):
        """$0.34 x 100 shares is $34 of stock but $250 of margin."""
        assert mgn.short_maintenance_requirement(0.34, 100) == pytest.approx(250.0)

    def test_sub_five_uses_market_value_when_it_exceeds_the_floor(self):
        """100% of market value binds between $2.50 and $5.00."""
        assert mgn.short_maintenance_requirement(4.00, 100) == pytest.approx(400.0)

    def test_at_five_dollars_switches_tier(self):
        """At $5 the rule flips to max($5/share, 30% of value) -> $5/share binds."""
        assert mgn.short_maintenance_requirement(5.00, 100) == pytest.approx(500.0)

    def test_high_priced_uses_thirty_percent(self):
        """$20/share: 30% of $2,000 = $600, which beats the $5/share floor."""
        assert mgn.short_maintenance_requirement(20.00, 100) == pytest.approx(600.0)

    def test_the_two_tiers_meet_sensibly_at_the_boundary(self):
        assert mgn.short_maintenance_requirement(4.99, 100) == pytest.approx(499.0)
        assert mgn.short_maintenance_requirement(5.00, 100) == pytest.approx(500.0)

    def test_house_multiple_scales_the_floor(self):
        base = mgn.short_maintenance_requirement(0.34, 100)
        assert mgn.short_maintenance_requirement(0.34, 100, house_multiple=1.5) == \
            pytest.approx(base * 1.5)

    @pytest.mark.parametrize("price,shares", [(0, 100), (1.0, 0), (-1, 10), (1.0, -5)])
    def test_non_positions_require_nothing(self, price, shares):
        assert mgn.short_maintenance_requirement(price, shares) == 0.0


class TestMarginMultiple:
    def test_cheap_stocks_cost_multiples_of_their_notional(self):
        """The finding that matters: a $0.0224 short needs ~112x its value."""
        assert mgn.margin_multiple(0.0224) == pytest.approx(2.50 / 0.0224, rel=1e-6)
        assert mgn.margin_multiple(0.0224) > 100

    def test_the_multiple_is_independent_of_position_size(self):
        """So a bigger account does not escape the constraint."""
        small = mgn.short_maintenance_requirement(0.34, 100) / (0.34 * 100)
        large = mgn.short_maintenance_requirement(0.34, 100_000) / (0.34 * 100_000)
        assert small == pytest.approx(large)

    def test_expensive_stocks_cost_a_fraction_of_notional(self):
        assert mgn.margin_multiple(45.88) == pytest.approx(0.30)

    def test_multiple_rises_monotonically_as_price_falls(self):
        prices = [0.02, 0.10, 0.34, 1.00, 2.50, 4.00]
        multiples = [mgn.margin_multiple(p) for p in prices]
        assert multiples == sorted(multiples, reverse=True)


class TestCapacity:
    def test_max_shares_respects_the_budget(self):
        # $2.50/share floor -> $1,000 supports 400 shares regardless of price.
        assert mgn.max_shares_for_margin(0.34, 1000.0) == 400
        assert mgn.max_shares_for_margin(0.02, 1000.0) == 400

    def test_a_single_cheap_position_can_exceed_a_whole_account(self):
        """RCON at $0.0224: a $50 notional short needs more than $4,932 of equity."""
        shares = int(50 / 0.0224)
        assert mgn.short_maintenance_requirement(0.0224, shares) > 4932.82

    def test_no_budget_means_no_shares(self):
        assert mgn.max_shares_for_margin(0.34, 0) == 0


class TestPortfolioRequirement:
    def test_sums_open_positions(self):
        positions = [
            {"ticker": "ABC", "filled_shares": 100, "entry_fill_price": 0.34},
            {"ticker": "XYZ", "filled_shares": 200, "entry_fill_price": 0.50},
        ]
        assert mgn.portfolio_margin_requirement(positions) == pytest.approx(750.0)

    def test_marks_against_the_live_quote_not_the_entry_price(self):
        """A stock that ROSE still needs marking to market - entry understates it."""
        class Q:
            last, ask = 6.00, 6.05
        positions = [{"ticker": "ABC", "filled_shares": 100, "entry_fill_price": 3.00}]

        at_entry = mgn.portfolio_margin_requirement(positions)
        at_market = mgn.portfolio_margin_requirement(positions, {"ABC": Q()})

        assert at_entry == pytest.approx(300.0)      # $2.50 floor x 100
        assert at_market == pytest.approx(500.0)     # $5.00 floor x 100

    def test_positions_without_shares_are_ignored(self):
        positions = [{"ticker": "ABC", "filled_shares": 0, "entry_fill_price": 1.0}]
        assert mgn.portfolio_margin_requirement(positions) == 0.0

    def test_falls_back_to_notional_over_shares_when_no_price(self):
        positions = [{"ticker": "ABC", "shares": 100, "notional": 40.0}]
        # implies $0.40/share -> $2.50 floor binds -> $250
        assert mgn.portfolio_margin_requirement(positions) == pytest.approx(250.0)


class TestDescribe:
    def test_reads_as_a_ratio(self):
        text = mgn.describe(0.34, 147)
        assert "$368 margin" in text
        assert "7.4x" in text
        assert "$0.3400/share" in text

    def test_empty_position_says_so(self):
        assert mgn.describe(0, 0) == "no position"
