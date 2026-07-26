"""
Configuration settings for the Split Strategy application.
Handles environment variables and constant definitions.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
# Try to find .env file in project root (3 levels up from this file)
# src/split_strategy/config.py -> src/split_strategy -> src -> root
ROOT_DIR = Path(__file__).parent.parent.parent.absolute()
ENV_PATH = ROOT_DIR / '.env'

if ENV_PATH.exists():
    load_dotenv(ENV_PATH)

# MongoDB Configuration
MONGODB_URI = os.environ.get("MONGODB_URI")
if not MONGODB_URI:
    # Warning or Error? For now, we'll let it be None and fail at connection time if needed
    pass

MONGODB_DATABASE = os.environ.get("MONGODB_DATABASE", "split_strategy")
REVERSE_SPLITS_COLLECTION = os.environ.get("MONGODB_COLLECTION", "reverse_splits") # Default to reverse_splits
EDGAR_COLLECTION = "reverse_splits_edgar"
EARLY_WARNINGS_COLLECTION = "early_edgar_splits"

# EDGAR Configuration
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Split Strategy Analysis contact@splitstrategy.com")
SEC_BASE_URL = "https://data.sec.gov"
SEC_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data"
REQUEST_DELAY = 0.2

# Logging
LOG_DIR = ROOT_DIR / "logs"
# OpenAI Configuration
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# HTTP Headers
HEADERS = {
    "User-Agent": SEC_USER_AGENT,
    "Accept": "application/json, text/html"
}

# --- Schwab Trader API (automation) ---
# Generate these in the Schwab developer portal (see docs/SCHWAB_SETUP.md).
# Both naming conventions are accepted: SCHWAB_APP_KEY/SCHWAB_APP_SECRET (used in
# docs) or the portal's own CLIENT_ID/CLIENT_SECRET wording.
SCHWAB_APP_KEY = os.environ.get("SCHWAB_APP_KEY") or os.environ.get("CLIENT_ID")
SCHWAB_APP_SECRET = os.environ.get("SCHWAB_APP_SECRET") or os.environ.get("CLIENT_SECRET")
# Must exactly match the callback registered on the app; 127.0.0.1 loopback is typical.
SCHWAB_CALLBACK_URL = (
    os.environ.get("SCHWAB_CALLBACK_URL")
    or os.environ.get("CALLBACK_URL")
    or "https://127.0.0.1:8182"
)
# Where the OAuth token (access + 7-day refresh) is cached between runs.
SCHWAB_TOKEN_PATH = os.environ.get("SCHWAB_TOKEN_PATH", str(ROOT_DIR / ".schwab_token.json"))
# Optional: restrict trading to a specific account hash (else the first account is used).
SCHWAB_ACCOUNT_HASH = os.environ.get("SCHWAB_ACCOUNT_HASH")

# --- Trading / sizing defaults (mirror analysis/strategy.md + ui/dashboard.py) ---
DEFAULT_ACCOUNT_SIZE = float(os.environ.get("ACCOUNT_SIZE", "10000"))
TRADE_PCT = 0.05          # 5% of equity notional per trade
STOP_LOSS_PCT = 0.40      # 40% hard stop (short: stop is above entry)
MAX_GAP_UP_PCT = 0.30     # skip entry if it gaps up >30% vs prior close
# Max total notional committed across ALL concurrently open positions, as a fraction
# of account equity. Trades routinely overlap (backtest found a median of 13-55
# concurrent positions), so without this cap, position sizing silently assumes
# unlimited buying power. 1.0 = fully cash-collateralized, the realistic ceiling for
# non-marginable short sales (most of these microcaps). See docs/VALIDATION_REPORT.md.
MAX_EXPOSURE = float(os.environ.get("MAX_EXPOSURE", "1.0"))

# Optional SMTP alerting for the daily signal run (all optional; no-op if unset).
SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
ALERT_EMAIL_TO = os.environ.get("ALERT_EMAIL_TO")

