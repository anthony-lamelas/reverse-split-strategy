# Schwab re-login

Refresh tokens expire ~weekly. Run this to re-authenticate:

```powershell
.\venv\Scripts\python.exe scripts\run_trading.py --login
```

Then push the refreshed token up to the Modal volume so the scheduled live run picks it up. Set `MODAL_PROFILE` first — the machine's default Modal CLI profile gets switched to `verse-prod` for other work, and `split-strategy-data` only exists under `anthony-lamelas23`:

```powershell
$env:MODAL_PROFILE = "anthony-lamelas23"
.\venv\Scripts\python.exe -m modal volume put --force split-strategy-data .schwab_token.json /.schwab_token.json
```
