"""
Phase 2: Short and Long Strike Selection
Selects 0DTE OTM options based on target delta (baseline 0.17) and spread width.
Enforces strict rules:
- OTM check
- Exact spread width match (no silent width alteration)
"""

import pandas as pd

def select_spread_legs(chain_1000, direction, target_delta=0.17, width=10):
    """
    chain_1000: DataFrame of 10:00:00 options for a single date
    direction: 'PUT' or 'CALL'
    target_delta: target absolute delta (e.g. 0.17)
    width: spread width in index points (e.g. 10)
    """
    if chain_1000.empty or direction == "NO_TRADE":
        return None

    und_price = chain_1000['active_underlying_price'].iloc[0]

    if direction == "PUT":
        # Put Credit Spread: sell higher-strike OTM put, buy lower-strike put
        # OTM puts have strike < und_price
        puts = chain_1000[(chain_1000['option_type'] == 'P') & (chain_1000['strike'] < und_price)].copy()
        if puts.empty:
            return None
        
        puts['abs_delta'] = puts['delta'].abs()
        puts['delta_diff'] = (puts['abs_delta'] - target_delta).abs()
        
        # Select short put closest to target_delta
        short_row = puts.sort_values('delta_diff').iloc[0]
        short_strike = short_row['strike']
        
        long_strike = short_strike - width
        long_row = chain_1000[(chain_1000['option_type'] == 'P') & (chain_1000['strike'] == long_strike)]
        
        if long_row.empty:
            # Missing long strike -> INVALID SPREAD
            return None
            
        return {
            'direction': 'PUT',
            'short_leg': short_row.to_dict(),
            'long_leg': long_row.iloc[0].to_dict(),
            'short_strike': short_strike,
            'long_strike': long_strike,
            'spread_width': width
        }

    elif direction == "CALL":
        # Call Credit Spread: sell lower-strike OTM call, buy higher-strike call
        # OTM calls have strike > und_price
        calls = chain_1000[(chain_1000['option_type'] == 'C') & (chain_1000['strike'] > und_price)].copy()
        if calls.empty:
            return None
            
        calls['abs_delta'] = calls['delta'].abs()
        calls['delta_diff'] = (calls['abs_delta'] - target_delta).abs()
        
        # Select short call closest to target_delta
        short_row = calls.sort_values('delta_diff').iloc[0]
        short_strike = short_row['strike']
        
        long_strike = short_strike + width
        long_row = chain_1000[(chain_1000['option_type'] == 'C') & (chain_1000['strike'] == long_strike)]
        
        if long_row.empty:
            # Missing long strike -> INVALID SPREAD
            return None
            
        return {
            'direction': 'CALL',
            'short_leg': short_row.to_dict(),
            'long_leg': long_row.iloc[0].to_dict(),
            'short_strike': short_strike,
            'long_strike': long_strike,
            'spread_width': width
        }

    return None
