"""Tests for relocatable state.

Everything the trading path persists - ledger, audit log, kill switch, ground-truth
CSV - has to live somewhere configurable, because the host is moving to Modal where
there is no repo checkout and the only writable place is a mounted Volume. These pin
that redirection so the same code runs unchanged in both places.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    """Reload config with DATA_DIR/LOG_DIR pointed at a temp directory."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    from split_strategy import config as c
    importlib.reload(c)
    yield c
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.delenv("LOG_DIR", raising=False)
    importlib.reload(c)


class TestRelocation:
    def test_data_and_log_dirs_follow_the_environment(self, cfg, tmp_path):
        assert cfg.DATA_DIR == tmp_path / "data"
        assert cfg.LOG_DIR == tmp_path / "logs"

    def test_defaults_keep_the_repo_layout(self, monkeypatch):
        """Unset env must behave exactly as before, so local runs are unaffected."""
        monkeypatch.delenv("DATA_DIR", raising=False)
        monkeypatch.delenv("LOG_DIR", raising=False)
        from split_strategy import config as c
        importlib.reload(c)
        assert c.DATA_DIR.name == "DATA"
        assert c.LOG_DIR.name == "logs"
        assert c.DATA_DIR.parent == c.ROOT_DIR


class TestKillSwitch:
    def test_off_by_default(self, cfg):
        assert cfg.STOP_TRADING is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", " 1 "])
    def test_env_values_that_halt(self, monkeypatch, value):
        monkeypatch.setenv("STOP_TRADING", value)
        from split_strategy import config as c
        importlib.reload(c)
        assert c.STOP_TRADING is True
        monkeypatch.delenv("STOP_TRADING")
        importlib.reload(c)

    @pytest.mark.parametrize("value", ["", "0", "false", "no"])
    def test_env_values_that_do_not_halt(self, monkeypatch, value):
        monkeypatch.setenv("STOP_TRADING", value)
        from split_strategy import config as c
        importlib.reload(c)
        assert c.STOP_TRADING is False
        monkeypatch.delenv("STOP_TRADING")
        importlib.reload(c)


class TestGapFilterDefault:
    def test_no_gap_filter_by_default(self):
        """The walk-forward chose max_gap_up=inf in 9 of 11 folds.

        Live used to hard-code 0.30, vetoing trades the validated strategy took -
        and in the wrong direction, since for a short a gap UP is a better entry.
        """
        from split_strategy import config as c
        importlib.reload(c)
        assert c.MAX_GAP_UP_PCT == float("inf")

    def test_the_old_veto_can_be_restored(self, monkeypatch):
        monkeypatch.setenv("MAX_GAP_UP_PCT", "0.30")
        from split_strategy import config as c
        importlib.reload(c)
        assert c.MAX_GAP_UP_PCT == pytest.approx(0.30)
        monkeypatch.delenv("MAX_GAP_UP_PCT")
        importlib.reload(c)
