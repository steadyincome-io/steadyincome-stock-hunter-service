"""
Updated real options backtest runner using local 30m intraday price bars (spx_intraday_30m.csv).
Zero external API calls during backtest execution.
"""

import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

def run_real_option_fast():
    print("Loading 100% REAL historical SPX 30-minute intraday bars & 0DTE options datasets from local storage...")
    
    # 1. Load local 30-minute intraday bars (single local CSV read, 0 API rate limit hits)
    df_30m = pd.read_csv('backtest/data/spx_intraday_30m.csv')
    
    # Extract 09:30 open and 10:00 entry prices from local 30m bars
    bars_0930 = df_30m[df_30m['time'] == '09:30:00'].set_index('date')['open'].to_dict()
    bars_1000 = df_30m[df_30m['time'] == '10:00:00'].set_index('date')['close'].to_dict()
    bars_1600 = df_30m[df_30m['time'] == '15:30:00'].set_index('date')['close'].to_dict()

    # 2. Load pre-filtered 0DTE options dataset
    opt_0dte = pd.read_parquet('backtest/data/real_0dte_filtered.parquet')
    opt_0dte['QUOTE_DATE'] = pd.to_datetime(opt_0dte['QUOTE_DATE']).dt.strftime('%Y-%m-%d')

    # Build fast in-memory option chain maps
    opt_map = {}
    for date_val, grp in opt_0dte.groupby('QUOTE_DATE'):
        records = grp[['STRIKE', 'C_DELTA', 'C_BID', 'C_ASK', 'P_DELTA', 'P_BID', 'P_ASK']].to_dict('records')
        opt_map[date_val] = records

    valid_dates = sorted(list(set(opt_map.keys()).intersection(set(bars_0930.keys())).intersection(set(bars_1000.keys()))))
    print(f"Matched {len(valid_dates)} trading sessions between local 30m price bars and real 0DTE option chains.")

    deltas = [0.15, 0.17, 0.20]
    widths = [5, 10, 15, 20, 25]
    targets = [0.50, 0.55, 0.60]

    grid_results = []
    baseline_trades_df = None

    for d_target in deltas:
        for width in widths:
            for p_target in targets:
                trades = []

                for date_str in valid_dates:
                    spx_open = bars_0930[date_str]
                    spx_1000 = bars_1000[date_str]
                    spx_close = bars_1600.get(date_str, spx_1000)

                    ret_open_entry = (spx_1000 / spx_open) - 1.0
                    direction = 'PUT' if ret_open_entry > 0 else ('CALL' if ret_open_entry < 0 else 'NO_TRADE')

                    if direction == 'NO_TRADE':
                        continue

                    records = opt_map[date_str]

                    if direction == 'PUT':
                        otm = [r for r in records if r['STRIKE'] < spx_1000]
                        if not otm:
                            continue
                        short_leg = min(otm, key=lambda r: abs(abs(r['P_DELTA']) - d_target))
                        short_strike = short_leg['STRIKE']
                        long_strike = short_strike - width

                        long_leg = next((r for r in records if r['STRIKE'] == long_strike), None)
                        if not long_leg:
                            continue

                        s_bid, s_ask = float(short_leg['P_BID']), float(short_leg['P_ASK'])
                        l_bid, l_ask = float(long_leg['P_BID']), float(long_leg['P_ASK'])
                        s_mid = (s_bid + s_ask) / 2.0 if (s_bid + s_ask) > 0 else s_bid
                        l_mid = (l_bid + l_ask) / 2.0 if (l_bid + l_ask) > 0 else l_bid
                        short_delta = float(short_leg['P_DELTA'])

                    else:
                        otm = [r for r in records if r['STRIKE'] > spx_1000]
                        if not otm:
                            continue
                        short_leg = min(otm, key=lambda r: abs(abs(r['C_DELTA']) - d_target))
                        short_strike = short_leg['STRIKE']
                        long_strike = short_strike + width

                        long_leg = next((r for r in records if r['STRIKE'] == long_strike), None)
                        if not long_leg:
                            continue

                        s_bid, s_ask = float(short_leg['C_BID']), float(short_leg['C_ASK'])
                        l_bid, l_ask = float(long_leg['C_BID']), float(long_leg['C_ASK'])
                        s_mid = (s_bid + s_ask) / 2.0 if (s_bid + s_ask) > 0 else s_bid
                        l_mid = (l_bid + l_ask) / 2.0 if (l_bid + l_ask) > 0 else l_bid
                        short_delta = float(short_leg['C_DELTA'])

                    entry_credit = s_mid - l_mid
                    if entry_credit <= 0:
                        continue

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
                        'date': date_str,
                        'entry_time': '10:00:00',
                        'spx_open': spx_open,
                        'spx_entry': spx_1000,
                        'open_to_entry_return': ret_open_entry,
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
    print("Zero-API local backtest completed! Saved summary.csv and trades.parquet.")

if __name__ == '__main__':
    run_real_option_fast()
