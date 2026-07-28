"""
Direct test of yfinance API outside Streamlit environment
Tests if yfinance works correctly for known tickers
"""

import yfinance as yf
import pandas as pd
from datetime import datetime

def test_ticker_direct(ticker: str):
    """Test yfinance directly without any caching or Streamlit"""
    print(f"\n{'='*70}")
    print(f"Testing {ticker} directly with yfinance")
    print(f"{'='*70}")
    
    try:
        # Step 1: Create ticker object
        print(f"1. Creating Ticker object...")
        ticker_obj = yf.Ticker(ticker)
        print(f"   [OK] Ticker object created")
        
        # Step 2: Get history (7 days) - NO TIMEOUT
        print(f"2. Fetching 7-day history (no timeout parameter)...")
        hist_7d = ticker_obj.history(period="7d")
        print(f"   [OK] History retrieved")
        print(f"   - Shape: {hist_7d.shape}")
        print(f"   - Empty: {hist_7d.empty}")
        
        if not hist_7d.empty:
            print(f"   - Date range: {hist_7d.index[0]} to {hist_7d.index[-1]}")
            print(f"   - Columns: {list(hist_7d.columns)}")
            print(f"   - Last Close: ${hist_7d['Close'].iloc[-1]:.4f}")
            print(f"\n   Sample data (last 3 rows):")
            print(hist_7d[['Close']].tail(3))
            
            # Step 3: Format for Streamlit chart (exactly as we do in app)
            print(f"\n3. Formatting for Streamlit chart...")
            chart_df = pd.DataFrame({
                'Price': hist_7d['Close'].values
            }, index=hist_7d.index)
            print(f"   [OK] Formatted DataFrame created")
            print(f"   - Shape: {chart_df.shape}")
            print(f"   - Columns: {list(chart_df.columns)}")
            print(f"   - Index type: {type(chart_df.index)}")
            print(f"\n   Formatted data preview:")
            print(chart_df.head(3))
            print(chart_df.tail(3))
            
            return True, chart_df
        else:
            print(f"   [WARN] History is empty!")
            return False, None
            
    except Exception as e:
        print(f"   [ERROR] Error: {str(e)}")
        import traceback
        print(f"\n   Full traceback:")
        print(traceback.format_exc())
        return False, None

def main():
    print("="*70)
    print("DIRECT YFINANCE API TEST (No Streamlit)")
    print("="*70)
    
    test_tickers = ["AAPL", "MSFT"]
    
    results = {}
    for ticker in test_tickers:
        success, chart_df = test_ticker_direct(ticker)
        results[ticker] = {
            'success': success,
            'data': chart_df
        }
        print("\n")
    
    # Summary
    print("="*70)
    print("SUMMARY")
    print("="*70)
    for ticker, result in results.items():
        status = "SUCCESS" if result['success'] else "FAILED"
        print(f"{ticker}: {status}")
        if result['success'] and result['data'] is not None:
            print(f"  - Data points: {len(result['data'])}")
            print(f"  - Price range: ${result['data']['Price'].min():.2f} - ${result['data']['Price'].max():.2f}")
    
    print("\n" + "="*70)
    print("If all tests pass, yfinance is working correctly!")
    print("The issue is likely in Streamlit rendering or data flow.")
    print("="*70)

if __name__ == "__main__":
    main()

