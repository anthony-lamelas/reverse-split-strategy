"""Tests for EDGAR filing scoring.

`score_filing` decides which filings are trustworthy enough to define a trade's
announcement date (Tier A/B). If its thresholds drift, the historical event set the
whole backtest rests on drifts with it — so the tier boundaries are pinned explicitly.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from split_strategy.edgar.scoring import (
    get_business_days_diff,
    has_rs_keyword,
    is_year_like_ratio,
    parse_sa_ratio,
    score_filing,
)


class TestParseSaRatio:
    @pytest.mark.parametrize("text,expected", [
        ("1 : 100", (1, 100)),
        ("1:100", (1, 100)),
        ("1 for 100", None),      # 'for' is not handled by this parser
        ("1 : 10", (1, 10)),
        ("  5 : 25  ", (5, 25)),
    ])
    def test_parses_colon_forms(self, text, expected):
        assert parse_sa_ratio(text) == expected

    @pytest.mark.parametrize("bad", [None, "", "no numbers", "0:10", "10:0"])
    def test_rejects_unusable(self, bad):
        assert parse_sa_ratio(bad) is None


class TestIsYearLikeRatio:
    def test_year_pair_is_flagged(self):
        assert is_year_like_ratio(2018, 2019) is True

    @pytest.mark.parametrize("num,den", [(1, 10), (1, 100), (1899, 2000), (2000, 2101)])
    def test_real_ratios_not_flagged(self, num, den):
        assert is_year_like_ratio(num, den) is False


class TestHasRsKeyword:
    @pytest.mark.parametrize("text", [
        "a reverse stock split of the Company",
        "REVERSE SPLIT effective immediately",
        "reverse   split",  # collapsed whitespace
    ])
    def test_detects(self, text):
        assert has_rs_keyword(text) is True

    @pytest.mark.parametrize("text", ["forward split", "stock split", "", "splitting hairs"])
    def test_rejects(self, text):
        assert has_rs_keyword(text) is False


class TestBusinessDaysDiff:
    def test_same_day_is_zero(self):
        d = datetime(2025, 1, 6)  # Monday
        assert get_business_days_diff(d, d) == 0

    def test_across_a_weekend(self):
        # Fri 2025-01-10 -> Mon 2025-01-13 is one business day apart.
        assert get_business_days_diff(datetime(2025, 1, 10), datetime(2025, 1, 13)) == 1

    def test_is_symmetric(self):
        a, b = datetime(2025, 1, 6), datetime(2025, 1, 10)
        assert get_business_days_diff(a, b) == get_business_days_diff(b, a)

    def test_accepts_mixed_str_and_datetime(self):
        """Regression: parse_date returns a str while callers pass datetimes. Comparing
        the two raised TypeError, which killed scoring for the entire split."""
        assert get_business_days_diff("2025-01-10", datetime(2025, 1, 13)) == 1
        assert get_business_days_diff(datetime(2025, 1, 10), "2025-01-13") == 1
        assert get_business_days_diff("2025-01-10", "2025-01-13") == 1

    def test_rejects_unparseable(self):
        with pytest.raises(ValueError):
            get_business_days_diff("not-a-date", datetime(2025, 1, 13))


def make_filing(**overrides):
    """A minimal filing dict that passes the RS hard gate."""
    base = {
        "form": "8-K",
        "filing_date": "2025-01-15",
        "ratio_num": 1,
        "ratio_den": 10,
        "effective_date": "2025-02-01",
        "flags": {},
        "items": [],
        "text_matches": {},
    }
    base.update(overrides)
    return base


class TestScoreFilingHardGate:
    def test_no_rs_evidence_returns_tier_f(self):
        filing = {"form": "8-K", "flags": {}, "items": [], "text_matches": {}}
        result = score_filing(filing, None, None)
        assert result["tier"] == "F"
        assert result["score"] == 0
        assert result["candidate_announce_date"] is None

    def test_keyword_alone_passes_gate(self):
        filing = {
            "form": "10-K", "flags": {}, "items": [], "filing_date": "2025-01-15",
            "text_matches": {"body": "a reverse stock split was approved"},
        }
        assert score_filing(filing, None, None)["tier"] != "F"

    def test_compliance_flag_alone_passes_gate(self):
        filing = {
            "form": "10-K", "flags": {"compliance_flag": True}, "items": [],
            "filing_date": "2025-01-15", "text_matches": {},
        }
        assert score_filing(filing, None, None)["tier"] != "F"


class TestScoreFilingComponents:
    @pytest.mark.parametrize("form,points", [
        ("8-K", 3), ("6-K", 3),
        ("DEF 14A", 2), ("PRE 14A", 2), ("DEFA14A", 2),
        ("S-1", 1), ("424B5", 1), ("FWP", 1),
        ("10-K", 0), ("10-Q", 0), ("20-F", 0),
    ])
    def test_form_priors(self, form, points):
        """Score = form prior + 2 (valid ratio) + 1 (effective date)."""
        result = score_filing(make_filing(form=form), None, None)
        assert result["score"] == points + 3

    def test_valid_ratio_adds_two(self):
        with_ratio = score_filing(make_filing(), None, None)["score"]
        without = score_filing(make_filing(ratio_num=None, ratio_den=None), None, None)["score"]
        assert with_ratio - without == 2

    def test_year_like_ratio_earns_nothing(self):
        """2018:2019 is a date range that happened to parse, not a split ratio."""
        result = score_filing(make_filing(ratio_num=2018, ratio_den=2019), None, None)
        assert result["score"] == 3 + 1  # form + effective date only

    def test_effective_date_adds_one(self):
        with_date = score_filing(make_filing(), None, None)["score"]
        without = score_filing(make_filing(effective_date=None), None, None)["score"]
        assert with_date - without == 1

    def test_compliance_flag_adds_one(self):
        base = score_filing(make_filing(), None, None)["score"]
        flagged = score_filing(make_filing(flags={"compliance_flag": True}), None, None)["score"]
        assert flagged - base == 1

    def test_item_302_adds_one(self):
        base = score_filing(make_filing(), None, None)["score"]
        with_item = score_filing(make_filing(items=["3.02"]), None, None)["score"]
        assert with_item - base == 1

    def test_matching_sa_ratio_adds_one(self):
        base = score_filing(make_filing(), None, None)["score"]
        matched = score_filing(make_filing(), (1, 10), None)["score"]
        assert matched - base == 1

    def test_mismatched_sa_ratio_adds_nothing(self):
        base = score_filing(make_filing(), None, None)["score"]
        mismatched = score_filing(make_filing(), (1, 50), None)["score"]
        assert mismatched == base

    def test_effective_date_near_sa_adds_one(self):
        base = score_filing(make_filing(), None, None)["score"]
        near = score_filing(make_filing(), None, datetime(2025, 2, 3))["score"]
        assert near - base == 1

    def test_survives_unparseable_effective_date(self):
        """A junk effective date must cost only the +1 bonus, never raise - the caller
        discards every filing for the split if scoring throws."""
        filing = make_filing(effective_date="not a date")
        result = score_filing(filing, None, datetime(2025, 2, 3))
        assert result["tier"] in {"A", "B", "C"}

    def test_financing_only_incurs_penalty(self):
        """A financing mention with no ratio and no date is weak evidence, not strong."""
        filing = make_filing(ratio_num=None, ratio_den=None, effective_date=None,
                             flags={"financing_flag": True, "compliance_flag": True})
        result = score_filing(filing, None, None)
        # 8-K(+3) + compliance(+1) - financing(-1)
        assert result["score"] == 3
        assert any("Financing only" in r for r in result["reasons"])


class TestScoreFilingTiers:
    """Tier boundaries are A>=5, B>=3, else C. These gate the backtest event set."""

    def test_tier_a_at_boundary(self):
        # 8-K(3) + ratio(2) = 5
        filing = make_filing(effective_date=None)
        result = score_filing(filing, None, None)
        assert result["score"] == 5
        assert result["tier"] == "A"

    def test_tier_b_at_boundary(self):
        # 8-K(3) only. Keeps an RS keyword so it clears the hard gate without scoring.
        filing = make_filing(ratio_num=None, ratio_den=None, effective_date=None,
                             text_matches={"body": "a reverse stock split"})
        result = score_filing(filing, None, None)
        assert result["score"] == 3
        assert result["tier"] == "B"

    def test_tier_c_below_boundary(self):
        # S-1(1) + effective date(1) = 2
        filing = make_filing(form="S-1", ratio_num=None, ratio_den=None)
        result = score_filing(filing, None, None)
        assert result["score"] == 2
        assert result["tier"] == "C"


class TestCandidateAnnounceDate:
    def test_falls_back_to_filing_date(self):
        result = score_filing(make_filing(), None, None)
        assert result["candidate_announce_date"] == "2025-01-15"

    def test_uses_earlier_announce_date_when_present(self):
        filing = make_filing(announce_date="2025-01-10")
        assert score_filing(filing, None, None)["candidate_announce_date"] == "2025-01-10"

    def test_ignores_announce_date_after_filing_date(self):
        """An announcement can't post-date the filing that reports it."""
        filing = make_filing(announce_date="2025-01-20")
        assert score_filing(filing, None, None)["candidate_announce_date"] == "2025-01-15"
