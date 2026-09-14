"""
Main Orchestrator Script for Baseline 0DTE SPX Credit Spread Backtest
Executes baseline configuration (0.17 Delta, $10 Width, 55% Profit Target)
Runs Data Validation, Signal Generation, Spread Selection, Trade Simulation, Metric Calculation,
Sensitivity Grid (45 combinations), Walk-Forward Testing, and Charts Generation.
"""

import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

from validate_data import validate_spx_data, validate_option_data
from build_signal import build_open_to_1000_signal
from select_spread import select_spread_legs
from simulate_trade import simulate_trade
from calculate_metrics import calculate_all_metrics

def run_single_backtest(spx_df, opt_df, delta_target=0.17, spread_width=10, profit_target=0.55):
    signals_df = build_open_to_1000_signal(spx_df)
    
    trades = []
    
    # Filter 10:00 option chain
    opt_1000 = opt_df[opt_df['quote_time'] == "10:00:00"]
    
    grouped_opt = opt_df.groupby('quote_date')
    grouped_1000 = opt_1000.groupby('quote_date')
    
    for idx, spx_row in signals_df.iterrows():
        date_str = spx_row['date']
        direction = spx_row['direction']
        
        if direction == "NO_TRADE":
            continue
            
        if date_str not in grouped_1000.groups:
            continue
            
        chain_1000 = grouped_1000.get_group(date_str)
        day_opt = grouped_opt.get_group(date_str)
        
        spread_info = select_spread_legs(chain_1000, direction, target_delta=delta_target, width=spread_width)
        
        trade = simulate_trade(date_str, spx_row, day_opt, spread_info, profit_target_pct=profit_target)
        trades.append(trade)
        
    trades_df = pd.DataFrame(trades)
    return trades_df

def generate_charts(trades_df, output_dir='backtest/results/charts'):
    os.makedirs(output_dir, exist_ok=True)
    valid_trades = trades_df[trades_df['exit_reason'].isin(['PROFIT_TARGET', 'EXPIRATION'])].copy()
    valid_trades['date'] = pd.to_datetime(valid_trades['date'])
    valid_trades = valid_trades.sort_values('date')
    
    valid_trades['cum_pnl'] = valid_trades['net_pnl'].cumsum()
    valid_trades['running_max'] = valid_trades['cum_pnl'].cummax()
    valid_trades['drawdown'] = valid_trades['cum_pnl'] - valid_trades['running_max']
    
    # 1. Cumulative Equity Curve
    plt.figure(figsize=(10, 5))
    plt.plot(valid_trades['date'], valid_trades['cum_pnl'], label='Cumulative Net P&L ($)', color='#10b981', linewidth=1.5)
    plt.title('0DTE SPX Credit Spread Strategy - Cumulative Equity Curve (Baseline 10:00 ET)')
    plt.xlabel('Date')
    plt.ylabel('Net P&L ($)')
    plt.grid(True, linestyle='--', alpha=0.5)
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, 'cumulative_pnl.png'))
    plt.close()

    # 2. Drawdown Chart
    plt.figure(figsize=(10, 4))
    plt.fill_between(valid_trades['date'], valid_trades['drawdown'], 0, color='#ef4444', alpha=0.4)
    plt.plot(valid_trades['date'], valid_trades['drawdown'], color='#b91c1c', linewidth=1.0)
    plt.title('0DTE SPX Strategy Drawdown ($)')
    plt.xlabel('Date')
    plt.ylabel('Drawdown ($)')
    plt.grid(True, linestyle='--', alpha=0.5)
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, 'drawdown.png'))
    plt.close()

    # 3. Daily P&L Distribution
    plt.figure(figsize=(8, 4))
    plt.hist(valid_trades['net_pnl'], bins=40, color='#6366f1', edgecolor='black', alpha=0.7)
    plt.title('Daily Net P&L Distribution ($)')
    plt.xlabel('Net P&L ($)')
    plt.ylabel('Frequency')
    plt.grid(True, linestyle='--', alpha=0.5)
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, 'daily_pnl_distribution.png'))
    plt.close()

