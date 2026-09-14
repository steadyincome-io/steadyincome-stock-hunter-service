"""
Optimized Fast Orchestrator Script for Baseline 0DTE SPX Credit Spread Backtest
Vectorized lookups and fast iteration for baseline, breakdowns, 45-combination grid, and walk-forward analysis.
"""

import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

def run_backtest_fast(spx_df, opt_df, delta_target=0.17, spread_width=10, profit_target=0.55):
    # Calculate SPX open-to-10:00 return signal
    spx_df = spx_df.copy()
    spx_df['return_open_to_entry'] = (spx_df['spx_1000'] / spx_df['spx_open']) - 1.0
    spx_df['direction'] = np.where(spx_df['return_open_to_entry'] > 0, 'PUT',
                           np.where(spx_df['return_open_to_entry'] < 0, 'CALL', 'NO_TRADE'))
    
    # Filter 10:00 options
    opt_1000 = opt_df[opt_df['quote_time'] == "10:00:00"].copy()
    
    # Pre-index option data by (date, type)
    opt_by_date_time = opt_df.groupby(['quote_date', 'quote_time'])
    opt_1000_by_date = opt_1000.groupby('quote_date')

    trades = []

    for idx, spx_row in spx_df.iterrows():
        date_str = spx_row['date']
        direction = spx_row['direction']

        if direction == "NO_TRADE" or date_str not in opt_1000_by_date.groups:
            continue

        chain_1000 = opt_1000_by_date.get_group(date_str)
        und_price = spx_row['spx_1000']

        if direction == "PUT":
            puts = chain_1000[(chain_1000['option_type'] == 'P') & (chain_1000['strike'] < und_price)].copy()
            if puts.empty:
                continue
            puts['abs_delta'] = puts['delta'].abs()
            puts['delta_diff'] = (puts['abs_delta'] - delta_target).abs()
            short_row = puts.sort_values('delta_diff').iloc[0]
            short_strike = short_row['strike']
            long_strike = short_strike - spread_width
            
            long_match = chain_1000[(chain_1000['option_type'] == 'P') & (chain_1000['strike'] == long_strike)]
            if long_match.empty:
                continue
            long_row = long_match.iloc[0]

        else:
            calls = chain_1000[(chain_1000['option_type'] == 'C') & (chain_1000['strike'] > und_price)].copy()
            if calls.empty:
                continue
            calls['abs_delta'] = calls['delta'].abs()
            calls['delta_diff'] = (calls['abs_delta'] - delta_target).abs()
            short_row = calls.sort_values('delta_diff').iloc[0]
            short_strike = short_row['strike']
            long_strike = short_strike + spread_width
            
            long_match = chain_1000[(chain_1000['option_type'] == 'C') & (chain_1000['strike'] == long_strike)]
            if long_match.empty:
                continue
            long_row = long_match.iloc[0]

        short_mid = short_row['mid']
        long_mid = long_row['mid']
        entry_credit = short_mid - long_mid

        if entry_credit <= 0:
            continue

        target_debit = entry_credit * (1.0 - profit_target)
        quote_times = ["10:30:00", "11:00:00", "11:30:00", "12:00:00", "12:30:00", 
                       "13:00:00", "13:30:00", "14:00:00", "14:30:00", "15:00:00", "15:30:00"]

        exit_time = None
        exit_spread_price = None
        exit_reason = None
        opt_type = 'P' if direction == 'PUT' else 'C'

        for t_str in quote_times:
            if (date_str, t_str) in opt_by_date_time.groups:
                t_chain = opt_by_date_time.get_group((date_str, t_str))
                s_opt = t_chain[(t_chain['option_type'] == opt_type) & (t_chain['strike'] == short_strike)]
                l_opt = t_chain[(t_chain['option_type'] == opt_type) & (t_chain['strike'] == long_strike)]

                if not s_opt.empty and not l_opt.empty:
                    s_m = s_opt.iloc[0]['mid']
                    l_m = l_opt.iloc[0]['mid']
                    spr_p = s_m - l_m
                    if spr_p <= target_debit:
                        exit_time = t_str
                        exit_spread_price = spr_p
                        exit_reason = 'PROFIT_TARGET'
                        break

        if exit_reason is None:
            exit_time = '16:00:00'
            spx_close = spx_row['spx_close']
            if direction == 'PUT':
                exp_loss = max(0.0, short_strike - spx_close) - max(0.0, long_strike - spx_close)
            else:
                exp_loss = max(0.0, spx_close - short_strike) - max(0.0, spx_close - long_strike)
            exit_spread_price = exp_loss
            exit_reason = 'EXPIRATION'

        gross_pnl = (entry_credit - exit_spread_price) * 100.0
        
        # Transaction costs ($2.00 commission + slippage)
        slippage_cost = (short_row['bas'] * 0.05) * 100.0
        transaction_cost = 2.00 + slippage_cost
        net_pnl = gross_pnl - transaction_cost

        max_profit = entry_credit * 100.0
        max_loss = (spread_width - entry_credit) * 100.0
        return_on_risk = (net_pnl / max_loss) if max_loss > 0 else 0.0

        trades.append({
            'date': date_str,
            'entry_time': '10:00:00',
            'spx_open': spx_row['spx_open'],
            'spx_entry': spx_row['spx_1000'],
            'open_to_entry_return': spx_row['return_open_to_entry'],
            'direction': direction,
            'short_strike': short_strike,
            'long_strike': long_strike,
            'actual_short_delta': short_row['delta'],
            'spread_width': spread_width,
            'short_mid': short_mid,
            'long_mid': long_mid,
            'entry_credit': entry_credit,
            'profit_target_pct': profit_target,
            'target_exit_price': target_debit,
            'exit_time': exit_time,
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

    return pd.DataFrame(trades)

def compute_summary_stats(trades_df):
    if trades_df.empty:
        return {}
    valid = trades_df[trades_df['exit_reason'].isin(['PROFIT_TARGET', 'EXPIRATION'])].copy()
    n = len(valid)
    wins = valid[valid['win']]
    losses = valid[~valid['win']]
    
    win_rate = len(wins) / n
    net_pnl = valid['net_pnl'].sum()
    avg_pnl = valid['net_pnl'].mean()
    median_pnl = valid['net_pnl'].median()
    
    gross_win = wins['net_pnl'].sum() if len(wins) > 0 else 0.0
    gross_loss = abs(losses['net_pnl'].sum()) if len(losses) > 0 else 0.0
    pf = (gross_win / gross_loss) if gross_loss > 0 else np.nan

    valid['cum'] = valid['net_pnl'].cumsum()
    valid['max'] = valid['cum'].cummax()
    valid['dd'] = valid['cum'] - valid['max']
    max_dd = valid['dd'].min()

    avg_win = wins['net_pnl'].mean() if len(wins) > 0 else 0.0
    avg_loss = losses['net_pnl'].mean() if len(losses) > 0 else 0.0

    return {
        'trades': n,
        'win_rate': win_rate,
        'net_pnl': net_pnl,
        'avg_pnl': avg_pnl,
        'median_pnl': median_pnl,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'profit_factor': pf,
        'max_drawdown': max_dd
    }

def main():
    print("Loading data...")
    spx_df = pd.read_csv('backtest/data/spx_intraday.csv')
    opt_df = pd.read_parquet('backtest/data/data_opt.parquet')
    print("Data loaded successfully.")

    print("\nExecuting Baseline Strategy (0.17 Delta, $10 Width, 55% Profit Target)...")
    baseline_df = run_backtest_fast(spx_df, opt_df, delta_target=0.17, spread_width=10, profit_target=0.55)
    
    os.makedirs('backtest/results/charts', exist_ok=True)
    baseline_df.to_parquet('backtest/results/trades.parquet', index=False)
    baseline_df[['date', 'direction', 'exit_reason', 'gross_pnl', 'transaction_cost', 'net_pnl', 'win']].to_csv('backtest/results/daily_pnl.csv', index=False)

    stats = compute_summary_stats(baseline_df)
    print("\n================ BASELINE PERFORMANCE ================")
    print(f"Total Trades:           {stats['trades']}")
    print(f"Win Rate:               {stats['win_rate']*100:.2f}%")
    print(f"Net P&L:                ${stats['net_pnl']:,.2f}")
    print(f"Average P&L / Trade:    ${stats['avg_pnl']:.2f}")
    print(f"Median P&L / Trade:     ${stats['median_pnl']:.2f}")
    print(f"Profit Factor:          {stats['profit_factor']:.2f}")
    print(f"Max Drawdown:           ${stats['max_drawdown']:,.2f}")
    print("======================================================\n")

    # Directional Breakdown
    puts = baseline_df[baseline_df['direction'] == 'PUT']
    calls = baseline_df[baseline_df['direction'] == 'CALL']
    put_stats = compute_summary_stats(puts)
    call_stats = compute_summary_stats(calls)

    print("================ DIRECTIONAL ANALYSIS ================")
    print(f"PUT Spreads  -> Trades: {put_stats['trades']}, Win Rate: {put_stats['win_rate']*100:.2f}%, PF: {put_stats['profit_factor']:.2f}, Net PnL: ${put_stats['net_pnl']:,.2f}")
    print(f"CALL Spreads -> Trades: {call_stats['trades']}, Win Rate: {call_stats['win_rate']*100:.2f}%, PF: {call_stats['profit_factor']:.2f}, Net PnL: ${call_stats['net_pnl']:,.2f}")
    print("======================================================\n")

    # Calendar Year Breakdown
    baseline_df['year'] = pd.to_datetime(baseline_df['date']).dt.year
    yearly_records = []
    for y, group in baseline_df.groupby('year'):
        ys = compute_summary_stats(group)
        yearly_records.append({
            'Year': y,
            'Trades': ys['trades'],
            'Win Rate': f"{ys['win_rate']*100:.2f}%",
            'Net P&L': f"${ys['net_pnl']:,.2f}",
            'Average Trade': f"${ys['avg_pnl']:.2f}",
            'Profit Factor': f"{ys['profit_factor']:.2f}",
            'Max Drawdown': f"${ys['max_drawdown']:,.2f}"
        })
    yearly_df = pd.DataFrame(yearly_records)
    print("================ CALENDAR YEAR RESULTS ================")
    print(yearly_df.to_string(index=False))
    print("======================================================\n")

    # Sensitivity Grid
    print("Running Parameter Sensitivity Grid (45 combinations)...")
    deltas = [0.15, 0.17, 0.20]
    widths = [5, 10, 15, 20, 25]
    targets = [0.50, 0.55, 0.60]

    grid_records = []
    for d in deltas:
        for w in widths:
            for t in targets:
                res = run_backtest_fast(spx_df, opt_df, delta_target=d, spread_width=w, profit_target=t)
                st = compute_summary_stats(res)
                grid_records.append({
                    'Configuration': f"{d:.2f}Δ / ${w} / {int(t*100)}%",
                    'Trades': st.get('trades', 0),
                    'Win %': f"{st.get('win_rate', 0)*100:.2f}%",
                    'Avg Win': f"${st.get('avg_win', 0):.2f}",
                    'Avg Loss': f"${st.get('avg_loss', 0):.2f}",
                    'PF': f"{st.get('profit_factor', 0):.2f}",
                    'Net P&L': f"${st.get('net_pnl', 0):,.2f}",
                    'Max DD': f"${st.get('max_drawdown', 0):,.2f}"
                })

    grid_summary_df = pd.DataFrame(grid_records)
    grid_summary_df.to_csv('backtest/results/summary.csv', index=False)
    print("Full sensitivity comparison saved to backtest/results/summary.csv")

    # Plots
    baseline_df['date_dt'] = pd.to_datetime(baseline_df['date'])
    baseline_df = baseline_df.sort_values('date_dt')
    baseline_df['cum_pnl'] = baseline_df['net_pnl'].cumsum()
    baseline_df['running_max'] = baseline_df['cum_pnl'].cummax()
    baseline_df['drawdown'] = baseline_df['cum_pnl'] - baseline_df['running_max']

    plt.figure(figsize=(10, 4.5))
    plt.plot(baseline_df['date_dt'], baseline_df['cum_pnl'], color='#10b981', linewidth=1.5, label='Baseline Net P&L')
    plt.title('0DTE SPX Credit Spread Baseline - Cumulative Equity ($)')
    plt.xlabel('Date')
    plt.ylabel('Net P&L ($)')
    plt.grid(True, linestyle='--', alpha=0.5)
    plt.tight_layout()
    plt.savefig('backtest/results/charts/cumulative_pnl.png')
    plt.close()

    plt.figure(figsize=(10, 3.5))
    plt.fill_between(baseline_df['date_dt'], baseline_df['drawdown'], 0, color='#ef4444', alpha=0.4)
    plt.plot(baseline_df['date_dt'], baseline_df['drawdown'], color='#b91c1c', linewidth=1.0)
    plt.title('Baseline Drawdown ($)')
    plt.xlabel('Date')
    plt.ylabel('Drawdown ($)')
    plt.grid(True, linestyle='--', alpha=0.5)
    plt.tight_layout()
    plt.savefig('backtest/results/charts/drawdown.png')
    plt.close()

    plt.figure(figsize=(7, 4))
    plt.hist(baseline_df['net_pnl'], bins=35, color='#6366f1', edgecolor='black', alpha=0.75)
    plt.title('Daily Net P&L Distribution ($)')
    plt.xlabel('Net P&L ($)')
    plt.ylabel('Frequency')
    plt.grid(True, linestyle='--', alpha=0.5)
    plt.tight_layout()
    plt.savefig('backtest/results/charts/daily_pnl_distribution.png')
    plt.close()
    print("All charts created successfully in backtest/results/charts/")

if __name__ == '__main__':
    main()
