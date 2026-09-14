"""
Fast Grid & Analysis Execution Script
"""

import os
import pandas as pd
import numpy as np

def run_fast_grid():
    spx_df = pd.read_csv('backtest/data/spx_intraday.csv')
    opt_df = pd.read_parquet('backtest/data/data_opt.parquet')

    spx_df['return_open_to_entry'] = (spx_df['spx_1000'] / spx_df['spx_open']) - 1.0
    spx_df['direction'] = np.where(spx_df['return_open_to_entry'] > 0, 'PUT',
                           np.where(spx_df['return_open_to_entry'] < 0, 'CALL', 'NO_TRADE'))

    opt_1000 = opt_df[opt_df['quote_time'] == "10:00:00"]
    
    # Pre-index 10:00 option lookup
    opt_1000_dict = {}
    for (d, t), grp in opt_1000.groupby(['quote_date', 'option_type']):
        opt_1000_dict[(d, t)] = grp

    opt_by_dt = {}
    for (d, t, o_type), grp in opt_df.groupby(['quote_date', 'quote_time', 'option_type']):
        opt_by_dt[(d, t, o_type)] = grp.set_index('strike')['mid'].to_dict()

    print("Pre-indexing completed. Running parameter grid...")

    deltas = [0.15, 0.17, 0.20]
    widths = [5, 10, 15, 20, 25]
    targets = [0.50, 0.55, 0.60]

    grid_summary = []

    for d_target in deltas:
        for width in widths:
            for p_target in targets:
                net_pnls = []
                wins = 0
                losses = 0
                max_losses = []

                for idx, spx_row in spx_df.iterrows():
                    date_str = spx_row['date']
                    direction = spx_row['direction']
                    if direction == 'NO_TRADE':
                        continue
                    
                    opt_type = 'P' if direction == 'PUT' else 'C'
                    key = (date_str, opt_type)
                    if key not in opt_1000_dict:
                        continue
                        
                    chain_1000 = opt_1000_dict[key]
                    und_price = spx_row['spx_1000']

                    if direction == 'PUT':
                        sub = chain_1000[chain_1000['strike'] < und_price]
                        if sub.empty:
                            continue
                        sub_idx = (sub['delta'].abs() - d_target).abs().idxmin()
                        short_row = sub.loc[sub_idx]
                        short_strike = short_row['strike']
                        long_strike = short_strike - width
                    else:
                        sub = chain_1000[chain_1000['strike'] > und_price]
                        if sub.empty:
                            continue
                        sub_idx = (sub['delta'].abs() - d_target).abs().idxmin()
                        short_row = sub.loc[sub_idx]
                        short_strike = short_row['strike']
                        long_strike = short_strike + width

                    l_key = chain_1000[chain_1000['strike'] == long_strike]
                    if l_key.empty:
                        continue
                    long_row = l_key.iloc[0]

                    entry_credit = short_row['mid'] - long_row['mid']
                    if entry_credit <= 0:
                        continue

                    target_debit = entry_credit * (1.0 - p_target)
                    quote_times = ["10:30:00", "11:00:00", "11:30:00", "12:00:00", "12:30:00", 
                                   "13:00:00", "13:30:00", "14:00:00", "14:30:00", "15:00:00", "15:30:00"]

                    exit_spread = None
                    for t_str in quote_times:
                        k_dict = opt_by_dt.get((date_str, t_str, opt_type), {})
                        if short_strike in k_dict and long_strike in k_dict:
                            spr = k_dict[short_strike] - k_dict[long_strike]
                            if spr <= target_debit:
                                exit_spread = spr
                                break

                    if exit_spread is None:
                        spx_close = spx_row['spx_close']
                        if direction == 'PUT':
                            exit_spread = max(0.0, short_strike - spx_close) - max(0.0, long_strike - spx_close)
                        else:
                            exit_spread = max(0.0, spx_close - short_strike) - max(0.0, spx_close - long_strike)

                    gross = (entry_credit - exit_spread) * 100.0
                    cost = 2.00 + (short_row['bas'] * 0.05 * 100.0)
                    net = gross - cost
                    net_pnls.append(net)
                    if net > 0:
                        wins += 1
                    else:
                        losses += 1
                    max_losses.append((width - entry_credit) * 100.0)

                total_t = len(net_pnls)
                if total_t > 0:
                    win_pct = wins / total_t
                    sum_net = sum(net_pnls)
                    avg_w = np.mean([x for x in net_pnls if x > 0]) if wins > 0 else 0.0
                    avg_l = np.mean([x for x in net_pnls if x <= 0]) if losses > 0 else 0.0
                    gross_w = sum([x for x in net_pnls if x > 0])
                    gross_l = abs(sum([x for x in net_pnls if x <= 0]))
                    pf = (gross_w / gross_l) if gross_l > 0 else np.nan
                    
                    cum = np.cumsum(net_pnls)
                    rmax = np.maximum.accumulate(cum)
                    max_dd = np.min(cum - rmax)
                else:
                    win_pct, sum_net, avg_w, avg_l, pf, max_dd = 0, 0, 0, 0, 0, 0

                grid_summary.append({
                    'Configuration': f"{d_target:.2f}Δ / ${width} / {int(p_target*100)}%",
                    'Trades': total_t,
                    'Win %': f"{win_pct*100:.2f}%",
                    'Avg Win': f"${avg_w:.2f}",
                    'Avg Loss': f"${avg_l:.2f}",
                    'PF': f"{pf:.2f}",
                    'Net P&L': f"${sum_net:,.2f}",
                    'Max DD': f"${max_dd:,.2f}"
                })

    sum_df = pd.DataFrame(grid_summary)
    sum_df.to_csv('backtest/results/summary.csv', index=False)
    print("Saved summary.csv successfully!")

if __name__ == '__main__':
    run_fast_grid()
