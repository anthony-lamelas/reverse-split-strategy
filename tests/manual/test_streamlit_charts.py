"""
Minimal Streamlit test to check if charts display correctly
Run with: streamlit run test_streamlit_charts.py
"""

import streamlit as st
import yfinance as yf
import pandas as pd

st.title("Price Chart Test")

# Test with 2-3 tickers
test_tickers = ["AAPL", "MSFT", "QYOUF"]

for ticker in test_tickers:
    st.markdown(f"### {ticker}")
    
    try:
        # Get data
        ticker_obj = yf.Ticker(ticker)
        hist = ticker_obj.history(period="7d", timeout=10)
        
        if hist.empty:
            st.warning(f"{ticker}: No data")
        else:
            st.success(f"{ticker}: Got {len(hist)} days of data")
            
            # Try different chart formats
            st.write("**Method 1: Direct Close column**")
            try:
                st.line_chart(hist['Close'])
            except Exception as e:
                st.error(f"Error: {e}")
            
            st.write("**Method 2: DataFrame with Close**")
            try:
                chart_df = pd.DataFrame({'Price': hist['Close'].values}, index=hist.index)
                st.line_chart(chart_df)
            except Exception as e:
                st.error(f"Error: {e}")
            
            st.write("**Method 3: DataFrame with Date index**")
            try:
                chart_df = hist[['Close']].copy()
                chart_df.columns = ['Price']
                st.line_chart(chart_df)
            except Exception as e:
                st.error(f"Error: {e}")
            
            st.write("**Raw data preview:**")
            st.dataframe(hist[['Close']].tail())
            
    except Exception as e:
        st.error(f"{ticker}: {e}")
    
    st.markdown("---")

