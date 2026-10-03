"""Generate daily short-candidate signals from the early_edgar_splits scanner.

Implements the chosen "Optimal Safe" strategy as a live signal:
  - Enter short the morning AFTER the SEC announcement (filing_date).
  - Exit at the open on the execution date (effective_date).
  - Skip if the stock gaps up >30% at entry (retail momentum).
  - Size at 5% of equity notional; 40% hard stop (above entry).

A signal is "actionable" when today is on/after the first business day after the
filing and strictly before the execution date. The generator ranks High-confidence,
soonest-executing names first.

Prices come from Schwab, via the `quote_fn` passed into generate_signals() - never from
yfinance. That used to be the other way round, and it cost real trades: yfinance
returns an EMPTY frame rather than an error for a throttled or unrecognised symbol, so
a name it could not price was skipped as "missing or invalid price" and looked
identical to a genuine data gap. Over the first fortnight live that was a third of all
skips, including GLTK ($4.58) and SCNI ($2.05) - both comfortably above the $1.00 floor
and both quotable at Schwab in the same run, sixteen lines later.

The module makes no network calls of its own. Its only I/O is the Mongo read of the
early_edgar_splits scanner; everything price-shaped arrives through injected callables
(`quote_fn`, and a live client for `enrich_with_schwab_shortability`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field
from typing import Callable, Optional

import pandas as pd

from ..database import get_collection, EARLY_WARNINGS_COLLECTION
from ..backtest.events import parse_ratio, _to_ts
from ..backtest.shortability import load_exchange_map, MAJOR_EXCHANGES
from ..live import calendar as mcal
from .. import config

_CONF_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass
class Signal:
    ticker: str
    company_name: Optional[str]
    filing_date: Optional[str]
    effective_date: Optional[str]        # planned exit date
    ratio: Optional[float]
    confidence: Optional[str]
    status: str                          # ENTER_NOW | UPCOMING | HOLDING
    entry_date: Optional[str]            # first business day after filing
    current_price: Optional[float] = None
    prior_close: Optional[float] = None
    gap_up_pct: Optional[float] = None
    gap_up_ok: Optional[bool] = None     # False if it gapped up beyond the filter
    exchange: Optional[str] = None
    likely_shortable: Optional[bool] = None       # our historical proxy classifier's guess
    schwab_is_shortable: Optional[bool] = None    # real, live Schwab data (None = not checked)
    schwab_is_hard_to_borrow: Optional[bool] = None
    schwab_htb_rate: Optional[float] = None       # annualized borrow fee %, from Schwab
    notional: Optional[float] = None
    shares: Optional[int] = None
    stop_price: Optional[float] = None
    max_loss: Optional[float] = None
    capital_ok: Optional[bool] = None    # False = would exceed the max-exposure cap given
                                          # capital already committed to other open positions
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _next_business_day(ts: pd.Timestamp) -> pd.Timestamp:
    """The next market session after `ts` - weekends AND exchange holidays skipped.

    Skipping weekends only put the entry date of a filing made on the Friday before
    Labor Day on the holiday itself. The live run does nothing on a closed market, and
    by Tuesday the signal was HOLDING, which never opens a position - so the trade was
    lost without a single filter rejecting it.
    """
    return mcal.next_trading_day(ts)


def apply_pricing(
    sig: "Signal",
    current_price: Optional[float],
    prior_close: Optional[float],
    *,
    account_size: float,
    trade_pct: float,
    max_trade_notional: Optional[float],
    stop_loss: float,
    max_gap_up: float,
) -> "Signal":
    """Size a signal from a price, in place. Pure: no network, no database.

    Extracted from `generate_signals` so the arithmetic that decides share counts on a
    real short book can be tested directly. Every caller previously reached it only
    through a live yfinance call and a Mongo query, which is why it had no coverage at
    all.

    A missing or non-positive `current_price` leaves `shares` unset rather than zero,
    which is what routes the signal to the "missing or invalid price" block reason in
    `schwab_orders.entry_block_reason` instead of the "buys 0 shares" one. Those two
    look identical to a reader and mean completely different things - a data outage
    versus a sizing choice - so the distinction is load-bearing.
    """
    sig.current_price, sig.prior_close = current_price, prior_close

    if current_price and prior_close and prior_close > 0:
        gap = (current_price - prior_close) / prior_close
        sig.gap_up_pct = round(gap * 100, 2)
        sig.gap_up_ok = gap <= max_gap_up
        if not sig.gap_up_ok:
            sig.notes.append(
                f"SKIP: gapped up {sig.gap_up_pct:.1f}% (> {max_gap_up*100:.0f}%)")

    if current_price and current_price > 0:
        notional = account_size * trade_pct
        if max_trade_notional:
            # Absolute dollar ceiling, applied after the proportional size so a
            # growing account cannot silently scale up a deliberately tiny
            # validation position.
            notional = min(notional, max_trade_notional)
        sig.notional = round(notional, 2)
        sig.shares = int(math.floor(notional / current_price))
        sig.stop_price = round(current_price * (1 + stop_loss), 4)
        sig.max_loss = round(notional * stop_loss, 2)
        if current_price < 1.0:
            # Sub-$1 is generally non-marginable / not shortable regardless of venue.
            sig.likely_shortable = False
            sig.notes.append(
                "entry < $1.00 - typically non-marginable / not shortable")
    return sig


def generate_signals(
    account_size: Optional[float] = None,
    as_of: Optional[pd.Timestamp] = None,
    min_confidence: str = "High",
    lookback_days: int = 7,
    trade_pct: float = None,
    stop_loss: float = None,
    max_gap_up: float = None,
    quote_fn: Optional[Callable[[list[str]], dict]] = None,
    existing_committed: float = 0.0,
    max_exposure: Optional[float] = None,
    max_trade_notional: Optional[float] = None,
) -> list[Signal]:
    """Build ranked short-candidate signals actionable around `as_of`.

    Args:
        account_size: equity used for position sizing (defaults to config).
        as_of: reference "today" (defaults to now).
        min_confidence: minimum scanner confidence (High/Medium/Low).
        lookback_days: how many days back a filing can be and still be actionable.
        quote_fn: called once with the ENTER_NOW tickers, returning {TICKER: Quote}.
            Supplies the price for sizing and the $1.00 floor (the BID, since a short
            sells into it) and the prior close for the gap-up filter. Pass
            `lambda ts: get_quotes(client, ts)` in the live path. When None, no signal
            is priced and every one is left unsized - callers must treat that as a
            failure, not as a day with no candidates.
        existing_committed: notional $ already tied up in other still-open positions
            (from the portfolio ledger) - counted against the exposure cap before any
            new signal here is allocated capital.
        max_exposure: cap on TOTAL committed notional as a fraction of account_size
            (defaults to config.MAX_EXPOSURE). Signals are allocated capital in ranked
            order (actionable-now, then confidence, then soonest execution); once the
            cap is hit, remaining signals are flagged capital_ok=False and excluded
            from order construction, not silently over-allocated.
    """
    account_size = config.DEFAULT_ACCOUNT_SIZE if account_size is None else account_size
    trade_pct = config.TRADE_PCT if trade_pct is None else trade_pct
    max_trade_notional = (config.MAX_TRADE_NOTIONAL if max_trade_notional is None
                          else max_trade_notional)
    stop_loss = config.STOP_LOSS_PCT if stop_loss is None else stop_loss
    max_gap_up = config.MAX_GAP_UP_PCT if max_gap_up is None else max_gap_up
    max_exposure = config.MAX_EXPOSURE if max_exposure is None else max_exposure
    as_of = pd.Timestamp.now().normalize() if as_of is None else pd.Timestamp(as_of).normalize()
    min_rank = _CONF_RANK.get(min_confidence.lower(), 2)

    exchange_map = load_exchange_map()
    docs = list(get_collection(EARLY_WARNINGS_COLLECTION).find({}))

    signals: list[Signal] = []
    for d in docs:
        ticker = d.get("ticker")
        if not ticker or ticker == "UNKNOWN":
            continue
        if _CONF_RANK.get(str(d.get("confidence", "")).lower(), -1) < min_rank:
            continue

        t_ann = _to_ts(d.get("filing_date"))
        t_split = _to_ts(d.get("effective_date"))
        if pd.isna(t_ann) or pd.isna(t_split) or t_ann >= t_split:
            continue
        # Trade window must still be open (execution in the future) ...
        if t_split <= as_of:
            continue
        # ... and the announcement recent enough to still be acting on.
        if t_ann < as_of - pd.Timedelta(days=lookback_days):
            continue

        entry_date = _next_business_day(t_ann)
        if as_of < entry_date:
            status = "UPCOMING"
        elif as_of == entry_date:
            status = "ENTER_NOW"
        else:
            status = "HOLDING"  # already past ideal entry but trade window still open

        exch = exchange_map.get(ticker.upper())
        likely_shortable = (exch or "").lower() in MAJOR_EXCHANGES

        sig = Signal(
            ticker=ticker,
            company_name=d.get("company_name"),
            filing_date=str(d.get("filing_date")),
            effective_date=str(d.get("effective_date")),
            ratio=parse_ratio(d.get("ratio")),
            confidence=d.get("confidence"),
            status=status,
            entry_date=str(entry_date.date()),
            exchange=exch,
            likely_shortable=bool(likely_shortable),
        )
        if not likely_shortable:
            sig.notes.append(f"likely NOT shortable at Schwab (exchange={exch or 'unlisted'})")

        signals.append(sig)

    # Price in ONE batched call, after the loop.
    #
    # ENTER_NOW *and* HOLDING, because those are exactly the two statuses
    # `allocate_capital` considers - and it skips any signal without shares. Quoting
    # only ENTER_NOW would leave every HOLDING name unpriced and therefore silently
    # unable to consume exposure budget, which is a change to how capital is rationed
    # rather than a change of price source. UPCOMING names are excluded: allocation
    # ignores them and they cannot be entered.
    #
    # This is deliberately wider than `session.collect_quote_tickers`, which needs
    # quotes only for orders. That function selects on status rather than on shares,
    # which is how Schwab came to be quoting the very tickers yfinance had just
    # declared priceless, sixteen lines further down the same run.
    if quote_fn is not None:
        priceable = ("ENTER_NOW", "HOLDING")
        tickers = sorted({s.ticker.upper() for s in signals
                          if s.status in priceable and s.ticker})
        quotes = quote_fn(tickers) if tickers else {}
        for s in signals:
            q = quotes.get((s.ticker or "").upper())
            # No quote leaves the signal unpriced, which routes it to the "missing or
            # invalid price" block reason - now truthfully about Schwab.
            apply_pricing(s, q.entry_price if q else None,
                          q.prev_close if q else None,
                          account_size=account_size, trade_pct=trade_pct,
                          max_trade_notional=max_trade_notional,
                          stop_loss=stop_loss, max_gap_up=max_gap_up)

    # Both depend on `shares`, so they must run after pricing.
    rank_signals(signals)
    allocate_capital(signals, account_size, max_exposure, existing_committed)
    return signals


def rank_signals(signals: list[Signal]) -> list[Signal]:
    """Sort in place: actionable-now first, then confidence, then soonest execution.

    Ranking determines who gets scarce capital first in `allocate_capital`, so the
    order is a risk decision, not cosmetics.
    """
    status_rank = {"ENTER_NOW": 0, "HOLDING": 1, "UPCOMING": 2}
    signals.sort(key=lambda s: (
        status_rank.get(s.status, 3),
        -_CONF_RANK.get((s.confidence or "").lower(), -1),
        s.effective_date or "9999",
    ))
    return signals


def allocate_capital(
    signals: list[Signal],
    account_size: float,
    max_exposure: float,
    existing_committed: float = 0.0,
) -> float:
    """Consume the exposure budget in ranked order, flagging `capital_ok` per signal.

    A signal that would push total committed notional over the cap is marked
    `capital_ok=False` rather than sized anyway — this is what prevents the
    "unlimited buying power" bug, where every signal got a fresh slice of total equity
    regardless of what was already committed to other open positions (trades routinely
    overlap; see docs/VALIDATION_REPORT.md).

    Signals already excluded by another filter (no shares, gap-up rejected, not yet at
    entry) must not consume budget — otherwise a trade we were never going to place
    would crowd out one we would.

    Returns the total committed notional after allocation.
    """
    exposure_cap = max_exposure * account_size
    committed = existing_committed
    for s in signals:
        if s.status not in ("ENTER_NOW", "HOLDING"):
            continue
        if not s.shares or s.shares <= 0 or s.gap_up_ok is False:
            continue
        if committed + (s.notional or 0) <= exposure_cap:
            s.capital_ok = True
            committed += (s.notional or 0)
        else:
            s.capital_ok = False
            s.notes.append(f"SKIPPED: capital constrained (committed ${committed:,.0f} "
                          f"of ${exposure_cap:,.0f} cap)")
    return committed


def enrich_with_schwab_shortability(signals: list[Signal], client) -> list[Signal]:
    """Fill in real Schwab shortability data (requires an authenticated client).

    Best-effort: on any failure a signal's schwab_* fields just stay None (unknown),
    never silently treated as shortable or unshortable.
    """
    from ..broker.schwab_market_data import get_shortability_batch

    tickers = [s.ticker for s in signals if s.ticker]
    if not tickers:
        return signals
    results = get_shortability_batch(client, tickers)
    for s in signals:
        r = results.get(s.ticker)
        if not r:
            continue
        s.schwab_is_shortable = r.get("is_shortable")
        s.schwab_is_hard_to_borrow = r.get("is_hard_to_borrow")
        s.schwab_htb_rate = r.get("htb_rate")
    return signals


def log_shortability_ground_truth(signals: list[Signal], log_path) -> int:
    """Append a row per signal with schwab_* data populated to a growing CSV, building
    a real ground-truth dataset over time to validate the historical proxy classifier
    in backtest/shortability.py. Returns the number of rows appended (0 if none had
    Schwab data checked).

    At most ONE row per ticker per day. The scheduled task can fire more than once
    across the entry window, and each repeat used to append another identical row -
    inflating the very dataset this exists to build. An accuracy figure computed from
    it would then be weighted by how often the scheduler happened to run rather than
    by distinct observations, which is worse than having less data.
    """
    import csv
    from pathlib import Path

    rows = [s for s in signals if s.schwab_is_shortable is not None]
    if not rows:
        return 0

    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not log_path.exists()
    fieldnames = ["logged_at", "ticker", "confidence", "entry_price", "exchange",
                 "proxy_likely_shortable", "schwab_is_shortable", "schwab_is_hard_to_borrow",
                 "schwab_htb_rate"]

    now_ts = pd.Timestamp.now()
    today = now_ts.strftime("%Y-%m-%d")
    if not is_new:
        seen_today = set()
        try:
            with open(log_path, newline="", encoding="utf-8") as f:
                for existing in csv.DictReader(f):
                    if (existing.get("logged_at") or "")[:10] == today:
                        seen_today.add((existing.get("ticker") or "").upper())
        except OSError:
            # Unreadable log: fall through and append. A duplicate row is a far
            # smaller problem than dropping the observation entirely.
            seen_today = set()
        rows = [s for s in rows if (s.ticker or "").upper() not in seen_today]
        if not rows:
            return 0

    with open(log_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if is_new:
            w.writeheader()
        now = now_ts.isoformat()
        for s in rows:
            w.writerow(dict(
                logged_at=now, ticker=s.ticker, confidence=s.confidence,
                entry_price=s.current_price, exchange=s.exchange,
                proxy_likely_shortable=s.likely_shortable,
                schwab_is_shortable=s.schwab_is_shortable,
                schwab_is_hard_to_borrow=s.schwab_is_hard_to_borrow,
                schwab_htb_rate=s.schwab_htb_rate,
            ))
    return len(rows)
