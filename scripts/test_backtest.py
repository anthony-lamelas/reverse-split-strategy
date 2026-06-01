import os
import sys
import pandas as pd
import numpy as np
from pathlib import Path
from bson.objectid import ObjectId

current_dir = Path(os.getcwd())
sys.path.append(str(current_dir / 'src'))

from split_strategy.database import get_collection, REVERSE_SPLITS_COLLECTION, EDGAR_COLLECTION

reverse_coll = get_collection(REVERSE_SPLITS_COLLECTION)
edgar_coll = get_collection(EDGAR_COLLECTION)

edgar_filings = list(edgar_coll.find({'tier': {'$in': ['A', 'B']}}))
split_ids = set([f['reverse_splits_id'] for f in edgar_filings])
object_ids = [ObjectId(sid) for sid in split_ids]
splits = list(reverse_coll.find({'_id': {'$in': object_ids}}))

prices_path = current_dir / 'data' / 'prices.pkl'
prices = pd.read_pickle(prices_path)

split_events = []
for split in splits:
    split_id = str(split['_id'])
    filings = [f for f in edgar_filings if str(f['reverse_splits_id']) == split_id]
    if not filings: continue
    ticker = split.get('Symbol')
    if not ticker: continue
    df_f = pd.DataFrame(filings)
    df_f['filing_date_obj'] = pd.to_datetime(df_f['filing_date'], errors='coerce')
    earliest_filing = df_f.loc[df_f['filing_date_obj'].idxmin()]
    t_ann = earliest_filing['filing_date_obj']
    t_split = pd.to_datetime(split['Date'], errors='coerce')
    split_events.append({'ticker': ticker, 't_ann': t_ann, 't_split': t_split, 'ratio': split.get('Split Ratio')})

df_events = pd.DataFrame(split_events).dropna(subset=['t_ann', 't_split']).sort_values('t_ann')
print(f"Total split events matched with dates: {len(df_events)}")

def backtest_strategy(entry_type='announcement', initial_capital=10000, trade_pct=0.05, stop_loss=0.20, hold_days=5, slippage_and_fees=0.015):
    portfolio_value = initial_capital
    trades = []
    equity_curve = []
    for idx, row in df_events.iterrows():
        ticker = row['ticker']
        if ticker not in prices.columns.levels[0]: continue
        ticker_data = prices[ticker]
        entry_date_target = row['t_ann'] if entry_type == 'announcement' else row['t_split']
        future_data = ticker_data[ticker_data.index >= entry_date_target]
        if future_data.empty: continue
        entry_price = future_data.iloc[0]['Open']
        entry_date_actual = future_data.index[0]
        if pd.isna(entry_price) or entry_price <= 0: continue
        exit_price, exit_date = None, None
        stop_loss_price = entry_price * (1 + stop_loss)
        holding_data = future_data.iloc[1:hold_days+1]
        for h_idx, h_row in holding_data.iterrows():
            if h_row['High'] >= stop_loss_price:
                exit_price = stop_loss_price
                exit_date = h_idx
                break
        if exit_price is None and not holding_data.empty:
            exit_price = holding_data.iloc[-1]['Open']
            exit_date = holding_data.index[-1]
        if exit_price is None: continue
        raw_pct_return = (entry_price - exit_price) / entry_price
        net_pct_return = raw_pct_return - slippage_and_fees
        profit_dollars = portfolio_value * trade_pct * net_pct_return
        portfolio_value += profit_dollars
        trades.append({'net_return': net_pct_return})
        equity_curve.append(portfolio_value)
    return pd.DataFrame(trades), pd.Series(equity_curve)

trades_ann, equity_ann = backtest_strategy('announcement')
if not trades_ann.empty:
    print(f"--- SHORT ON ANNOUNCEMENT ---")
    print(f'Trades={len(trades_ann)}, WinRate={(trades_ann.net_return > 0).mean():.2%}, Return={(equity_ann.iloc[-1]-10000)/10000:.2%}, MaxDD={((equity_ann - equity_ann.cummax()) / equity_ann.cummax()).min():.2%}')

trades_exec, equity_exec = backtest_strategy('split')
if not trades_exec.empty:
    print(f"--- SHORT ON EXECUTION ---")
    print(f'Trades={len(trades_exec)}, WinRate={(trades_exec.net_return > 0).mean():.2%}, Return={(equity_exec.iloc[-1]-10000)/10000:.2%}, MaxDD={((equity_exec - equity_exec.cummax()) / equity_exec.cummax()).min():.2%}')
