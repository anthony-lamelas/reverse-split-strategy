# Code Validation Report

_Reverse-Split Shorting Strategy — codebase health review._
_Scope: `src/`, `scripts/`, `analysis/`, `.github/`, `tests/` (excludes `venv/`). Read-only review — no code was changed._

## How to read this
Each issue has a **severity** (Critical / High / Medium / Low) and a `file:line` anchor. "Critical/High" items are the handful that actually change your results or can take down the pipeline. Nothing here was fixed — this is a report only, per the agreed scope.

---

## TL;DR — the five that matter
1. **Backtest look-ahead entry + entry-day stop exclusion** (`scripts/test_backtest.py:49-62`, and the notebook grid) — the backtested edge is optimistic and not reproducible live.
2. **No borrow modeling + survivorship + possibly split-adjusted prices** (`scripts/test_backtest.py:21,68`) — the edge may be partly an artifact of untradeable names and adjusted price paths.
3. **EDGAR `filings.recent`-only fetch** (`src/split_strategy/edgar/processing.py:192`) — silently drops paginated filings, so the announcement date (`t_ann`) the whole strategy keys on can be wrong or missing for older/high-volume filers.
4. **Fake SEC User-Agent** (`src/split_strategy/config.py:30`, `.github/workflows/nightly-scrape.yml`) — violates SEC fair-access policy; risks an IP ban for the entire automated pipeline.
5. **Near-zero automated test coverage** — only regex helpers are tested; the scoring and backtest math have no tests.

---

## 1. Backtest correctness & realism

The core backtest logic exists in three near-identical places: the runnable prototype `scripts/test_backtest.py`, the canonical grid in `analysis/strategy.ipynb` (cell 5), and its generator `scripts/generate_notebook.py` (`code_mega`).

| # | Severity | Issue | Location |
|---|----------|-------|----------|
| 1.1 | **Critical** | **Look-ahead entry.** Entry uses the Open of the *same day* as the signal (`t_ann` = earliest EDGAR `filing_date`). 8-K/6-K filings post intraday or after the close, so that Open is not tradeable on the signal. Entry should be the *next* session's open. | `scripts/test_backtest.py:49-53`; notebook cell 5 |
| 1.2 | **High** | **Entry-day risk excluded.** `holding_data = future_data.iloc[1:hold_days+1]` skips day 0, so the stop loop never sees the entry day's High — exactly the day these microcaps squeeze hardest. Biases results favorably. | `scripts/test_backtest.py:57` |
| 1.3 | **High** | **No borrow availability or cost.** The universe is sub-$1 compliance microcaps, many hard-to-borrow or unshortable, with borrow fees that can exceed 100% annualized. Only a flat `slippage_and_fees=0.015` is modeled. | `scripts/test_backtest.py:41,68` |
| 1.4 | **High** | **Survivorship bias.** Prices come from a yfinance-derived `data/prices.pkl`, which drops delisted tickers — and reverse-split compliance names delist frequently (the losers). | `scripts/test_backtest.py:21-22` |
| 1.5 | **High** | **Possible split-adjusted price distortion.** yfinance `history()`/`download()` default to `auto_adjust=True`, retroactively scaling pre-split prices. Measuring `(entry-exit)/entry` across the split boundary on adjusted data misrepresents the tradeable path. Needs raw/unadjusted prices — verify how the pkl was built. | `scripts/test_backtest.py:21,67` |
| 1.6 | **Medium** | **"Stepped compounding" is effectively a no-op.** `strategy.md` describes a flat 5% bet held for 30 days, but the code recomputes `current_bet_size = portfolio_value * 0.05` on *every* trade; the `last_rebalance_date` 30-day check has no effect on bet size. Prose ≠ code. | notebook cell 5 / `scripts/generate_notebook.py` |
| 1.7 | **Medium** | **Optimistic stop fills.** A stop fills at exactly `stop_loss_price` whenever `High >= stop_loss_price`; a gap-open above the stop would really fill higher (worse). Gap-through losses are understated. | `scripts/test_backtest.py:59-61`; notebook cell 5 |
| 1.8 | **Medium** | **Documented vs. backtested stop mismatch.** The prototype uses a 20% stop, while the published "Optimal Safe" strategy and the dashboard sizing use 40% (`ui/dashboard.py:625`, `max_loss/0.40`). 2× difference. | `scripts/test_backtest.py:41` |
| 1.9 | **Medium** | **No max-gap guard on late entry.** `future_data[... >= entry_date_target].iloc[0]` will silently enter weeks later if the ticker was halted/data-gapped near the signal, instead of discarding the trade. | `scripts/test_backtest.py:50-53` |
| 1.10 | **Low** | **Path/case fragility.** Reads `data/prices.pkl` (lowercase) but the repo dir is `DATA/`; works on case-insensitive Windows, breaks on Linux/CI. The pkl is also not in the repo, so the script isn't runnable as-is. | `scripts/test_backtest.py:21` |

---

## 2. EDGAR processing & data derivation

