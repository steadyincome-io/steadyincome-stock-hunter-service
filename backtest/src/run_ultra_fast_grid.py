"""
Ultra Fast Pre-calculated Grid Solver
"""

import os
import pandas as pd
import numpy as np

def run_ultra_fast():
    print("Loading data...")
    spx_df = pd.read_csv('backtest/data/spx_intraday.csv')
    opt_df = pd.read_parquet('backtest/data/data_opt.parquet')

    spx_df['return_open_to_entry'] = (spx_df['spx_1000'] / spx_df['spx_open']) - 1.0
    spx_df['direction'] = np.where(spx_df['return_open_to_entry'] > 0, 'PUT',
                           np.where(spx_df['return_open_to_entry'] < 0, 'CALL', 'NO_TRADE'))

    # Build multi-index dict for O(1) lookup
    print("Indexing options by date, time, type, strike...")
    
    # 10:00 chain lookup
    opt_1000 = opt_df[opt_df['quote_time'] == "10:00:00"]
    
    # Dictionary structure: opt_1000_map[(date, opt_type)] = list of dicts/DataFrame
    chain_1000_map = {}
    for (d, o_type), grp in opt_1000.groupby(['quote_date', 'option_type']):
        chain_1000_map[(d, o_type)] = grp[['strike', 'mid', 'delta', 'bas']].to_dict('records')

    # Intraday quote lookup: mid_map[(date, time, opt_type, strike)] = mid
    opt_intraday = opt_df[opt_df['quote_time'] != "10:00:00"]
    mid_map = {}
    for row in opt_intraday[['quote_date', 'quote_time', 'option_type', 'strike', 'mid']].itertuples(index=False):
        mid_map[(row[0], row[1], row[2], row[3])] = row[4]

    print("Indexing done. Iterating over grid parameters...")

    deltas = [0.15, 0.17, 0.20]
    widths = [5, 10, 15, 20, 25]
    targets = [0.50, 0.55, 0.60]
    quote_times = ["10:30:00", "11:00:00", "11:30:00", "12:00:00", "12:30:00", 
                   "13:00:00", "13:30:00", "14:00:00", "14:30:00", "15:00:00", "15:30:00"]

    grid_results = []

    for d_target in deltas:
        for width in widths:
            for p_target in targets:
                net_pnls = []
                wins = 0
                losses = 0

                for spx_row in spx_df.itertuples(index=False):
                    date_str = spx_row.date
                    direction = spx_row.direction
                    if direction == 'NO_TRADE':
                        continue

                    opt_type = 'P' if direction == 'PUT' else 'C'
                    key_1000 = (date_str, opt_type)
                    if key_1000 not in chain_1000_map:
                        continue

                    chain_records = chain_1000_map[key_1000]
                    und_price = spx_row.spx_1000

                    # Filter OTM and find closest delta
                    if direction == 'PUT':
                        otm = [r for r in chain_records if r['strike'] < und_price]
                    else:
                        otm = [r for r in chain_records if r['strike'] > und_price]

                    if not otm:
                        continue

                    short_leg = min(otm, key=lambda r: abs(abs(r['delta']) - d_target))
                    short_strike = short_leg['strike']
                    long_strike = (short_strike - width) if direction == 'PUT' else (short_strike + width)

                    # Find long leg in 10:00 chain
                    long_leg = next((r for r in chain_records if r['strike'] == long_strike), None)
                    if not long_leg:
                        continue

                    entry_credit = short_leg['mid'] - long_leg['mid']
                    if entry_credit <= 0:
                        continue

                    target_debit = entry_credit * (1.0 - p_target)

                    exit_spread = None
                    for t_str in quote_times:
                        s_mid = mid_map.get((date_str, t_str, opt_type, short_strike))
                        l_mid = mid_map.get((date_str, t_str, opt_type, long_strike))
                        if s_mid is not None and l_mid is not None:
                            spr = s_mid - l_mid
                            if spr <= target_debit:
                                exit_spread = spr
                                break

                    if exit_spread is None:
                        spx_close = spx_row.spx_close
                        if direction == 'PUT':
                            exit_spread = max(0.0, short_strike - spx_close) - max(0.0, long_strike - spx_close)
                        else:
                            exit_spread = max(0.0, spx_close - short_strike) - max(0.0, spx_close - long_strike)

                    gross = (entry_credit - exit_spread) * 100.0
                    cost = 2.00 + (short_leg['bas'] * 0.05 * 100.0)
                    net = gross - cost
                    net_pnls.append(net)
                    if net > 0:
                        wins += 1
                    else:
                        losses += 1

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

                is_baseline = (d_target == 0.17 and width == 10 and p_target == 0.55)
                cfg_str = f"**{d_target:.2f}Δ / ${width} / {int(p_target*100)}%**" if is_baseline else f"{d_target:.2f}Δ / ${width} / {int(p_target*100)}%"

                grid_results.append({
                    'Configuration': cfg_str,
                    'Trades': total_t,
                    'Win %': f"{win_pct*100:.2f}%",
                    'Avg Win': f"${avg_w:.2f}",
                    'Avg Loss': f"${avg_l:.2f}",
                    'PF': f"{pf:.2f}",
                    'Net P&L': f"${sum_net:,.2f}",
                    'Max DD': f"${max_dd:,.2f}"
                })

    grid_df = pd.DataFrame(grid_results)
    grid_df.to_csv('backtest/results/summary.csv', index=False)
    print(f"Ultra fast grid finished! Processed {len(grid_df)} combinations. Saved to backtest/results/summary.csv.")

if __name__ == '__main__':
    run_ultra_fast()
