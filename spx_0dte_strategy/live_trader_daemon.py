"""
0DTE SPX / SPY Live Signal & Trade Tracker Script
- Folder: spx_0dte_strategy/
- Database: spx_0dte_strategy/spx_spread_trades.db
- Polling: 5-minute interval (300 seconds) to conserve Alpaca API rate limits
- Signals: 0.15 Delta, $25 Spread Width, 60% Profit Target
"""

import os
import sys
import time
import requests
import datetime
import pandas as pd
from dotenv import load_dotenv

# Import local SQLite helper module
from db import init_db, log_trade_entry, update_trade_exit, get_all_trades_df

load_dotenv()

# --- Config Knobs ---
TARGET_DELTA = 0.15
SPREAD_WIDTH = 25.0
PROFIT_TARGET_PCT = 0.60
POLL_INTERVAL_SECONDS = 300 # 5-minute polling to save rate limits

APCA_KEY = os.getenv('APCA_API_KEY_ID')
APCA_SECRET = os.getenv('APCA_API_SECRET_KEY')
BASE_URL = os.getenv('APCA_API_BASE_URL', 'https://data.alpaca.markets')

HEADERS = {
    'APCA-API-KEY-ID': APCA_KEY,
    'APCA-API-SECRET-KEY': APCA_SECRET
}

def log(msg):
    now_str = datetime.datetime.now().strftime('%H:%M:%S')
    print(f"[{now_str}] {msg}")

def get_latest_price(symbol='SPY'):
    """Fetch latest minute bar for SPY/SPX from Alpaca Market Data"""
    url = f"{BASE_URL}/v2/stocks/bars?symbols={symbol}&timeframe=1Min&limit=5"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code == 200:
            bars = res.json().get('bars', {}).get(symbol, [])
            if bars:
                latest = bars[-1]
                return float(latest['c']), latest['t']
    except Exception as e:
        log(f"Error fetching price: {e}")
    return None, None

def get_option_snapshot(symbol_option):
    """Fetch option quote/snapshot for specific contract option symbol"""
    url = f"{BASE_URL}/v1beta1/options/snapshots?symbols={symbol_option}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code == 200:
            snapshots = res.json().get('snapshots', {})
            if symbol_option in snapshots:
                snap = snapshots[symbol_option]
                quote = snap.get('latestQuote', {})
                bid = float(quote.get('bp', 0.0))
                ask = float(quote.get('ap', 0.0))
                mid = (bid + ask) / 2.0 if (bid + ask) > 0 else float(snap.get('latestTrade', {}).get('p', 0.0))
                return {'bid': bid, 'ask': ask, 'mid': mid}
    except Exception as e:
        log(f"Error fetching option snapshot for {symbol_option}: {e}")
    return None

def wait_for_1000_am():
    """Poll every 5 minutes until 10:00 AM ET entry price is available"""
    log("Checking market open and entry price...")
    
    spx_open = None
    spx_1000 = None

    while True:
        now_et = datetime.datetime.now(datetime.timezone.utc).astimezone(datetime.timezone(datetime.timedelta(hours=-4)))
        curr_time_str = now_et.strftime('%H:%M:%S')

        p, p_time = get_latest_price('SPY')

        if p is not None:
            if spx_open is None:
                spx_open = p
                log(f"Captured Morning Opening Price: ${spx_open:.2f}")

            log(f"Current Price ({curr_time_str}): ${p:.2f}")

            # Check if 10:00 AM ET is reached
            if now_et.hour > 10 or (now_et.hour == 10 and now_et.minute >= 0):
                spx_1000 = p
                log(f"Captured 10:00 AM Entry Price: ${spx_1000:.2f}")
                break
        
        log(f"Waiting for 10:00 AM ET entry time... (Current: {curr_time_str}). Polling in 5 minutes...")
        time.sleep(POLL_INTERVAL_SECONDS)

    return spx_open, spx_1000

