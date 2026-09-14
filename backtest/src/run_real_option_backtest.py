"""
Ultra-fast backtest runner reading lightweight real 0DTE options data.
"""

import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

def run_real_option_backtest():
    print("Loading 100% REAL historical SPX underlying and pre-filtered 0DTE options datasets...")
    
    spx_df = pd.read_csv('backtest/data/spx_intraday.csv')
    spx_df['date'] = pd.to_datetime(spx_df['date'])
    
    opt_0dte = pd.read_parquet('backtest/data/real_0dte_filtered.parquet')
    opt_0dte['QUOTE_DATE'] = pd.to_datetime(opt_0dte['QUOTE_DATE'])
    
    spx_df['return_open_to_entry'] = (spx_df['spx_1000'] / spx_df['spx_open']) - 1.0
    spx_df['direction'] = np.where(spx_df['return_open_to_entry'] > 0, 'PUT',
                           np.where(spx_df['return_open_to_entry'] < 0, 'CALL', 'NO_TRADE'))

    opt_by_date = dict(tuple(opt_0dte.groupby('QUOTE_DATE')))

    print(f"Loaded {len(spx_df)} SPX trading days and {len(opt_by_date)} real 0DTE option chain dates.")

    deltas = [0.15, 0.17, 0.20]
    widths = [5, 10, 15, 20, 25]
    targets = [0.50, 0.55, 0.60]

    grid_results = []
    baseline_trades_df = None

    for d_target in deltas:
        for width in widths:
            for p_target in targets:
                trades = []

                for spx_row in spx_df.itertuples(index=False):
                    date_val = spx_row.date
                    direction = spx_row.direction
                    if direction == 'NO_TRADE' or date_val not in opt_by_date:
                        continue

                    chain = opt_by_date[date_val]
                    und_price = float(spx_row.spx_1000)

                    if direction == 'PUT':
                        otm = chain[chain['STRIKE'] < und_price].copy()
                        if otm.empty:
                            continue
                        otm['delta_diff'] = (otm['P_DELTA'].abs() - d_target).abs()
                        short_leg = otm.sort_values('delta_diff').iloc[0]
                        short_strike = short_leg['STRIKE']
                        long_strike = short_strike - width

                        long_match = chain[chain['STRIKE'] == long_strike]
                        if long_match.empty:
                            continue
                        long_leg = long_match.iloc[0]

                        s_bid, s_ask = float(short_leg['P_BID']), float(short_leg['P_ASK'])
                        l_bid, l_ask = float(long_leg['P_BID']), float(long_leg['P_ASK'])
                        s_mid = (s_bid + s_ask) / 2.0 if (s_bid + s_ask) > 0 else float(short_leg['P_BID'])
                        l_mid = (l_bid + l_ask) / 2.0 if (l_bid + l_ask) > 0 else float(long_leg['P_BID'])
                        short_delta = float(short_leg['P_DELTA'])

                    else:
                        otm = chain[chain['STRIKE'] > und_price].copy()
                        if otm.empty:
                            continue
                        otm['delta_diff'] = (otm['C_DELTA'].abs() - d_target).abs()
                        short_leg = otm.sort_values('delta_diff').iloc[0]
                        short_strike = short_leg['STRIKE']
                        long_strike = short_strike + width

                        long_match = chain[chain['STRIKE'] == long_strike]
                        if long_match.empty:
                            continue
                        long_leg = long_match.iloc[0]

                        s_bid, s_ask = float(short_leg['C_BID']), float(short_leg['C_ASK'])
                        l_bid, l_ask = float(long_leg['C_BID']), float(long_leg['C_ASK'])
                        s_mid = (s_bid + s_ask) / 2.0 if (s_bid + s_ask) > 0 else float(short_leg['C_BID'])
                        l_mid = (l_bid + l_ask) / 2.0 if (l_bid + l_ask) > 0 else float(long_leg['C_BID'])
                        short_delta = float(short_leg['C_DELTA'])

                    entry_credit = s_mid - l_mid
                    if entry_credit <= 0:
                        continue

                    spx_close = float(spx_row.spx_close)
                    if direction == 'PUT':
                        exp_loss = max(0.0, short_strike - spx_close) - max(0.0, long_strike - spx_close)
                    else:
                        exp_loss = max(0.0, spx_close - short_strike) - max(0.0, spx_close - long_strike)

                    if exp_loss == 0:
                        exit_spread_price = entry_credit * (1.0 - p_target)
                        exit_reason = 'PROFIT_TARGET'
                    else:
                        exit_spread_price = exp_loss
                        exit_reason = 'EXPIRATION'

                    gross_pnl = (entry_credit - exit_spread_price) * 100.0
                    bas_cost = max(0.10, (s_ask - s_bid) + (l_ask - l_bid)) * 0.05 * 100.0
                    transaction_cost = 2.00 + bas_cost
                    net_pnl = gross_pnl - transaction_cost

                    max_profit = entry_credit * 100.0
                    max_loss = (width - entry_credit) * 100.0
                    return_on_risk = (net_pnl / max_loss) if max_loss > 0 else 0.0

                    trades.append({
                        'date': date_val.strftime('%Y-%m-%d'),
                        'entry_time': '10:00:00',
                        'spx_open': float(spx_row.spx_open),
                        'spx_entry': und_price,
                        'open_to_entry_return': float(spx_row.return_open_to_entry),
                        'direction': direction,
                        'short_strike': short_strike,
                        'long_strike': long_strike,
                        'actual_short_delta': short_delta,
                        'spread_width': width,
                        'short_mid': s_mid,
                        'long_mid': l_mid,
                        'entry_credit': entry_credit,
                        'profit_target_pct': p_target,
                        'target_exit_price': entry_credit * (1.0 - p_target),
                        'exit_time': '16:00:00',
                        'exit_spread_price': exit_spread_price,
                        'exit_reason': exit_reason,
                        'gross_pnl': gross_pnl,
                        'transaction_cost': transaction_cost,
                        'net_pnl': net_pnl,
                        'max_profit': max_profit,
                        'max_loss': max_loss,
                        'return_on_risk': return_on_risk,
                        'win': net_pnl > 0
                    })

                tdf = pd.DataFrame(trades)
                if d_target == 0.17 and width == 10 and p_target == 0.55:
                    baseline_trades_df = tdf.copy()

                total_t = len(tdf)
                if total_t > 0:
                    wins = tdf[tdf['win']]
                    losses = tdf[~tdf['win']]
                    win_pct = len(wins) / total_t
                    sum_net = tdf['net_pnl'].sum()
                    avg_w = wins['net_pnl'].mean() if not wins.empty else 0.0
                    avg_l = losses['net_pnl'].mean() if not losses.empty else 0.0
                    gross_w = wins['net_pnl'].sum() if not wins.empty else 0.0
                    gross_l = abs(losses['net_pnl'].sum()) if not losses.empty else 0.0
                    pf = (gross_w / gross_l) if gross_l > 0 else np.nan

                    tdf['cum'] = tdf['net_pnl'].cumsum()
                    tdf['rmax'] = tdf['cum'].cummax()
                    max_dd = (tdf['cum'] - tdf['rmax']).min()
                else:
                    win_pct, sum_net, avg_w, avg_l, pf, max_dd = 0, 0, 0, 0, 0, 0

                is_b = (d_target == 0.17 and width == 10 and p_target == 0.55)
                cfg = f"**{d_target:.2f}Δ / ${width} / {int(p_target*100)}%**" if is_b else f"{d_target:.2f}Δ / ${width} / {int(p_target*100)}%"

                grid_results.append({
                    'Configuration': cfg,
                    'Trades': total_t,
                    'Win %': f"{win_pct*100:.2f}%",
                    'Avg Win': f"${avg_w:.2f}",
                    'Avg Loss': f"${avg_l:.2f}",
                    'PF': f"{pf:.2f}",
                    'Net P&L': f"${sum_net:,.2f}",
                    'Max DD': f"${max_dd:,.2f}"
                })

    os.makedirs('backtest/results/charts', exist_ok=True)
    baseline_trades_df.to_parquet('backtest/results/trades.parquet', index=False)
    baseline_trades_df[['date', 'direction', 'exit_reason', 'gross_pnl', 'transaction_cost', 'net_pnl', 'win']].to_csv('backtest/results/daily_pnl.csv', index=False)

    summary_df = pd.DataFrame(grid_results)
    summary_df.to_csv('backtest/results/summary.csv', index=False)
    print("Real options backtest completed! Saved summary.csv and trades.parquet.")

    # Re-generate charts with real options data
    baseline_trades_df['date_dt'] = pd.to_datetime(baseline_trades_df['date'])
    baseline_trades_df = baseline_trades_df.sort_values('date_dt')
    baseline_trades_df['cum_pnl'] = baseline_trades_df['net_pnl'].cumsum()
    baseline_trades_df['running_max'] = baseline_trades_df['cum_pnl'].cummax()
    baseline_trades_df['drawdown'] = baseline_trades_df['cum_pnl'] - baseline_trades_df['running_max']

    plt.figure(figsize=(10, 4.5))
    plt.plot(baseline_trades_df['date_dt'], baseline_trades_df['cum_pnl'], color='#10b981', linewidth=1.5, label='Real Options Net P&L')
    plt.title('0DTE SPX Credit Spread (REAL Historical Option Chain Data) - Cumulative Equity ($)')
    plt.xlabel('Date')
    plt.ylabel('Net P&L ($)')
    plt.grid(True, linestyle='--', alpha=0.5)
    plt.tight_layout()
    plt.savefig('backtest/results/charts/cumulative_pnl.png')
    plt.close()

    plt.figure(figsize=(10, 3.5))
    plt.fill_between(baseline_trades_df['date_dt'], baseline_trades_df['drawdown'], 0, color='#ef4444', alpha=0.4)
    plt.plot(baseline_trades_df['date_dt'], baseline_trades_df['drawdown'], color='#b91c1c', linewidth=1.0)
    plt.title('Drawdown ($) - Real Option Data')
    plt.xlabel('Date')
    plt.ylabel('Drawdown ($)')
    plt.grid(True, linestyle='--', alpha=0.5)
    plt.tight_layout()
    plt.savefig('backtest/results/charts/drawdown.png')
    plt.close()

    plt.figure(figsize=(7, 4))
    plt.hist(baseline_trades_df['net_pnl'], bins=35, color='#6366f1', edgecolor='black', alpha=0.75)
    plt.title('Daily Net P&L Distribution ($) - Real Options Data')
    plt.xlabel('Net P&L ($)')
    plt.ylabel('Frequency')
    plt.grid(True, linestyle='--', alpha=0.5)
    plt.tight_layout()
    plt.savefig('backtest/results/charts/daily_pnl_distribution.png')
    plt.close()

if __name__ == '__main__':
    run_real_option_backtest()
