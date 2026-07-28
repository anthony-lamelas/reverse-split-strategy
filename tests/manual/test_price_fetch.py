"""
Test script to diagnose why price charts aren't showing
Tests yfinance with 2-3 specific tickers with detailed logging
"""

import yfinance as yf
import pandas as pd
from datetime import datetime
import time

def test_ticker(ticker: str):
    """Test fetching data for a single ticker with detailed logging"""
    print(f"\n{'='*70}")
    print(f"Testing ticker: {ticker}")
    print(f"{'='*70}")
    
    try:
        # Step 1: Create ticker object
        print(f"1. Creating Ticker object for {ticker}...")
        ticker_obj = yf.Ticker(ticker)
        print(f"   [OK] Ticker object created")
        
        # Step 2: Try to get info
        print(f"2. Attempting to get ticker info...")
        try:
            info = ticker_obj.info
            print(f"   [OK] Info retrieved")
            print(f"   - Symbol: {info.get('symbol', 'N/A')}")
            print(f"   - Name: {info.get('longName', info.get('shortName', 'N/A'))}")
            print(f"   - Current Price: {info.get('currentPrice', 'N/A')}")
            print(f"   - Regular Market Price: {info.get('regularMarketPrice', 'N/A')}")
            print(f"   - Previous Close: {info.get('previousClose', 'N/A')}")
        except Exception as e:
            print(f"   [WARN] Info failed: {str(e)[:200]}")
        
        # Step 3: Try to get history (7 days)
        print(f"3. Attempting to get 7-day history...")
        try:
            hist_7d = ticker_obj.history(period="7d", timeout=10)
            print(f"   [OK] 7-day history retrieved")
            print(f"   - Shape: {hist_7d.shape}")
            print(f"   - Empty: {hist_7d.empty}")
            if not hist_7d.empty:
                print(f"   - Date range: {hist_7d.index[0]} to {hist_7d.index[-1]}")
                print(f"   - Columns: {list(hist_7d.columns)}")
                print(f"   - Last Close: ${hist_7d['Close'].iloc[-1]:.4f}")
                print(f"   - Sample data:")
                print(hist_7d[['Close']].tail(3))
            else:
                print(f"   [WARN] History is empty")
        except Exception as e:
            print(f"   [ERROR] 7-day history failed: {str(e)[:200]}")
            hist_7d = None
        
        # Step 4: Try to get history (1 month) as fallback
        if hist_7d is None or hist_7d.empty:
            print(f"4. Attempting to get 1-month history as fallback...")
            try:
                hist_1mo = ticker_obj.history(period="1mo", timeout=10)
                print(f"   [OK] 1-month history retrieved")
                print(f"   - Shape: {hist_1mo.shape}")
                print(f"   - Empty: {hist_1mo.empty}")
                if not hist_1mo.empty:
                    print(f"   - Date range: {hist_1mo.index[0]} to {hist_1mo.index[-1]}")
                    print(f"   - Last Close: ${hist_1mo['Close'].iloc[-1]:.4f}")
            except Exception as e:
                print(f"   [ERROR] 1-month history failed: {str(e)[:200]}")
        
        # Step 5: Try to get history (5 days) as another fallback
        print(f"5. Attempting to get 5-day history...")
        try:
            hist_5d = ticker_obj.history(period="5d", timeout=10)
            print(f"   [OK] 5-day history retrieved")
            print(f"   - Shape: {hist_5d.shape}")
            print(f"   - Empty: {hist_5d.empty}")
            if not hist_5d.empty:
                print(f"   - Last Close: ${hist_5d['Close'].iloc[-1]:.4f}")
        except Exception as e:
            print(f"   [ERROR] 5-day history failed: {str(e)[:200]}")
        
        return True
        
    except Exception as e:
        print(f"   [ERROR] Fatal error: {str(e)[:200]}")
        import traceback
        print(f"   Traceback: {traceback.format_exc()[:500]}")
        return False

def main():
    print("="*70)
    print("YFINANCE PRICE FETCH DIAGNOSTIC TEST")
    print("="*70)
    
    # Test with a few known tickers from the recent splits
    # Let's test with some common ones that might work
    test_tickers = [
        "AAPL",  # Known working ticker
        "MSFT",  # Known working ticker
        "QYOUF", # From the table (might be OTC)
    ]
    
    print(f"\nTesting {len(test_tickers)} tickers: {', '.join(test_tickers)}")
    
    for ticker in test_tickers:
        test_ticker(ticker)
        time.sleep(1)  # Small delay between tests
    
    print(f"\n{'='*70}")
    print("TEST COMPLETE")
    print(f"{'='*70}")

if __name__ == "__main__":
    main()

