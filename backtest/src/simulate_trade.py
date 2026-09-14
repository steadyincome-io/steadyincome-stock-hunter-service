"""
Phases 3, 4 & 5: Trade Simulation and Transaction Costs
Simulates entry credit, intraday 30-min profit target checks (baseline 55%),
expiration settlement, transaction costs modeling, and exact return-on-risk metrics.
"""

import pandas as pd
import numpy as np

def simulate_trade(date_str, spx_row, day_opt_df, spread_info, profit_target_pct=0.55, commission_per_leg=0.50, slippage_pct=0.05):
    """
    spx_row: row from spx_intraday with open, 1000, close
    day_opt_df: all 30-min option quote rows for the date
    spread_info: dictionary from select_spread.py
    profit_target_pct: profit target fraction (e.g. 0.55 = 55% credit capture)
    """
    if not spread_info:
        return {
            'date': date_str,
            'entry_time': '10:00:00',
            'spx_open': spx_row['spx_open'],
            'spx_entry': spx_row['spx_1000'],
            'open_to_entry_return': (spx_row['spx_1000'] / spx_row['spx_open']) - 1.0,
            'direction': 'NO_TRADE',
            'short_strike': np.nan,
            'long_strike': np.nan,
            'actual_short_delta': np.nan,
            'spread_width': np.nan,
            'short_mid': np.nan,
            'long_mid': np.nan,
            'entry_credit': np.nan,
            'profit_target_pct': profit_target_pct,
            'target_exit_price': np.nan,
            'exit_time': None,
            'exit_spread_price': np.nan,
            'exit_reason': 'NO_SIGNAL',
            'gross_pnl': 0.0,
            'transaction_cost': 0.0,
            'net_pnl': 0.0,
            'max_profit': 0.0,
            'max_loss': 0.0,
            'return_on_risk': 0.0,
            'win': False
        }

    short_leg = spread_info['short_leg']
    long_leg = spread_info['long_leg']
    direction = spread_info['direction']
    width = spread_info['spread_width']

    short_mid = short_leg['mid']
    long_mid = long_leg['mid']
    
    # Gross entry credit at mid
    entry_credit = short_mid - long_mid

    if entry_credit <= 0:
        return {
            'date': date_str,
            'entry_time': '10:00:00',
            'spx_open': spx_row['spx_open'],
            'spx_entry': spx_row['spx_1000'],
            'open_to_entry_return': (spx_row['spx_1000'] / spx_row['spx_open']) - 1.0,
            'direction': direction,
            'short_strike': spread_info['short_strike'],
            'long_strike': spread_info['long_strike'],
            'actual_short_delta': short_leg['delta'],
            'spread_width': width,
            'short_mid': short_mid,
            'long_mid': long_mid,
            'entry_credit': entry_credit,
            'profit_target_pct': profit_target_pct,
            'target_exit_price': np.nan,
            'exit_time': '10:00:00',
            'exit_spread_price': np.nan,
            'exit_reason': 'INVALID_SPREAD',
            'gross_pnl': 0.0,
            'transaction_cost': 0.0,
            'net_pnl': 0.0,
            'max_profit': 0.0,
            'max_loss': 0.0,
            'return_on_risk': 0.0,
            'win': False
        }

    # Target closing debit: credit * (1 - profit_target_pct)
    target_closing_debit = entry_credit * (1.0 - profit_target_pct)

    # Intraday 30-min exit simulation
    quote_times = ["10:30:00", "11:00:00", "11:30:00", "12:00:00", "12:30:00", 
                   "13:00:00", "13:30:00", "14:00:00", "14:30:00", "15:00:00", "15:30:00"]

    exit_time = None
    exit_spread_price = None
    exit_reason = None

    opt_type = 'P' if direction == 'PUT' else 'C'

    for t_str in quote_times:
        t_chain = day_opt_df[(day_opt_df['quote_time'] == t_str) & (day_opt_df['option_type'] == opt_type)]
        s_row = t_chain[t_chain['strike'] == spread_info['short_strike']]
        l_row = t_chain[t_chain['strike'] == spread_info['long_strike']]

        if not s_row.empty and not l_row.empty:
            s_mid = s_row.iloc[0]['mid']
            l_mid = l_row.iloc[0]['mid']
            current_spread_price = s_mid - l_mid
            
            # Check if closing debit is at or below target
            if current_spread_price <= target_closing_debit:
                exit_time = t_str
                exit_spread_price = current_spread_price
                exit_reason = 'PROFIT_TARGET'
                break

    # If profit target never hit intraday, hold until expiration (16:00 close)
    if exit_reason is None:
        exit_time = '16:00:00'
        spx_close = spx_row['spx_close']
        
        if direction == 'PUT':
            # Put spread payoff at expiration
            short_payoff = max(0.0, spread_info['short_strike'] - spx_close)
            long_payoff = max(0.0, spread_info['long_strike'] - spx_close)
            expiration_loss = short_payoff - long_payoff
        else:
            # Call spread payoff at expiration
            short_payoff = max(0.0, spx_close - spread_info['short_strike'])
            long_payoff = max(0.0, spx_close - spread_info['long_strike'])
            expiration_loss = short_payoff - long_payoff

        exit_spread_price = expiration_loss
        exit_reason = 'EXPIRATION'

    # Index points PnL & Dollar PnL ($100 multiplier)
    gross_pnl_pts = entry_credit - exit_spread_price
    gross_pnl_dollars = gross_pnl_pts * 100.0

    # Model Transaction Costs: commission ($0.50/contract/leg * 4 legs total entry+exit = $2.00)
    # Plus bid/ask slippage based on dataset bas attribute
    bas_entry = short_leg['bas']
    slippage_cost = (bas_entry * slippage_pct) * 100.0  # scaled to dollars
    total_commission = commission_per_leg * 4.0        # 2 legs entry + 2 legs exit
    transaction_cost = total_commission + slippage_cost

    net_pnl_dollars = gross_pnl_dollars - transaction_cost

    max_profit_dollars = entry_credit * 100.0
    max_loss_dollars = (width - entry_credit) * 100.0
    return_on_risk = (net_pnl_dollars / max_loss_dollars) if max_loss_dollars > 0 else 0.0

    win = net_pnl_dollars > 0

    return {
        'date': date_str,
        'entry_time': '10:00:00',
        'spx_open': spx_row['spx_open'],
        'spx_entry': spx_row['spx_1000'],
        'open_to_entry_return': (spx_row['spx_1000'] / spx_row['spx_open']) - 1.0,
        'direction': direction,
        'short_strike': spread_info['short_strike'],
        'long_strike': spread_info['long_strike'],
        'actual_short_delta': short_leg['delta'],
        'spread_width': width,
        'short_mid': short_mid,
        'long_mid': long_mid,
        'entry_credit': entry_credit,
        'profit_target_pct': profit_target_pct,
        'target_exit_price': target_closing_debit,
        'exit_time': exit_time,
        'exit_spread_price': exit_spread_price,
        'exit_reason': exit_reason,
        'gross_pnl': gross_pnl_dollars,
        'transaction_cost': transaction_cost,
        'net_pnl': net_pnl_dollars,
        'max_profit': max_profit_dollars,
        'max_loss': max_loss_dollars,
        'return_on_risk': return_on_risk,
        'win': win
    }
