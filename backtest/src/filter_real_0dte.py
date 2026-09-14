"""
Fast execution engine using pre-filtered 0DTE real options dataset.
"""

import os
import pandas as pd
import numpy as np

def run_fast_real_options():
    opt_file = 'backtest/data/real_spx_options_2010_2023.parquet'
    print("Pre-filtering 0DTE options from real historical dataset...")
    
    df_raw = pd.read_parquet(opt_file)
    df_0dte = df_raw[df_raw['DTE'] == 0].copy()
    df_0dte['QUOTE_DATE'] = pd.to_datetime(df_0dte['QUOTE_DATE'])
    
    # Save lightweight 0DTE dataset
    df_0dte.to_parquet('backtest/data/real_0dte_filtered.parquet', index=False)
    print(f"Saved lightweight 0DTE dataset ({len(df_0dte)} rows).")

if __name__ == '__main__':
    run_fast_real_options()
