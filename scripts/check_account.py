#!/usr/bin/env python3
"""Read-only account status: is this account actually able to short right now?

Places no orders and changes nothing. Answers the two questions that gate live
trading, both of which have already been wrong once:

1. Is the account type MARGIN? Short selling is impossible in a cash account -
   every entry is rejected at the broker.
2. How much can actually be deployed? Schwab can report a healthy
   `liquidationValue` while `availableFunds` is near zero, which is exactly what
   happens in the window after margin is approved but before the held securities
   are recognised as collateral. Sizing off liquidationValue in that state
   overstates capacity by an order of magnitude.

    python scripts/check_account.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "src"))

from split_strategy.broker.schwab_auth import (SchwabAuthError, get_client,  # noqa: E402
                                               resolve_account_hash)

FIELDS = ("liquidationValue", "equity", "cashBalance", "availableFunds",
          "buyingPower", "maintenanceRequirement", "shortMarketValue")


def main() -> int:
    try:
        client = get_client(interactive=False)
        account_hash = resolve_account_hash(client)
    except SchwabAuthError as e:
        print(f"auth failed: {e}")
        return 2

    resp = client.get_account(account_hash, fields=client.Account.Fields.POSITIONS)
    if resp.status_code >= 400:
        print(f"account read failed: HTTP {resp.status_code}")
        return 2
    account = resp.json().get("securitiesAccount", {})
    balances = account.get("currentBalances", {}) or {}

    acct_type = account.get("type")
    print(f"account type          {acct_type}")
    for key in FIELDS:
        if key in balances:
            print(f"  {key:<22} ${balances[key]:,.2f}")

    positions = account.get("positions") or []
    if positions:
        print(f"\npositions ({len(positions)}):")
        for p in positions:
            sym = (p.get("instrument") or {}).get("symbol", "?")
            qty = (p.get("longQuantity") or 0) - (p.get("shortQuantity") or 0)
            print(f"  {sym:<8} {qty:>9,.0f}  ${p.get('marketValue', 0):>10,.2f}")

    print()
    if acct_type != "MARGIN":
        print("NOT READY: cash account. Short selling requires margin - every entry "
              "would be rejected at the broker.")
        return 1

    available = balances.get("availableFunds", 0.0)
    liquidation = balances.get("liquidationValue", 0.0)
    maintenance = balances.get("maintenanceRequirement", 0.0)

    # The tell for incomplete propagation: real securities held, but the broker is
    # not yet counting them, so nothing can be borrowed against them.
    if positions and maintenance == 0 and available < 0.1 * liquidation:
        print("MARGIN APPROVED BUT NOT PROPAGATED.")
        print(f"  Holding ${liquidation:,.0f} of securities, but maintenanceRequirement "
              f"is $0 and availableFunds is ${available:,.0f}.")
        print("  Schwab has not recognised the holdings as collateral yet. Usable "
              "capacity is the cash, not the portfolio. Re-check later.")
        return 1

    print(f"READY TO SHORT. Deployable now: ${available:,.0f} "
          f"(liquidation value ${liquidation:,.0f}).")
    print("  Size the margin budget off availableFunds, not liquidationValue.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
