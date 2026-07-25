# Schwab Trader API — Setup & Operation

This guide takes you from "API access approved, no keys yet" to a working **dry-run**
signal bot, and explains what live trading would require.

## What you're building
`scripts/run_signals.py` reads the confirmed upcoming splits from MongoDB
(`early_edgar_splits`), applies the chosen strategy (enter short the morning after the
announcement, exit at the open on the execution date, 40% stop, skip >30% gap-ups,
5%-equity sizing), and either **logs the orders it would place (dry-run)** or submits
them to Schwab (live).

## Hard realities (read first)
- **No paper trading.** Schwab's Trader API only connects to live accounts. "Paper"
  here = our dry-run mode (`scripts/run_signals.py` with no `--live`), which never
  contacts Schwab.
- **7-day token expiry.** Schwab refresh tokens expire every 7 days and cannot be
  extended. So `--login` must be re-run ~weekly; fully unattended live automation is
  not possible. Dry-run/signal generation has no such limit (it doesn't use Schwab).
- **Shortability.** Most reverse-split micro-caps (sub-$1, OTC, thin) are not
  shortable/marginable at Schwab. Expect many live orders to reject; that's logged,
  not an error. The recent backtest (`analysis/recent_backtest_results.md`) estimated
  ~57% of signals were unshortable.

## Step 1 — Create the app in the Schwab developer portal
1. Go to <https://developer.schwab.com/> and sign in.
2. **Create a new App**. Product: **Accounts and Trading Production** (this is what
   enables order placement; "Market Data" alone can't trade).
3. **Callback URL:** set exactly `https://127.0.0.1:8182`
   (must match `SCHWAB_CALLBACK_URL`; the `https` and the port matter).
4. Submit. Approval to *"Ready For Use"* can take a bit — the app must be **Ready For
   Use**, not just "Approved", before OAuth works.
5. Open the app and copy the **App Key** (client id) and **Secret**.

## Step 2 — Put credentials in `.env`
Add to the project `.env` (never commit it — it's gitignored):
```
SCHWAB_APP_KEY=your_app_key
SCHWAB_APP_SECRET=your_app_secret
SCHWAB_CALLBACK_URL=https://127.0.0.1:8182
# optional: pin a specific account (else the first account is used)
# SCHWAB_ACCOUNT_HASH=...
# optional: where to cache the OAuth token (default: .schwab_token.json in repo root)
# SCHWAB_TOKEN_PATH=.schwab_token.json
```

## Step 3 — First login (mint the token)
```bash
python scripts/run_signals.py --login
```
This opens a browser to Schwab, you log in and approve, and the token is cached to
`SCHWAB_TOKEN_PATH`. You'll see `Login OK` and an account HTTP 200. Re-run this weekly
(the refresh token expires every 7 days).

## Step 4 — Dry-run (safe, no Schwab needed)
```bash
python scripts/run_signals.py --min-confidence High --lookback 7
```
Prints the ranked candidate table (entry/exit dates, live price, gap-up %, share size,
40% stop, and whether each is likely shortable), constructs the SELL_SHORT orders in
**dry-run** (logged, never sent), and writes a JSON log to `logs/signals_*.json`.
Dry-run only needs MongoDB + internet.

## Step 5 — Going live (later, deliberately)
Once you've watched dry-run for a while and have a valid token:
```bash
python scripts/run_signals.py --live --min-confidence High
```
This submits real `SELL_SHORT` market orders for `ENTER_NOW`/`HOLDING` signals. Orders
that can't borrow / aren't marginable reject and are recorded (`REJECTED`) — the run
continues. Start tiny; verify one order in the Schwab UI before scaling.

## Optional — email alerts
Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL_TO` in `.env`
to get an email when there are `ENTER_NOW` signals. Unset = no-op.

## Scheduling
Dry-run + alerts can run on the existing GitHub Actions cron model
(`.github/workflows/nightly-scrape.yml`) since they don't touch Schwab. **Live** runs
cannot be fully headless because the weekly `--login` needs an interactive browser —
run those locally/attended.

## Files
- `src/split_strategy/signals/generate.py` — builds ranked signals from `early_edgar_splits`.
- `src/split_strategy/broker/schwab_auth.py` — OAuth client + token handling.
- `src/split_strategy/broker/schwab_orders.py` — order construction + dry-run/live submission.
- `scripts/run_signals.py` — the daily entrypoint.
- `src/split_strategy/config.py` — all `SCHWAB_*` and sizing settings.
