"""Tests for Schwab OAuth client construction.

Regression coverage for a real production bug: schwab-py does not validate a cached
token at construction time - it only attempts a refresh lazily, on the first real API
call. The original get_client() only wrapped the *construction* call in try/except, so
an expired refresh token raised an uncaught authlib OAuthError deep inside whatever the
caller did first, instead of cleanly falling through to the interactive login flow.
"""
from __future__ import annotations

import json
import os
import time
from unittest.mock import MagicMock

import pytest

from split_strategy.broker import schwab_auth


@pytest.fixture
def token_file(tmp_path):
    p = tmp_path / ".schwab_token.json"
    p.write_text("{}")
    return p


@pytest.fixture
def keys_configured(monkeypatch):
    monkeypatch.setattr(schwab_auth.config, "SCHWAB_APP_KEY", "test_key")
    monkeypatch.setattr(schwab_auth.config, "SCHWAB_APP_SECRET", "test_secret")


def _patch_schwab_auth_module(monkeypatch, *, from_token_file=None, from_login_flow=None):
    """Patch schwab.auth's functions at the source module, since get_client() imports
    them locally (`from schwab.auth import ...`) inside the function body."""
    import schwab.auth as real_schwab_auth

    if from_token_file is not None:
        monkeypatch.setattr(real_schwab_auth, "client_from_token_file", from_token_file)
    if from_login_flow is not None:
        monkeypatch.setattr(real_schwab_auth, "client_from_login_flow", from_login_flow)


def test_missing_keys_raises_before_touching_token(monkeypatch):
    monkeypatch.setattr(schwab_auth.config, "SCHWAB_APP_KEY", None)
    monkeypatch.setattr(schwab_auth.config, "SCHWAB_APP_SECRET", None)
    with pytest.raises(schwab_auth.SchwabAuthError, match="Missing Schwab credentials"):
        schwab_auth.get_client()


def test_valid_cached_token_returns_client_without_login_flow(monkeypatch, keys_configured, token_file):
    monkeypatch.setattr(schwab_auth.config, "SCHWAB_TOKEN_PATH", str(token_file))
    good_client = MagicMock()
    good_client.get_account_numbers.return_value = MagicMock(status_code=200)
    login_flow = MagicMock()
    _patch_schwab_auth_module(monkeypatch, from_token_file=lambda **kw: good_client, from_login_flow=login_flow)

    result = schwab_auth.get_client(interactive=False)

    assert result is good_client
    good_client.get_account_numbers.assert_called_once()
    login_flow.assert_not_called()


def test_expired_token_noninteractive_raises_clean_error(monkeypatch, keys_configured, token_file):
    """The exact bug: an expired refresh token used to raise an uncaught authlib
    OAuthError from deep inside the caller's first real request. It must now surface as
    a clean SchwabAuthError from get_client() itself."""
    monkeypatch.setattr(schwab_auth.config, "SCHWAB_TOKEN_PATH", str(token_file))
    stale_client = MagicMock()
    stale_client.get_account_numbers.side_effect = RuntimeError(
        "unsupported_token_type: 400 Bad Request: "
        '"{"error_description":"Refresh token is invalid, expired or revoked","error":"invalid_grant"}"'
    )
    login_flow = MagicMock()
    _patch_schwab_auth_module(monkeypatch, from_token_file=lambda **kw: stale_client, from_login_flow=login_flow)

    with pytest.raises(schwab_auth.SchwabAuthError, match="invalid/expired"):
        schwab_auth.get_client(interactive=False)
    login_flow.assert_not_called()


def test_expired_token_interactive_falls_through_to_login_flow(monkeypatch, keys_configured, token_file):
    """The actual regression: with interactive=True, an expired cached token must fall
    through to the browser login flow instead of returning a client that will crash on
    first real use."""
    monkeypatch.setattr(schwab_auth.config, "SCHWAB_TOKEN_PATH", str(token_file))
    stale_client = MagicMock()
    stale_client.get_account_numbers.side_effect = RuntimeError("invalid_grant")
    fresh_client = MagicMock()
    login_flow = MagicMock(return_value=fresh_client)
    _patch_schwab_auth_module(monkeypatch, from_token_file=lambda **kw: stale_client, from_login_flow=login_flow)

    result = schwab_auth.get_client(interactive=True)

    assert result is fresh_client
    login_flow.assert_called_once()


def test_no_token_file_noninteractive_raises(monkeypatch, keys_configured, tmp_path):
    monkeypatch.setattr(schwab_auth.config, "SCHWAB_TOKEN_PATH", str(tmp_path / "does_not_exist.json"))
    with pytest.raises(schwab_auth.SchwabAuthError, match="No Schwab token found"):
        schwab_auth.get_client(interactive=False)


def test_no_token_file_interactive_runs_login_flow(monkeypatch, keys_configured, tmp_path):
    monkeypatch.setattr(schwab_auth.config, "SCHWAB_TOKEN_PATH", str(tmp_path / "does_not_exist.json"))
    fresh_client = MagicMock()
    login_flow = MagicMock(return_value=fresh_client)
    _patch_schwab_auth_module(monkeypatch, from_login_flow=login_flow)

    result = schwab_auth.get_client(interactive=True)

    assert result is fresh_client
    login_flow.assert_called_once()


# ---------------------------------------------------------------------------------
# token age
# ---------------------------------------------------------------------------------
# The 7-day refresh clock lives in `creation_timestamp` inside the token file. The
# file itself is rewritten every ~30 min when the access token refreshes, so reading
# its mtime reports the last *refresh*, not the last *login*. That bug pinned the
# computed age near zero and silently disabled the expiry warning, letting the login
# lapse unannounced twice in August 2026.

def _write_token(path, created_epoch: float, *, include_key: bool = True) -> None:
    body = {"token": {"expires_at": created_epoch + 1800}}
    if include_key:
        body["creation_timestamp"] = created_epoch
    path.write_text(json.dumps(body), encoding="utf-8")


def test_token_age_reads_creation_timestamp_not_mtime(tmp_path):
    """The exact shape of the real bug: mtime is fresh, creation_timestamp is old."""
    token = tmp_path / "token.json"
    six_days_ago = time.time() - 6 * 86400
    _write_token(token, six_days_ago)
    # Touch it, the way a 30-minute access-token refresh would.
    os.utime(token, (time.time(), time.time()))

    age = schwab_auth.token_age_days(token)

    assert age == pytest.approx(6.0, abs=0.01)


def test_token_age_falls_back_to_mtime_without_the_key(tmp_path):
    token = tmp_path / "token.json"
    _write_token(token, time.time(), include_key=False)
    three_days_ago = time.time() - 3 * 86400
    os.utime(token, (three_days_ago, three_days_ago))

    assert schwab_auth.token_age_days(token) == pytest.approx(3.0, abs=0.01)


def test_token_age_is_none_when_no_file(tmp_path):
    assert schwab_auth.token_age_days(tmp_path / "nope.json") is None


def test_expiry_warning_window_is_reachable(tmp_path):
    """Regression: with the mtime bug this could never be true, so nothing ever fired."""
    token = tmp_path / "token.json"
    _write_token(token, time.time() - 5.5 * 86400)
    os.utime(token, (time.time(), time.time()))

    days_left = schwab_auth.REFRESH_TOKEN_DAYS - schwab_auth.token_age_days(token)

    assert 0 < days_left <= 2
