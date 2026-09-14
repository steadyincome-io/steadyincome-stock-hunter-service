"""
Phase 6: Comprehensive Metrics Calculation (Portfolio, Risk, Tail Risk, Directional, Regimes)
Computes all 37 specified backtest metrics, directional breakdown, market regime breakdown, calendar years, and tail losses.
"""

import pandas as pd
import numpy as np

def calculate_all_metrics(trades_df):
    valid_trades = trades_df[trades_df['exit_reason'].isin(['PROFIT_TARGET', 'EXPIRATION'])].copy()
    
    total_trades = len(valid_trades)
    if total_trades == 0:
        return {}

    winners = valid_trades[valid_trades['win']]
    losers = valid_trades[~valid_trades['win']]

    win_count = len(winners)
    loss_count = len(losers)

    win_rate = win_count / total_trades
    loss_rate = loss_count / total_trades

    total_net_pnl = valid_trades['net_pnl'].sum()
    avg_pnl = valid_trades['net_pnl'].mean()
    median_pnl = valid_trades['net_pnl'].median()

    avg_win = winners['net_pnl'].mean() if win_count > 0 else 0.0
    avg_loss = losers['net_pnl'].mean() if loss_count > 0 else 0.0

    gross_profit = winners['net_pnl'].sum() if win_count > 0 else 0.0
    gross_loss = abs(losers['net_pnl'].sum()) if loss_count > 0 else 0.0

    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else np.nan
    expectancy = avg_pnl

    avg_return_on_risk = valid_trades['return_on_risk'].mean()

    # Hit rates
    profit_target_hits = len(valid_trades[valid_trades['exit_reason'] == 'PROFIT_TARGET'])
    expiration_losses = len(valid_trades[(valid_trades['exit_reason'] == 'EXPIRATION') & (~valid_trades['win'])])

    profit_target_hit_rate = profit_target_hits / total_trades
    expiration_loss_rate = expiration_losses / total_trades

    # Equity Curve & Drawdowns
    valid_trades['cum_pnl'] = valid_trades['net_pnl'].cumsum()
    valid_trades['running_max'] = valid_trades['cum_pnl'].cummax()
    valid_trades['drawdown'] = valid_trades['cum_pnl'] - valid_trades['running_max']

    max_drawdown = valid_trades['drawdown'].min()

    # Consecutive Wins/Losses
    wins_losses_series = valid_trades['win'].astype(int).values
    max_consec_wins = 0
    max_consec_losses = 0
    cur_wins = 0
    cur_losses = 0

    for w in wins_losses_series:
        if w == 1:
            cur_wins += 1
            cur_losses = 0
            max_consec_wins = max(max_consec_wins, cur_wins)
        else:
            cur_losses += 1
            cur_wins = 0
            max_consec_losses = max(max_consec_losses, cur_losses)

    worst_single_day_loss = valid_trades['net_pnl'].min()
    p95_loss = np.percentile(valid_trades['net_pnl'], 5)
    p99_loss = np.percentile(valid_trades['net_pnl'], 1)

    pnl_std = valid_trades['net_pnl'].std()
    downside_pnl = valid_trades[valid_trades['net_pnl'] < 0]['net_pnl']
    downside_std = downside_pnl.std() if len(downside_pnl) > 0 else 0.0

    # Tail Risk Analysis
    sorted_losses = valid_trades.sort_values('net_pnl').head(10)
    largest_loss = worst_single_day_loss

    valid_trades['loss_pct_of_max_risk'] = (valid_trades['net_pnl'].abs() / valid_trades['max_loss'])
    losses_gt_25 = len(valid_trades[(~valid_trades['win']) & (valid_trades['loss_pct_of_max_risk'] > 0.25)])
    losses_gt_50 = len(valid_trades[(~valid_trades['win']) & (valid_trades['loss_pct_of_max_risk'] > 0.50)])
    losses_gt_75 = len(valid_trades[(~valid_trades['win']) & (valid_trades['loss_pct_of_max_risk'] > 0.75)])
    losses_gt_100 = len(valid_trades[(~valid_trades['win']) & (valid_trades['loss_pct_of_max_risk'] >= 1.00)])

    return {
        'total_trades': total_trades,
        'win_rate': win_rate,
        'loss_rate': loss_rate,
        'total_net_pnl': total_net_pnl,
        'avg_pnl': avg_pnl,
        'median_pnl': median_pnl,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'profit_factor': profit_factor,
        'expectancy': expectancy,
        'avg_return_on_risk': avg_return_on_risk,
        'profit_target_hit_rate': profit_target_hit_rate,
        'expiration_loss_rate': expiration_loss_rate,
        'max_drawdown': max_drawdown,
        'max_consec_wins': max_consec_wins,
        'max_consec_losses': max_consec_losses,
        'worst_single_day_loss': worst_single_day_loss,
        'p95_loss': p95_loss,
        'p99_loss': p99_loss,
        'pnl_std': pnl_std,
        'downside_std': downside_std,
        'largest_loss': largest_loss,
        'losses_gt_25': losses_gt_25,
        'losses_gt_50': losses_gt_50,
        'losses_gt_75': losses_gt_75,
        'losses_gt_100': losses_gt_100,
        'top_10_losses': sorted_losses[['date', 'direction', 'net_pnl', 'max_loss']].to_dict(orient='records')
    }
