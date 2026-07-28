"""
Test yfinance for specific tickers: HPP, TSORF, LGHL, PHGE
"""

import yfinance as yf
import pandas as pd
from datetime import datetime

def test_ticker(ticker: str):
    """Test a single ticker with yfinance"""
    print(f"\n{'='*70}")
    print(f"Testing: {ticker}")
    print(f"{'='*70}")
    
    try:
        # Create ticker object
        print(f"1. Creating Ticker object...")
        ticker_obj = yf.Ticker(ticker)
        print(f"   [OK] Ticker object created")
        
        # Try to get info first
        print(f"2. Getting ticker info...")
        try:
            info = ticker_obj.info
            print(f"   [OK] Info retrieved")
            print(f"   - Symbol: {info.get('symbol', 'N/A')}")
            print(f"   - Name: {info.get('longName', info.get('shortName', 'N/A'))}")
            print(f"   - Exchange: {info.get('exchange', 'N/A')}")
            print(f"   - Quote Type: {info.get('quoteType', 'N/A')}")
        except Exception as e:
            print(f"   [WARN] Info failed: {str(e)[:200]}")
        
        # Try 7-day history
        print(f"3. Fetching 7-day history...")
        try:
            hist_7d = ticker_obj.history(period="7d")
            print(f"   [OK] 7-day history retrieved")
            print(f"   - Empty: {hist_7d.empty}")
            print(f"   - Shape: {hist_7d.shape}")
            
            if not hist_7d.empty:
                print(f"   - Date range: {hist_7d.index[0]} to {hist_7d.index[-1]}")
                print(f"   - Last Close: ${hist_7d['Close'].iloc[-1]:.4f}")
                print(f"\n   Sample data:")
                print(hist_7d[['Close']].tail(3))
                return True, hist_7d
            else:
                print(f"   [WARN] 7-day history is empty")
        except Exception as e:
            print(f"   [ERROR] 7-day history failed: {str(e)[:200]}")
        
        # Try 1-month history as fallback
        print(f"4. Trying 1-month history...")
        try:
            hist_1mo = ticker_obj.history(period="1mo")
            print(f"   [OK] 1-month history retrieved")
            print(f"   - Empty: {hist_1mo.empty}")
            print(f"   - Shape: {hist_1mo.shape}")
            
            if not hist_1mo.empty:
                print(f"   - Date range: {hist_1mo.index[0]} to {hist_1mo.index[-1]}")
                print(f"   - Last Close: ${hist_1mo['Close'].iloc[-1]:.4f}")
                return True, hist_1mo
            else:
                print(f"   [WARN] 1-month history is empty")
        except Exception as e:
            print(f"   [ERROR] 1-month history failed: {str(e)[:200]}")
        
        # Try 5-day history
        print(f"5. Trying 5-day history...")
        try:
            hist_5d = ticker_obj.history(period="5d")
            print(f"   [OK] 5-day history retrieved")
            print(f"   - Empty: {hist_5d.empty}")
            print(f"   - Shape: {hist_5d.shape}")
            
            if not hist_5d.empty:
                print(f"   - Last Close: ${hist_5d['Close'].iloc[-1]:.4f}")
                return True, hist_5d
        except Exception as e:
            print(f"   [ERROR] 5-day history failed: {str(e)[:200]}")
        
        return False, None
        
    except Exception as e:
        print(f"   [ERROR] Fatal error: {str(e)}")
        import traceback
        print(f"   Traceback: {traceback.format_exc()[:500]}")
        return False, None

def main():
    print("="*70)
    print("YFINANCE TEST FOR SPECIFIC TICKERS")
    print("="*70)
    
    test_tickers = ["HPP", "TSORF", "LGHL", "PHGE"]
    
    results = {}
    for ticker in test_tickers:
        success, data = test_ticker(ticker)
        results[ticker] = {
            'success': success,
            'has_data': data is not None and not data.empty if data is not None else False,
            'data_points': len(data) if data is not None and not data.empty else 0
        }
    
    # Summary
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    for ticker, result in results.items():
        if result['success'] and result['has_data']:
            status = f"SUCCESS ({result['data_points']} data points)"
        elif result['success']:
            status = "SUCCESS BUT EMPTY DATA"
        else:
            status = "FAILED"
        print(f"{ticker:10} : {status}")
    
    print("\n" + "="*70)

if __name__ == "__main__":
    main()

