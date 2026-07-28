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
# SEC fair-access policy requires a real, reachable contact in the User-Agent. Shipping
# a fake one risks an IP ban for the whole pipeline. We warn rather than hard-fail so an
# unset value can't take down a nightly run mid-flight - but the placeholder below is
# NOT compliant and must be overridden via the SEC_USER_AGENT env var.
_SEC_UA_PLACEHOLDER = "Split Strategy Analysis contact@splitstrategy.com"
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT") or _SEC_UA_PLACEHOLDER
if SEC_USER_AGENT == _SEC_UA_PLACEHOLDER:
    import warnings

    warnings.warn(
        "SEC_USER_AGENT is unset and is falling back to a placeholder contact address. "
        "SEC fair-access policy requires a real name + email; set SEC_USER_AGENT in .env "
        "(and as a GitHub Actions secret) to avoid risking an IP ban.",
        RuntimeWarning,
        stacklevel=2,
    )
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
# 2% of equity notional per trade (deliberately below the 5% used in backtesting -
# stops don't help this strategy (tail_risk_test.py: every stop level tested reduced
# returns without reliably reducing drawdown), so position size is the only real
# tail-risk lever until live behavior is confirmed against the backtest).
TRADE_PCT = float(os.environ.get("TRADE_PCT", "0.02"))
STOP_LOSS_PCT = 0.40      # legacy reference only - validated strategy uses NO stop
MAX_GAP_UP_PCT = 0.30     # skip entry if it gaps up >30% vs prior close
# Max total notional committed across ALL concurrently open positions, as a fraction
# of account equity. Trades routinely overlap (backtest found a median of 13-55
# concurrent positions), so without this cap, position sizing silently assumes
# unlimited buying power. 1.0 = fully cash-collateralized, the realistic ceiling for
# non-marginable short sales (most of these microcaps). See docs/VALIDATION_REPORT.md.
MAX_EXPOSURE = float(os.environ.get("MAX_EXPOSURE", "1.0"))

# --- Live-trading circuit breakers (see docs/LIVE_DEPLOYMENT.md) ---
# Deliberately NO concurrent-position cap: the 100% exposure ceiling above governs how
# many positions can be open. These limit the *rate* of new risk and the quality of
# fills, guarding against a runaway loop or a bad data day.
MAX_NEW_SHORTS_PER_DAY = int(os.environ.get("MAX_NEW_SHORTS_PER_DAY", "5"))
MAX_DAILY_NOTIONAL = float(os.environ.get("MAX_DAILY_NOTIONAL", "5000"))
# Annualized hard-to-borrow ceiling, in percent. Backtesting showed Strategy B still
# profitable at 200%/yr borrow, but a name that expensive is a warning sign.
MAX_HTB_RATE = float(os.environ.get("MAX_HTB_RATE", "100"))
# Skip names whose bid-ask spread exceeds this fraction of the mid. On a $0.09 stock a
# one-cent spread is ~11% - crossing it twice costs far more than the strategy's edge.
MAX_SPREAD_PCT = float(os.environ.get("MAX_SPREAD_PCT", "0.05"))

# Optional SMTP alerting for the daily signal run (all optional; no-op if unset).
SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")
# Gmail displays App Passwords grouped with spaces ("abcd efgh ijkl mnop") for
# readability, but the real credential has none - strip them so a copy-paste that
# includes the spaces doesn't silently fail auth.
_smtp_password_raw = os.environ.get("SMTP_PASSWORD")
SMTP_PASSWORD = _smtp_password_raw.replace(" ", "").strip() if _smtp_password_raw else None
ALERT_EMAIL_TO = os.environ.get("ALERT_EMAIL_TO")

