"""Schwab OAuth client construction via the `schwab-py` library.

Token lifecycle (Schwab policy, confirmed):
  - access token  ~30 min  (auto-refreshed by the client)
  - refresh token ~7 days   (HARD expiry, cannot be extended)

So a token cached in SCHWAB_TOKEN_PATH keeps working unattended for up to 7 days;
after that the interactive login flow must be re-run. `get_client()` surfaces that
clearly instead of failing cryptically. Import of `schwab` is lazy so the rest of the
package (signals, dry-run) works without the library installed.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .. import config


class SchwabAuthError(RuntimeError):
    """Raised when a Schwab client cannot be constructed (missing keys/token/expiry)."""


def _require_keys():
    missing = [name for name, val in [
        ("SCHWAB_APP_KEY", config.SCHWAB_APP_KEY),
        ("SCHWAB_APP_SECRET", config.SCHWAB_APP_SECRET),
    ] if not val]
    if missing:
        raise SchwabAuthError(
            "Missing Schwab credentials: " + ", ".join(missing) +
            ". Generate them in the Schwab developer portal and add to .env "
            "(see docs/SCHWAB_SETUP.md)."
        )


def get_client(interactive: bool = False):
    """Return an authenticated schwab-py client.

    Args:
        interactive: if True and no valid token exists, run the browser login flow to
            mint a fresh token (needed roughly weekly). If False (e.g. in CI/cron) and
            the token is missing/expired, raise SchwabAuthError.
    """
    _require_keys()
    try:
        from schwab.auth import client_from_token_file, client_from_login_flow
    except ImportError as e:
        raise SchwabAuthError(
            "The `schwab-py` package is not installed. Run: pip install schwab-py"
        ) from e

    token_path = Path(config.SCHWAB_TOKEN_PATH)

    if token_path.exists():
        try:
            return client_from_token_file(
                token_path=str(token_path),
                api_key=config.SCHWAB_APP_KEY,
                app_secret=config.SCHWAB_APP_SECRET,
            )
        except Exception as e:
            # Most commonly: refresh token older than 7 days -> invalid_client.
            if not interactive:
                raise SchwabAuthError(
                    f"Cached Schwab token is invalid/expired ({e}). Re-run the login "
                    "flow: python scripts/run_signals.py --login"
                ) from e
            # fall through to interactive login below

    if not interactive:
        raise SchwabAuthError(
            "No Schwab token found. Run the one-time login: "
            "python scripts/run_signals.py --login"
        )

    return client_from_login_flow(
        api_key=config.SCHWAB_APP_KEY,
        app_secret=config.SCHWAB_APP_SECRET,
        callback_url=config.SCHWAB_CALLBACK_URL,
        token_path=str(token_path),
    )


def resolve_account_hash(client) -> str:
    """Return the account hash to trade (SCHWAB_ACCOUNT_HASH or the first account)."""
    if config.SCHWAB_ACCOUNT_HASH:
        return config.SCHWAB_ACCOUNT_HASH
    resp = client.get_account_numbers()
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise SchwabAuthError("No Schwab accounts returned for these credentials.")
    return data[0]["hashValue"]
