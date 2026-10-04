# Summary: the reverse-split short, and why it was shut down

_Written 2026-10-03, the day the system was stopped._

## The short version

- The idea: short small companies between the announcement of a reverse stock split and the split itself.
- The live system ran from 2026-08-24 to 2026-10-03 and never opened a position.
- Tested properly, the rule it traded roughly breaks even after costs.
- The best version found makes about 10% a year on a $5,000 account, and that is not proven.
- An S&P 500 index fund made about 12% a year over the same period with far less risk and no work.
- Six other ideas were tested with the same tools. None was worth running.
- Decision: stop, and use index funds.

## What the system was

- **Filing scan.** Each morning it read new SEC filings (8-K and 6-K) and used an AI model to find definite reverse-split announcements with an effective date.
- **Trading run.** At 09:25 ET on weekdays it checked each new announcement against a set of filters and shorted the ones that passed, through Schwab.
- **The trade.** Short the day after the filing. Cover before the split. Take profit at 20%. $50 per trade, one new trade a day.
- **Where it ran.** The scan and trading ran on Modal. A backup scan and a watchdog ran on GitHub Actions. Signals and logs were stored in MongoDB.
- **Safety.** Kill switch, entry-time window, ledger checked against the broker before every run, limits on price, spread, borrow cost and margin.

## Why it never traded

- **The scan arrived late.** GitHub started the scan 4–8 hours late, after the trading run. A signal could only be entered the day after its filing, so late ones were lost.
- **Wrong tickers.** The scan picked a company's warrant or preferred ticker instead of its common stock.
- **Holidays.** Entry dates ignored market holidays.
- **A date bug.** The AI marked about 1 in 8 valid announcements as "already happened".
- **The $1 minimum price.** Most of these stocks trade under $1, so most candidates were rejected.

All of these were fixed on 2026-10-03. No market session ran between the fixes and the shutdown.

## What was built on 2026-10-03

- **A trustworthy event list.** The live AI classifier was run over every reverse-split filing from 2019 to 2026 (about 40,000 filings). Each event keeps the time the filing became public.
- **Real prices.** Unadjusted daily prices from Polygon, including delisted stocks. The old backtest used Yahoo prices, which are adjusted for later splits and made a $0.11 stock look like $0.88.
- **A new backtest.** It only counts trades that could really have been made, charges for trading costs and borrowing, and compares every result against shorting similar stocks at random on the same days.
- **Rules written down first.** Each rule and its pass mark was recorded before testing (`analysis/backtest_v2_preregistration.md`), with the last year held back as a final check.
- **Live protections.** A stop order, a post-open follow-up run, and a log of real quotes and borrow rates for every candidate.

## What the tests showed

Costs below are estimates, not measurements. "Random" means the same rule on shuffled tickers, 200 times.

| Rule | Period | Trades | Per trade before costs | After costs | Beat random? |
|---|---|---|---|---|---|
| The live rule | Dec 2021 – Sep 2025 | 73 | +3.6% | +0.9% | Yes (0 of 200 random runs matched it) |
| One-day trade the day before the split | Dec 2021 – Sep 2024 | 363 | +1.7% | −3.8% | No (139 of 200) |
| Best rule found | Dec 2021 – Sep 2025 | 214 | +12.0% | +7.6% | Yes (0 of 200), but it was chosen on this data |
| Best rule found | Held-back year, Oct 2025 – Oct 2026 | 138 | +7.9% | +3.6% | No (100 of 200) |

- **The live rule** made about +0.3% a year on a $5,000 account. It took about 19 trades a year.
- **The best rule found:** short at the first open after the filing, only stocks at $0.50 or more, cover the morning the split takes effect, no stop and no profit target.
- On the held-back year it made about +10% a year at 2% of the account per trade. At double the estimated costs it lost money.

### What was learned about these stocks

- They drop about 5% on the announcement, before anyone can trade it.
- They keep falling during trading hours until the split, then stop.
- Entering a day late costs about 2% per trade. The live system entered a day late.
- Very short trades lose to trading costs.
- Stops made every version worse.
- Stocks under $0.50 carry the large losses (worst: −334%).
- Shorting after the split does not work.

### What was wrong with the old backtest

- Its headline (+12.66% per trade) came from trying 4,620 settings and keeping the best. The same search on shuffled data gave the same number.
- For 45% of its trades, the exit date had not been published when the trade was entered.
- It filtered on adjusted prices, so its $1 minimum meant something different from the live one.

## Other ideas tested

| Idea | Result | Verdict |
|---|---|---|
| Round-up: buy one share before a split that rounds fractions up | About $3 per split, 105–135 splits a year, so $350–400 a year per account. Depends on the broker passing the rounded share through, which was not checked. The AI's round-up flag was right in 34 of 40 spot checks. | Tiny, does not grow with capital |
| Tender offers with priority for small holders | About 8 a year. Seven clean fixed-price cases in four years made about $530 in total on 99 shares each. Final outcomes were not collected. | Too rare |
| Buying when 3+ executives buy | +0.8% ahead of the S&P 500 at three months on average, but most trades lost to it; 3.1% behind at six months. | No edge |
| Buying CEO or CFO purchases | Behind the S&P 500 at every holding period. | No edge |
| Buying big jumps on big volume | 0.5% to 3.5% behind the S&P 500. | No edge |
| Momentum (last year's winners) | +11.6% a year against +19.1% for the S&P 500 over the same 33 months. | No edge |

- Benchmark, Oct 2021 – Sep 2025, price only: S&P 500 +11.7% a year, Nasdaq 100 +14.3% a year.
- In this period a few very large companies drove the market, so any rule picking from thousands of ordinary stocks started behind.
- Five years is a short history. These ideas might look better in another period, but there is no evidence here that they beat an index fund.

## Why it was shut down

- The best case is about $500 a year on $5,000, before data costs of about $350 a year.
- That best case is unproven: it passed one of its two checks and fails at higher costs.
- It needs short positions with no stop in stocks that can double overnight.
- It took real effort to keep running: a weekly broker login, monitoring, and bug fixes.
- An index fund has matched or beaten every result here.

## What was shut down, and what is left

Stopped on 2026-10-03:

- The Modal app (trading run, filing scan, follow-up run). Nothing is scheduled.
- The GitHub nightly scan and trading watchdog workflows (disabled).

Left in place, for the owner to deal with:

- **Schwab account.** No position was ever recorded by the system. This was not re-checked against the broker at shutdown; look once for open positions or orders.
- **Polygon subscription.** Cancel it. Their terms require deleting the downloaded prices: `rm -rf DATA/prices_v2`.
- **Modal.** The secret `split-strategy-secrets` and the volume `split-strategy-data` (which holds the Schwab login token) still exist.
- **MongoDB.** All collected filings and events are still there.
- **Restarting.** `modal deploy modal_app.py` would turn live trading back on. Do not run it unless that is the intent.

## Where things are

- `documents/summary.md` — this file.
- `documents/results/` — the output of every test quoted above.
- `documents/research/` — scripts for the round-up, tender-offer and long-only tests.
- `analysis/backtest_v2_preregistration.md` — the rules written down before testing, and the results.
- `analysis/deep_review_fable.md` — the 2026-09-05 audit of the old backtest.
- `scripts/backtest_v2.py`, `scripts/explore_v2.py`, `scripts/backfill_events.py` — the new backtest, the exploration, and the event backfill.
- `src/split_strategy/` — the trading system and backtest code.

Nothing here is investment advice. Every number is a backtest estimate.
