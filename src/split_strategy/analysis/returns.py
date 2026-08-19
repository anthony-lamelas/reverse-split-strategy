"""
Financial analysis and return calculations.
"""
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
import traceback

def get_stock_price_data(ticker: str, days: int = 7):
    """Get recent stock price data using yfinance"""
    try:
        ticker_obj = yf.Ticker(ticker)
        hist = ticker_obj.history(period=f"{days}d")
        
        if hist.empty:
            # Fallback to 1mo
            hist = ticker_obj.history(period="1mo")
        
        if hist.empty:
            return None
            
        return pd.DataFrame({
            'Price': hist['Close'].values
        }, index=hist.index)
        
    except Exception as e:
        print(f"Error getting stock data for {ticker}: {e}")
        return None

def get_current_price(ticker: str):
    """Get current/last available price for a ticker"""
    try:
        ticker_obj = yf.Ticker(ticker)
        # Try info first
        try:
            info = ticker_obj.info
            current_price = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('previousClose')
            if current_price:
                return float(current_price)
        except:
            pass
        
        # Fallback to history
        try:
            hist = ticker_obj.history(period="5d")
            if not hist.empty:
                return float(hist['Close'].iloc[-1])
        except:
            pass
        
        return None
    except:
        return None

def _match_index_tz(index, *stamps):
    """Coerce naive/aware datetimes to the same tz-awareness as `index`.

    yfinance returns a tz-aware index (America/New_York), while the callers here build
    plain naive datetimes from `split_date + timedelta`. Comparing the two raises
    `TypeError: Cannot compare tz-naive and tz-aware timestamps`, which the broad
    `except` at the bottom of this function swallowed into a `None, None` return - so
    a real bug looked exactly like "this ticker has no data".
    """
    out = []
    for stamp in stamps:
        ts = pd.Timestamp(stamp)
        if getattr(index, "tz", None) is not None:
            ts = ts.tz_localize(index.tz) if ts.tzinfo is None else ts.tz_convert(index.tz)
        elif ts.tzinfo is not None:
            ts = ts.tz_localize(None)
        out.append(ts)
    return out


def _bar_at_offset(hist, anchor_pos: int, trading_days: int):
    """The bar `trading_days` sessions from `anchor_pos`, or None if out of range.

    Offsets must be counted in *trading days*, not calendar days. The original code
    did `split_date - timedelta(days=20)` and then snapped to the nearest available
    bar, which made every window shorter than its label (a "20d" window spanned about
    14 sessions) and collapsed adjacent windows onto the same bar around a weekend -
    so `1d_before` and `3d_before` could report identical numbers.
    """
    pos = anchor_pos + trading_days
    if pos < 0 or pos >= len(hist.index):
        return None
    return hist.index[pos]


def get_stock_price_data_around_split(ticker: str, split_date: datetime, days_before: int = 30, days_after: int = 10):
    """Get price data around split date and calculate returns.

    Return-window keys (`5d_before`, `10d_after`, ...) are counted in TRADING days.
    """
    try:
        ticker_obj = yf.Ticker(ticker)

        # Calculate date range
        start_date = split_date - timedelta(days=days_before)
        end_date = split_date + timedelta(days=days_after)

        # Get historical data
        hist = ticker_obj.history(start=start_date, end=end_date)

        if hist.empty:
            # Try longer period. This fallback runs precisely for the thin, sparse and
            # delisted-adjacent tickers that matter most, so it must not fail silently.
            hist = ticker_obj.history(period="3mo")
            if not hist.empty:
                # Filter to our date range
                lo, hi = _match_index_tz(hist.index, start_date, end_date)
                hist = hist[(hist.index >= lo) & (hist.index <= hi)]

        if hist.empty:
            return None, None
        
        # Format for chart
        chart_df = pd.DataFrame({
            'Price': hist['Close'].values
        }, index=hist.index)
        
        # Calculate returns relative to split date
        # Find closest trading day <= split date
        split_date_only = split_date.date() if isinstance(split_date, datetime) else split_date
        split_trading_days = hist.index[hist.index.date <= split_date_only]
        
        if len(split_trading_days) == 0:
            return chart_df, None
        
        split_idx = split_trading_days[-1]
        split_price = hist.loc[split_idx, 'Close']
        # Anchor every window on this bar's POSITION, so offsets step over sessions
        # rather than calendar days and never collapse two windows onto one bar.
        split_pos = hist.index.get_loc(split_idx)

        # Calculate returns for windows before split
        return_windows = [-20, -10, -5, -3, -1]
        returns = {}

        for window in return_windows:
            lookback_idx = _bar_at_offset(hist, split_pos, window)
            if lookback_idx is not None:
                lookback_price = hist.loc[lookback_idx, 'Close']
                ret = (split_price / lookback_price - 1) * 100
                returns[f'{abs(window)}d_before'] = round(ret, 2)
            else:
                returns[f'{abs(window)}d_before'] = None

        # Calculate returns after split (if data available)
        after_split_days = hist.index[hist.index.date > split_date_only]
        if len(after_split_days) > 0:
            # Try 1d, 3d, 5d, 10d after
            for days_after_window in [1, 3, 5, 10]:
                target_idx = _bar_at_offset(hist, split_pos, days_after_window)
                if target_idx is not None:
                    target_price = hist.loc[target_idx, 'Close']
                    ret = (target_price / split_price - 1) * 100
                    returns[f'{days_after_window}d_after'] = round(ret, 2)
                else:
                    returns[f'{days_after_window}d_after'] = None
        
        returns['split_date'] = split_date.strftime('%Y-%m-%d')
        returns['split_price'] = round(split_price, 4)
        
        return chart_df, returns
        
    except Exception as e:
        # A data-availability problem and a code bug both landed here and looked
        # identical to the caller - which is how the tz-aware comparison bug hid for
        # months as "this ticker has no data". Still degrade gracefully (this feeds a
        # dashboard), but make a programming error impossible to mistake for one.
        if isinstance(e, (TypeError, AttributeError, KeyError, IndexError)):
            print(f"BUG in get_stock_price_data_around_split({ticker}): {e!r}")
            traceback.print_exc()
        else:
            print(f"Error getting price data for {ticker}: {str(e)[:200]}")
        return None, None
