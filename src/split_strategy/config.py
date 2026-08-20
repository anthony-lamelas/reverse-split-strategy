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

# --- Writable state locations ---
# Everything the trading path persists lives under these two, and both are
# env-overridable so the same code runs unchanged on a host with no repo checkout.
# On Modal these point at a mounted Volume (/data, /data/logs); locally they default
# to the repo, so behaviour is identical to before.
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT_DIR / "DATA")))
LOG_DIR = Path(os.environ.get("LOG_DIR", str(ROOT_DIR / "logs")))

# Kill switch. A STOP file under DATA_DIR halts trading, and STOP_TRADING=1 does the
# same via env - on a serverless host setting a secret is far quicker than writing a
# file into a Volume, and an emergency stop should not depend on the slower path.
STOP_TRADING = os.environ.get("STOP_TRADING", "").strip().lower() in ("1", "true", "yes")
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
# Skip entry if it gaps up more than this vs the prior close. Default: NO filter.
# The walk-forward selected max_gap_up=inf in 9 of 11 folds (0.30 only in the two
# earliest, smallest-training-set folds) - see analysis/walk_forward_results.md.
# Live previously hard-coded 0.30, which vetoed trades the validated strategy took,
# and in the wrong direction: for a SHORT, a gap UP is entry at a higher price with
# more room to fall. Set MAX_GAP_UP_PCT=0.30 to restore the old behaviour if you
# want the unvalidated safety veto back.
MAX_GAP_UP_PCT = float(os.environ.get("MAX_GAP_UP_PCT", "inf"))
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
MAX_NEW_SHORTS_PER_DAY = int(os.environ.get("MAX_NEW_SHORTS_PER_DAY", "8"))
MAX_DAILY_NOTIONAL = float(os.environ.get("MAX_DAILY_NOTIONAL", "5000"))
# Annualized hard-to-borrow ceiling, in percent. Backtesting showed Strategy B still
# profitable at 200%/yr borrow, but a name that expensive is a warning sign.
MAX_HTB_RATE = float(os.environ.get("MAX_HTB_RATE", "100"))
# Skip names whose bid-ask spread exceeds this fraction of the mid. On a $0.09 stock a
# one-cent spread is ~11% - crossing it twice costs far more than the strategy's edge.
MAX_SPREAD_PCT = float(os.environ.get("MAX_SPREAD_PCT", "0.05"))
# Cap on EXPECTED borrow cost as a fraction of notional (rate x holding days/365).
# MAX_HTB_RATE alone caps the annualized rate and ignores how long the position is
# held, so a 100%/yr name costs 1.4% over 5 days and 41% over 150 - and both used
# to pass identically. 5% is ~28% of the mean trade return, permissive enough that
# it almost never binds on the typical 11-day hold but catches the long-hold tail.
MAX_BORROW_COST_PCT = float(os.environ.get("MAX_BORROW_COST_PCT", "0.05"))
# Alert when an open position's borrow rate reaches this, or this multiple of what
# it cost at entry. A mid-hold spike is the squeeze signature, and it arrives while
# the position is already moving against us.
BORROW_ALERT_RATE = float(os.environ.get("BORROW_ALERT_RATE", "100"))
BORROW_ALERT_MULTIPLE = float(os.environ.get("BORROW_ALERT_MULTIPLE", "3.0"))

# Absolute ceiling on a SINGLE position's notional, in dollars. Unset/0 = no cap.
# TRADE_PCT alone is a *proportional* cap, so the dollar size it produces drifts
# silently as account equity changes - fine at steady state, wrong when you are
# deliberately trading tiny to validate the live path. This makes "$50 a trade" mean
# $50 regardless of equity, and unlike a CLI flag it cannot be forgotten on one run.
MAX_TRADE_NOTIONAL = float(os.environ.get("MAX_TRADE_NOTIONAL", "0")) or None

# Minimum entry price. Below $1 the strategy has no MEASURED edge: across 560
# pooled out-of-sample trades the sub-$1 bucket held just 29 of them, mean
# +5.88% with a t-stat of 0.46 and a 95% CI of [-20.6%, +32.3%] - indistinguishable
# from zero. That is a sample-size problem rather than evidence of losses, but
# trading it is a bet on an unmeasured effect, and those names also carry a ~5.9x
# margin multiple (FINRA's $2.50/share floor) versus 0.4x above $1.
# Excluding them keeps 95% of backtested trades and RAISES the t-stat 14.43 -> 17.05.
MIN_ENTRY_PRICE = float(os.environ.get("MIN_ENTRY_PRICE", "1.00"))

# --- Margin (FINRA 4210(c)) ---
# Fraction of account equity allowed to be tied up in short maintenance margin.
# NOT 1.0 by default: a book requiring 100% of equity sits exactly at the margin
# call line with no buffer, so any adverse move forces a liquidation. 0.5 leaves
# room to be wrong. See src/split_strategy/margin.py for why this binds harder
# than MAX_EXPOSURE - the $2.50/share floor means a $50 short of a $0.34 stock
# needs ~$368 of margin, and ~71% of this strategy's signals price under $1.
MARGIN_EQUITY_PCT = float(os.environ.get("MARGIN_EQUITY_PCT", "0.5"))
# Broker house requirement as a multiple of the FINRA floor. Schwab reserves the
# right to raise requirements on low-priced, thinly traded or volatile securities;
# set this above 1.0 once they confirm what they actually charge.
HOUSE_MARGIN_MULTIPLE = float(os.environ.get("HOUSE_MARGIN_MULTIPLE", "1.0"))

# --- Entry timing window ---
# Strategy B enters at the OPEN. A run that fires hours late is not the trade that was
# backtested, so live sessions outside this window halt rather than trade. Scheduled
# runs were observed firing at 12:15, 17:07 and 23:32 ET when the host slept through the
# 9:25 trigger and Task Scheduler's StartWhenAvailable caught up on wake.
# Expressed as minutes either side of the 9:30 ET open: 15/15 => 09:15-09:45.
ENTRY_WINDOW_BEFORE_MIN = float(os.environ.get("ENTRY_WINDOW_BEFORE_MIN", "15"))
ENTRY_WINDOW_AFTER_MIN = float(os.environ.get("ENTRY_WINDOW_AFTER_MIN", "15"))

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

