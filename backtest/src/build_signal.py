"""
Phase 1: Build Daily Direction Signal Table
Computes return_open_to_entry = (SPX_entry / SPX_open) - 1
Classifies:
> 0  -> PUT credit spread
< 0  -> CALL credit spread
= 0  -> NO TRADE
"""

import pandas as pd

def build_open_to_1000_signal(spx_df):
    df = spx_df.copy()
    df['return_open_to_entry'] = (df['spx_1000'] / df['spx_open']) - 1.0
    
    def classify_direction(ret):
        if ret > 0:
            return "PUT"
        elif ret < 0:
            return "CALL"
        else:
            return "NO_TRADE"
            
    df['direction'] = df['return_open_to_entry'].apply(classify_direction)
    return df

if __name__ == '__main__':
    spx_df = pd.read_csv('backtest/data/spx_intraday.csv')
    signals = build_open_to_1000_signal(spx_df)
    print("Sample Signal Table:")
    print(signals[['date', 'spx_open', 'spx_1000', 'return_open_to_entry', 'direction']].head(10))
