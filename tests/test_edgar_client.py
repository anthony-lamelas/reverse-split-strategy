"""Tests for SEC submissions pagination.

`filings.recent` caps at roughly the last 1,000 filings; everything older lives in
`filings.files[]`. Reading only `recent` silently dropped older filings — and for a
frequent-filer micro-cap that can hide the 8-K establishing a split's announcement
date, which the whole strategy keys on.

All HTTP is stubbed; nothing here touches the network.
"""
from __future__ import annotations

import time

import pytest

from split_strategy.edgar import client as edgar_client
from split_strategy.edgar.client import (
    _RateLimiter,
    _page_intersects_window,
    get_all_company_filings,
    normalize_filing_arrays,
)


def arrays(*rows):
    """Build SEC's parallel-array block from (form, date, accession, doc) tuples."""
    return {
        "form": [r[0] for r in rows],
        "filingDate": [r[1] for r in rows],
        "accessionNumber": [r[2] for r in rows],
        "primaryDocument": [r[3] for r in rows],
    }


class TestNormalizeFilingArrays:
    def test_converts_columns_to_rows(self):
        block = arrays(
            ("8-K", "2025-06-01", "0001-25-000001", "a.htm"),
            ("10-Q", "2025-05-01", "0001-25-000002", "b.htm"),
        )
        out = normalize_filing_arrays(block)
        assert out == [
            {"form": "8-K", "filingDate": "2025-06-01",
             "accessionNumber": "0001-25-000001", "primaryDocument": "a.htm"},
            {"form": "10-Q", "filingDate": "2025-05-01",
             "accessionNumber": "0001-25-000002", "primaryDocument": "b.htm"},
        ]

    def test_ragged_arrays_truncate_to_shortest(self):
        """Guards the bug class the old index-parallel loop had: primaryDocument was
        indexed without a length check."""
        block = arrays(("8-K", "2025-06-01", "0001-25-000001", "a.htm"))
        block["form"].append("10-K")
        block["filingDate"].append("2025-01-01")
        # accessionNumber / primaryDocument deliberately left short
        out = normalize_filing_arrays(block)
        assert len(out) == 1

    @pytest.mark.parametrize("block", [None, {}, {"form": []}])
    def test_empty_inputs(self, block):
        assert normalize_filing_arrays(block) == []


class TestPageIntersectsWindow:
    PAGE = {"name": "p.json", "filingFrom": "2020-01-01", "filingTo": "2021-12-31"}

    def test_overlapping_window_included(self):
        assert _page_intersects_window(self.PAGE, "2021-06-01", "2022-06-01") is True

    def test_window_entirely_before_page_excluded(self):
        assert _page_intersects_window(self.PAGE, "2015-01-01", "2016-01-01") is False

    def test_window_entirely_after_page_excluded(self):
        assert _page_intersects_window(self.PAGE, "2023-01-01", "2024-01-01") is False

    def test_no_window_includes_everything(self):
        assert _page_intersects_window(self.PAGE, None, None) is True

    def test_page_without_bounds_is_included(self):
        assert _page_intersects_window({"name": "p.json"}, "2020-01-01", "2020-12-31") is True


