# Login
  .\venv\Scripts\python.exe scripts\run_trading.py --login
.\venv\Scripts\python.exe -m modal volume put --force split-strategy-data .schwab_token.json /.schwab_token.json

# Stop
.\venv\Scripts\python.exe scripts\emergency_stop.py

# Dashboard  (health, open book, today's skips, realized P&L - prints once, exits)
.\venv\Scripts\python.exe scripts\dashboard.py

# If the dashboard reports a missing volume or workspace, the global Modal profile
# has reverted. Pin it for the one command:
$env:MODAL_PROFILE="anthony-lamelas23"; .\venv\Scripts\python.exe scripts\dashboard.py