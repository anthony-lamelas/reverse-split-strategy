"""Tests for Schwab OAuth client construction.

Regression coverage for a real production bug: schwab-py does not validate a cached
token at construction time - it only attempts a refresh lazily, on the first real API
call. The original get_client() only wrapped the *construction* call in try/except, so
an expired refresh token raised an uncaught authlib OAuthError deep inside whatever the
caller did first, instead of cleanly falling through to the interactive login flow.
"""
from __future__ import annotations

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