class TestGetAllCompanyFilings:
    """The core regression: an old filing living only in the archive must be returned."""

    @pytest.fixture
    def stub_sec(self, monkeypatch):
        root = {
            "filings": {
                "recent": arrays(("8-K", "2025-06-01", "ACC-RECENT", "recent.htm")),
                "files": [
                    {"name": "old-001.json", "filingFrom": "2019-01-01", "filingTo": "2019-12-31"},
                    {"name": "old-002.json", "filingFrom": "2020-01-01", "filingTo": "2020-12-31"},
                ],
            }
        }
        pages = {
            "old-001.json": arrays(("8-K", "2019-05-01", "ACC-2019", "old2019.htm")),
            "old-002.json": arrays(("8-K", "2020-05-01", "ACC-2020", "old2020.htm")),
        }
        requested = []

        monkeypatch.setattr(edgar_client, "get_company_filings", lambda cik: root)

        def fake_page(name):
            requested.append(name)
            return pages.get(name)

        monkeypatch.setattr(edgar_client, "get_submissions_page", fake_page)
        return requested

    def test_archive_filings_are_recovered(self, stub_sec):
        out = get_all_company_filings("0000320193")
        accessions = {f["accessionNumber"] for f in out}
        assert accessions == {"ACC-RECENT", "ACC-2019", "ACC-2020"}

    def test_only_intersecting_pages_are_fetched(self, stub_sec):
        """A 2020 window must not pay for the 2019 archive page."""
        get_all_company_filings("0000320193", start_date="2020-01-01", end_date="2020-12-31")
        assert stub_sec == ["old-002.json"]

    def test_window_covering_all_fetches_all_pages(self, stub_sec):
        get_all_company_filings("0000320193", start_date="2018-01-01", end_date="2026-01-01")
        assert sorted(stub_sec) == ["old-001.json", "old-002.json"]

    def test_duplicate_accessions_are_deduped(self, monkeypatch):
        """`recent` and the newest archive page can overlap at the boundary."""
        dup = arrays(("8-K", "2025-06-01", "ACC-DUP", "x.htm"))
        monkeypatch.setattr(edgar_client, "get_company_filings", lambda cik: {
            "filings": {"recent": dup, "files": [{"name": "p.json"}]}
        })
        monkeypatch.setattr(edgar_client, "get_submissions_page", lambda name: dup)
        out = get_all_company_filings("0000320193")
        assert len(out) == 1

    def test_missing_root_returns_empty(self, monkeypatch):
        monkeypatch.setattr(edgar_client, "get_company_filings", lambda cik: None)
        assert get_all_company_filings("0000320193") == []

    def test_unfetchable_page_is_skipped_not_fatal(self, monkeypatch):
        monkeypatch.setattr(edgar_client, "get_company_filings", lambda cik: {
            "filings": {
                "recent": arrays(("8-K", "2025-06-01", "ACC-RECENT", "r.htm")),
                "files": [{"name": "broken.json"}],
            }
        })
        monkeypatch.setattr(edgar_client, "get_submissions_page", lambda name: None)
        out = get_all_company_filings("0000320193")
        assert [f["accessionNumber"] for f in out] == ["ACC-RECENT"]

    def test_no_files_key_still_works(self, monkeypatch):
        monkeypatch.setattr(edgar_client, "get_company_filings", lambda cik: {
            "filings": {"recent": arrays(("8-K", "2025-06-01", "ACC-ONLY", "r.htm"))}
        })
        monkeypatch.setattr(edgar_client, "get_submissions_page",
                            lambda name: pytest.fail("should not page"))
        assert len(get_all_company_filings("0000320193")) == 1


class TestRateLimiter:
    """Timing assertions use generous lower bounds: OS sleep granularity (~15ms on
    Windows) means short sleeps routinely undershoot, so tight bounds would be flaky
    in CI. We only need to prove pacing happens at all, not its exact duration."""

    RATE = 20.0  # 50ms between slots

    def test_spaces_out_requests(self):
        limiter = _RateLimiter(rate_per_sec=self.RATE)
        start = time.monotonic()
        for _ in range(3):
            limiter.acquire()
        elapsed = time.monotonic() - start
        # First is immediate, then two ~50ms waits => ~100ms nominal.
        assert elapsed >= 0.06, f"no pacing applied (elapsed {elapsed:.3f}s)"

    def test_is_threadsafe_across_workers(self):
        """The limiter must bound the *global* rate, not the per-thread rate - the
        whole reason the pre-existing per-thread sleeps were insufficient."""
        from concurrent.futures import ThreadPoolExecutor

        limiter = _RateLimiter(rate_per_sec=self.RATE)
        start = time.monotonic()
        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(lambda _: limiter.acquire(), range(8)))
        elapsed = time.monotonic() - start
        # 8 slots => 7 gaps => ~350ms nominal. Well clear of granularity noise.
        assert elapsed >= 0.20, (
            f"concurrent workers bypassed the global rate limit (elapsed {elapsed:.3f}s)"
        )