| # | Severity | Issue | Location |
|---|----------|-------|----------|
| 2.1 | **High** | **`filings.recent`-only fetch.** The SEC submissions API caps `recent` (~1000 filings / ~1yr) and paginates the rest into `filings.files[]`, which is never fetched. With a `[T-180d, T+15d]` query window, the announcement filing that defines `t_ann` can be entirely missing for older/high-volume filers — corrupting the core signal. | `src/split_strategy/edgar/processing.py:192`; window in `edgar/utils.py:50-59` |
| 2.2 | **Medium** | **Return windows are calendar days mislabeled as trading days.** Keys like `20d_before`/`5d_after` use `timedelta(days=...)`, so `1d_before` can land on a weekend and resolve to the same Friday. | `src/split_strategy/analysis/returns.py:94-121` |
| 2.3 | **Medium** | **tz-aware vs tz-naive comparison bug.** Compares a tz-aware yfinance index against tz-naive datetimes, raising `TypeError` — swallowed by the surrounding bare `except`, so it silently returns `None, None`. | `src/split_strategy/analysis/returns.py:72` |
| 2.4 | **Medium** | **Ratio-orientation inconsistency across scrapers.** `stockanalysis.py:35` / `tipranks.py:35` do `.replace(" for ", " : ")` while `hedgefollow.py:64-65` parses `a:b` keeping `b>a`; `parse_sa_ratio` (`scoring.py:10-22`) assumes `num:den`. A source emitting `10:1` would be silently rejected by SA-alignment scoring. | scrapers + `edgar/scoring.py` |

---

## 3. Security / secrets / compliance

| # | Severity | Issue | Location |
|---|----------|-------|----------|
| 3.1 | **Good** | No hardcoded credentials committed. `MONGODB_URI`, `MONGODB_DATABASE`, `OPENAI_API_KEY` are env-based; `.env` is gitignored (with `!.env.example`); only public SEC ticker caches are in `DATA/`. | `config.py:19,24,38`; `.gitignore:30-34` |
| 3.2 | **Medium** | **Fake/placeholder SEC User-Agent.** Defaults to `"Split Strategy Analysis contact@splitstrategy.com"` and the CI workflow hardcodes the same. SEC fair-access requires a real declared contact; a bogus one risks an IP ban for the whole pipeline. | `config.py:30`; `.github/workflows/nightly-scrape.yml` |
| 3.3 | **Low** | **Secrets in plaintext on disk locally.** `.env` holds a live Atlas URI + OpenAI key in cleartext (not committed, but unencrypted on the machine). Acceptable for dev; worth noting. | `.env` (local) |
| 3.4 | **Low** | **`.env.example` omits keys the code needs.** Lists `MONGODB_URI` + unused `POLYGON_API_KEY`, but not `OPENAI_API_KEY`/`SEC_USER_AGENT`. | `.env.example` |

---

## 4. Robustness

| # | Severity | Issue | Location |
|---|----------|-------|----------|
| 4.1 | **Medium** | **Concurrency likely exceeds SEC rate limits.** `ThreadPoolExecutor(max_workers=5)`, each thread hammering `sec.gov` with its own retry; no global rate coordination → risks 429s/bans (SEC guidance ~10 req/s). | `scripts/scan_early_edgar.py:180` |
| 4.2 | **Medium** | **Pervasive silent `except`/`except: pass`.** Failures degrade to "no data / no match" instead of surfacing, which is what hides bugs 2.1–2.3. | `analysis/returns.py:40-53`; `edgar/processing.py:262-266`; `edgar/parsing.py:59,84`; `edgar/utils.py:43-48` |
| 4.3 | **Low/Med** | **LLM output trust.** `json.loads` on the model response (mitigated by `response_format=json_object` + try/except), but downstream assumes keys `is_future_split`/`effective_date`/`ratio` exist; model `gpt-4o-mini` hardcoded. | `edgar/llm_analysis.py:100`; `scan_early_edgar.py:93-119` |
| 4.4 | **Low** | **Retry with no backoff on thrown exceptions.** Non-final attempts hit `pass` with no delay on network exceptions (only HTTP-status paths back off). | `scripts/scan_early_edgar.py:123-127` |

---

## 5. Testing

| # | Severity | Issue | Location |
|---|----------|-------|----------|
| 5.1 | **High** | **Only one real automated test.** `tests/test_early_warning.py` asserts on regex helpers only. The other `tests/*.py` are interactive/network diagnostic scripts, not assertions. **Zero** tests for `scoring.py`, the date-window logic, `parse_sa_ratio`, or the backtest math. `pytest` isn't even a dependency. | `tests/` |
| 5.2 | **Low** | **Committed log file.** `tests/verify.log` is tracked and rewritten by `verify_imports.py:21`; should be gitignored. | `tests/verify.log` |

---

## 6. Dependencies / environment

| # | Severity | Issue | Location |
|---|----------|-------|----------|
| 6.1 | **Medium** | **No upper bounds / lockfile.** Only `>=` floors (`yfinance>=0.2.40`, `pandas>=2.0.0`). yfinance breaks its `history()`/`info` API frequently, so unpinned installs drift and silently change backtest data. | `requirements.txt` |
| 6.2 | **Low** | **Probable unused heavy deps.** `polygon-api-client`, `seaborn`, `scikit-learn`, `scipy`, `matplotlib` appear installed but aren't imported in `src/`/`scripts/` (only notebooks) — CI bloat. | `requirements.txt` |
| 6.3 | **Info** | CI pins Python 3.11; local dev is on 3.12.6. `zoneinfo` usage is fine on both. Minor version drift worth aligning. | `.github/workflows/nightly-scrape.yml` |

---

## Suggested remediation order (when you decide to fix)
1. **Trust the signal:** fix EDGAR pagination (2.1) and the look-ahead/entry-day backtest issues (1.1, 1.2) — without these, every downstream number is suspect.
2. **Make the backtest honest:** unadjusted prices (1.5), borrow/survivorship handling (1.3, 1.4), realistic stop fills (1.7). The 2-month backtest in this project's next workstream starts on this by using `auto_adjust=False` and a shortability proxy.
3. **Protect the pipeline:** real SEC User-Agent (3.2) and rate-limit coordination (4.1).
4. **Lock it down:** add `pytest` + tests for scoring and backtest math (5.1); pin dependency upper bounds (6.1).

_This report reflects the code as of branch `automation-and-backtest`. It documents issues; it does not change behavior._
