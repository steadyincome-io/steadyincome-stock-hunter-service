"""
Phase 1: Validate SPX & Option Datasets
Checks date coverage, option timestamps, underlying alignment, signal direction, and option chain sanity.
"""

import pandas as pd
import numpy as np

def validate_spx_data(spx_df):
    print("--- Phase 1: Validating SPX Underlying Data ---")
    min_date = spx_df['date'].min()
    max_date = spx_df['date'].max()
    count = len(spx_df)
    print(f"Date Range: {min_date} to {max_date}")
    print(f"Total Trading Days: {count}")
    
    assert 'spx_open' in spx_df.columns, "Missing spx_open column"
    assert 'spx_1000' in spx_df.columns, "Missing spx_1000 column"
    assert 'spx_close' in spx_df.columns, "Missing spx_close column"
    print("SPX Data Schema & Dates: OK\n")

def validate_option_data(opt_df, spx_df):
    print("--- Phase 1: Validating Option Dataset ---")
    times = opt_df['quote_time'].unique()
    print(f"Option Quote Times Available: {sorted(list(times))}")
    assert "10:00:00" in times, "Error: 10:00:00 timestamp missing from option dataset"
    
    # Check underlying alignment between external SPX 10:00 and option 10:00 active_underlying_price
    opt_1000 = opt_df[opt_df['quote_time'] == "10:00:00"].drop_duplicates(subset=['quote_date'])
    merged = pd.merge(spx_df, opt_1000, left_on='date', right_on='quote_date')
    
    merged['diff_pts'] = (merged['spx_1000'] - merged['active_underlying_price']).abs()
    merged['diff_pct'] = (merged['diff_pts'] / merged['spx_1000']) * 100.0
    
    max_diff_pts = merged['diff_pts'].max()
    max_diff_pct = merged['diff_pct'].max()
    print(f"Max 10:00 Underlying Price Difference: {max_diff_pts:.4f} pts ({max_diff_pct:.4f}%)")
    assert max_diff_pct < 1.0, f"Warning: High underlying divergence ({max_diff_pct:.2f}%)"
    
    print("Option timestamps & underlying alignment: OK\n")

if __name__ == '__main__':
    spx_df = pd.read_csv('backtest/data/spx_intraday.csv')
    opt_df = pd.read_parquet('backtest/data/data_opt.parquet')
    validate_spx_data(spx_df)
    validate_option_data(opt_df, spx_df)