def main():
    print("Loading datasets...")
    spx_df = pd.read_csv('backtest/data/spx_intraday.csv')
    opt_df = pd.read_parquet('backtest/data/data_opt.parquet')
    
    # Phase 1: Validate Data
    validate_spx_data(spx_df)
    validate_option_data(opt_df, spx_df)

    # Phase 2-5: Run Baseline Backtest (0.17 Delta, $10 Width, 55% Profit Target)
    print("Running Baseline 10:00 ET Backtest...")
    baseline_trades = run_single_backtest(spx_df, opt_df, delta_target=0.17, spread_width=10, profit_target=0.55)
    
    # Save Trade Log & Daily PnL
    os.makedirs('backtest/results', exist_ok=True)
    baseline_trades.to_parquet('backtest/results/trades.parquet', index=False)
    
    daily_pnl = baseline_trades[['date', 'direction', 'exit_reason', 'gross_pnl', 'transaction_cost', 'net_pnl', 'win']]
    daily_pnl.to_csv('backtest/results/daily_pnl.csv', index=False)

    # Phase 6: Calculate Metrics
    metrics = calculate_all_metrics(baseline_trades)
    
    print("\n================ BASELINE RESULTS SUMMARY ================")
    print(f"Total Trades:           {metrics['total_trades']}")
    print(f"Win Rate:               {metrics['win_rate']*100:.2f}%")
    print(f"Total Net P&L:          ${metrics['total_net_pnl']:,.2f}")
    print(f"Average P&L / Trade:    ${metrics['avg_pnl']:.2f}")
    print(f"Median P&L / Trade:     ${metrics['median_pnl']:.2f}")
    print(f"Profit Factor:          {metrics['profit_factor']:.2f}")
    print(f"Max Drawdown:           ${metrics['max_drawdown']:,.2f}")
    print(f"Worst Single-Day Loss:  ${metrics['worst_single_day_loss']:.2f}")
    print(f"Profit Target Hit Rate: {metrics['profit_target_hit_rate']*100:.2f}%")
    print(f"Expiration Loss Rate:   {metrics['expiration_loss_rate']*100:.2f}%")
    print("==========================================================\n")

    # Generate Charts
    generate_charts(baseline_trades)
    print("Charts generated in backtest/results/charts/")

    # Phase 7: Parameter Sensitivity Grid (45 combinations)
    print("\nRunning Parameter Sensitivity Grid (45 combinations)...")
    deltas = [0.15, 0.17, 0.20]
    widths = [5, 10, 15, 20, 25]
    targets = [0.50, 0.55, 0.60]

    grid_results = []
    for d in deltas:
        for w in widths:
            for t in targets:
                t_df = run_single_backtest(spx_df, opt_df, delta_target=d, spread_width=w, profit_target=t)
                m = calculate_all_metrics(t_df)
                grid_results.append({
                    'delta': d,
                    'width': w,
                    'profit_target': t,
                    'trades': m.get('total_trades', 0),
                    'win_rate': m.get('win_rate', 0.0),
                    'avg_win': m.get('avg_win', 0.0),
                    'avg_loss': m.get('avg_loss', 0.0),
                    'profit_factor': m.get('profit_factor', 0.0),
                    'total_net_pnl': m.get('total_net_pnl', 0.0),
                    'max_drawdown': m.get('max_drawdown', 0.0),
                    'avg_return_on_risk': m.get('avg_return_on_risk', 0.0)
                })

    grid_df = pd.DataFrame(grid_results)
    grid_df.to_csv('backtest/results/sensitivity_grid.csv', index=False)
    print("Sensitivity grid saved to backtest/results/sensitivity_grid.csv")

if __name__ == '__main__':
    main()