def run_signal_tracker():
    init_db()
    print("=================================================================")
    print("      0DTE SPX CREDIT SPREAD LIVE DAEMON & DB TRACKER            ")
    print(f"      Storage: spx_0dte_strategy/spx_spread_trades.db")
    print(f"      Settings: Delta={TARGET_DELTA} | Width=${SPREAD_WIDTH} | Target={int(PROFIT_TARGET_PCT*100)}%")
    print(f"      Rate-Limit Control: 5-Minute Polling (300s)")
    print("=================================================================\n")

    spx_open, spx_1000 = wait_for_1000_am()

    ret_open_to_entry = (spx_1000 / spx_open) - 1.0
    today_str = datetime.datetime.now().strftime('%Y-%m-%d')
    now_time_str = datetime.datetime.now().strftime('%H:%M:%S')

    print("\n---------------- DIRECTION SIGNAL RESULT ----------------")
    print(f"09:30 Open Price: ${spx_open:.2f}")
    print(f"10:00 Entry Price: ${spx_1000:.2f}")
    print(f"Return Open->10:00: {ret_open_to_entry*100:+.3f}%")

    if ret_open_to_entry > 0:
        direction = "PUT"
        print("SIGNAL: UP -> SELL PUT CREDIT SPREAD (Bullish)")
        estimated_short_strike = int(round((spx_1000 * 0.992) / 5.0) * 5)
        estimated_long_strike = estimated_short_strike - SPREAD_WIDTH
    elif ret_open_to_entry < 0:
        direction = "CALL"
        print("SIGNAL: DOWN -> SELL CALL CREDIT SPREAD (Bearish)")
        estimated_short_strike = int(round((spx_1000 * 1.008) / 5.0) * 5)
        estimated_long_strike = estimated_short_strike + SPREAD_WIDTH
    else:
        print("SIGNAL: FLAT -> NO TRADE")
        return

    print("\n---------------- RECOMMEND STRIKE SELECTION ----------------")
    print(f"Strategy Direction:   {direction} CREDIT SPREAD")
    print(f"Recommended Short ({TARGET_DELTA}Δ): Strike ${estimated_short_strike}")
    print(f"Recommended Long Strike:   Strike ${estimated_long_strike} (${SPREAD_WIDTH} width)")
    print("-----------------------------------------------------------\n")

    user_input = input("Did you place this trade? (y/n): ").strip().lower()
    if user_input not in ['y', 'yes']:
        log("User skipped trade. Exiting tracker daemon.")
        return

    try:
        user_credit = float(input("Enter net credit collected per share (e.g. 1.25): ").strip())
    except ValueError:
        user_credit = 1.25
        log("Defaulting net credit collected to $1.25")

    target_exit_debit = user_credit * (1.0 - PROFIT_TARGET_PCT)
    max_profit = user_credit * 100.0
    max_loss = (SPREAD_WIDTH - user_credit) * 100.0

    today_sym_date = datetime.datetime.now().strftime('%Y%m%d')[2:]
    short_sym = f"SPY{today_sym_date}{'P' if direction=='PUT' else 'C'}{int(estimated_short_strike*1000):08d}"
    long_sym = f"SPY{today_sym_date}{'P' if direction=='PUT' else 'C'}{int(estimated_long_strike*1000):08d}"

    # Log trade entry into SQLite DB
    trade_id = log_trade_entry({
        'trade_date': today_str,
        'entry_time': now_time_str,
        'spx_open': spx_open,
        'spx_entry': spx_1000,
        'open_to_entry_return': ret_open_to_entry,
        'direction': direction,
        'short_strike': estimated_short_strike,
        'long_strike': estimated_long_strike,
        'target_delta': TARGET_DELTA,
        'spread_width': SPREAD_WIDTH,
        'short_symbol': short_sym,
        'long_symbol': long_sym,
        'entry_credit': user_credit,
        'target_exit_debit': target_exit_debit,
        'max_profit': max_profit,
        'max_loss': max_loss
    })

    log(f"Trade successfully logged to SQLite Database! Trade ID: {trade_id}")

    print("\n================ ACTIVE TRADE MONITORING ================")
    print(f"Database Record ID:       {trade_id}")
    print(f"Net Credit Collected:     ${user_credit:.2f} (${max_profit:.2f} max profit)")
    print(f"Max Loss / Risk:          ${max_loss:.2f}")
    print(f"60% Profit Target Debit:  ${target_exit_debit:.2f} or lower to EXIT")
    print(f"Polling Interval:         Every 5 Minutes (300s)")
    print("========================================================\n")

    log(f"Tracking option quotes: Short {short_sym} | Long {long_sym}")

    while True:
        now_et = datetime.datetime.now(datetime.timezone.utc).astimezone(datetime.timezone(datetime.timedelta(hours=-4)))
        
        # Stop tracking after market close (16:00 ET)
        if now_et.hour >= 16:
            log("Market session closed (16:00 ET). Holding to expiration settlement.")
            spx_close, _ = get_latest_price('SPY')
            spx_close = spx_close if spx_close else spx_1000
            
            if direction == 'PUT':
                exp_loss = max(0.0, estimated_short_strike - spx_close) - max(0.0, estimated_long_strike - spx_close)
            else:
                exp_loss = max(0.0, spx_close - estimated_short_strike) - max(0.0, spx_close - estimated_long_strike)

            gross_pnl = (user_credit - exp_loss) * 100.0
            net_pnl = gross_pnl - 2.00 # Commission estimate
            win = net_pnl > 0

            update_trade_exit(trade_id, '16:00:00', exp_loss, 'EXPIRATION', gross_pnl, net_pnl, win)
            log(f"Trade ID {trade_id} updated in DB: EXPIRATION Settlement | Net PnL: ${net_pnl:+.2f}")
            break

        short_snap = get_option_snapshot(short_sym)
        long_snap = get_option_snapshot(long_sym)

        if short_snap and long_snap:
            current_spread_debit = short_snap['mid'] - long_snap['mid']
            unrealized_pnl = (user_credit - current_spread_debit) * 100.0
            
            log(f"Current Spread Debit: ${current_spread_debit:.2f} | Unrealized PnL: ${unrealized_pnl:+.2f}")

            if current_spread_debit <= target_exit_debit:
                gross_pnl = (user_credit - current_spread_debit) * 100.0
                net_pnl = gross_pnl - 2.00
                win = net_pnl > 0

                exit_time_str = now_et.strftime('%H:%M:%S')
                update_trade_exit(trade_id, exit_time_str, current_spread_debit, 'PROFIT_TARGET', gross_pnl, net_pnl, win)

                print("\n========================================================")
                print("🎯 PROFIT TARGET REACHED (60% Credit Captured)!")
                print(f"Current Closing Debit: ${current_spread_debit:.2f} <= Target ${target_exit_debit:.2f}")
                print(f"ACTION: CLOSE TRADE NOW FOR +${net_pnl:.2f} NET PROFIT")
                print(f"Database Record ID {trade_id} Updated to PROFIT_TARGET.")
                print("========================================================\n")
                break
        else:
            p, _ = get_latest_price('SPY')
            if p:
                log(f"Monitoring underlying price: ${p:.2f} (Target Exit Debit: ${target_exit_debit:.2f})")

        time.sleep(POLL_INTERVAL_SECONDS)

if __name__ == '__main__':
    run_signal_tracker()
