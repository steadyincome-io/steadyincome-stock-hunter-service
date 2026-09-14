"""
0DTE SPX / SPXW Live Signal & Trade Tracker Script
- Fetches real 4-digit SPX index values (^GSPC) for 09:30 AM open, 10:00 AM price, and 10:30 AM price
- Direction Logic: Uses 10:30 AM price vs 09:30 AM open price to determine final trade signal
- Fetches REAL SPXW 0DTE options chain from Alpaca and computes Black-Scholes delta
- Selects short strike closest to TARGET_DELTA from live chain data
- Solicits user input (Trade placed? [y/n] & Net Credit Collected)
- Tracks 60% Profit Target exit polling every 5 minutes (300 seconds)
"""

import os
import sys
import time
import math
import requests
import datetime
import pandas as pd
import numpy as np
import yfinance as yf
from dotenv import load_dotenv
from scipy.stats import norm

# Import local SQLite helper module
from db import init_db, log_trade_entry, update_trade_exit, get_all_trades_df

load_dotenv()

# --- Config Knobs ---
TARGET_DELTA = 0.15
SPREAD_WIDTH = 25.0
PROFIT_TARGET_PCT = 0.60
POLL_INTERVAL_SECONDS = 300  # 5-minute polling to save rate limits
RISK_FREE_RATE = 0.05        # Approximate risk-free rate for BS model

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

# ========================= BLACK-SCHOLES DELTA =========================

def bs_delta(S, K, T, r, sigma, option_type='call'):
    """
    Compute Black-Scholes delta for a European option.
    S: underlying price (SPX)
    K: strike price
    T: time to expiration in years (fraction of day for 0DTE)
    r: risk-free rate
    sigma: implied volatility
    option_type: 'call' or 'put'
    """
    if T <= 0:
        T = 1e-6  # Avoid division by zero on expiration day
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    if option_type == 'call':
        return norm.cdf(d1)
    else:
        return norm.cdf(d1) - 1.0

def implied_vol_from_price(S, K, T, r, market_price, option_type='call', tol=1e-5, max_iter=100):
    """
    Newton-Raphson method to back out implied volatility from market mid price.
    Returns IV or a default fallback if convergence fails.
    """
    if market_price <= 0:
        return 0.20  # fallback
    sigma = 0.20  # initial guess
    for _ in range(max_iter):
        if T <= 0:
            T = 1e-6
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        if option_type == 'call':
            bs_price = S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
        else:
            bs_price = K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        
        vega = S * norm.pdf(d1) * math.sqrt(T)
        if vega < 1e-12:
            break
        
        diff = bs_price - market_price
        sigma -= diff / vega
        
        if sigma <= 0.001:
            sigma = 0.001
        if abs(diff) < tol:
            break
    
    return max(sigma, 0.01)

def compute_time_to_expiry_years():
    """
    Compute T (in years) from now until 4:00 PM ET today (SPX 0DTE expiration).
    For 0DTE, this is a fraction of a day.
    """
    now_et = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-4)))
    expiry_et = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    remaining_seconds = max((expiry_et - now_et).total_seconds(), 60)  # At least 1 minute
    return remaining_seconds / (365.25 * 24 * 3600)

# ========================= SPX PRICE FETCHING =========================

def get_spx_index_bars():
    """Fetch exact 4-digit SPX Index (^GSPC) 1m intraday bars for 09:30 Open, 10:00, and 10:30 prices"""
    try:
        df = yf.download('^GSPC', period='1d', interval='1m', progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        log(f"Error fetching SPX index bars: {e}")
        return pd.DataFrame()

def get_latest_price_spy():
    """Fetch latest minute bar for SPY from Alpaca Market Data"""
    url = f"{BASE_URL}/v2/stocks/bars?symbols=SPY&timeframe=1Min&limit=5"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code == 200:
            bars = res.json().get('bars', {}).get('SPY', [])
            if bars:
                latest = bars[-1]
                return float(latest['c']), latest['t']
    except Exception as e:
        log(f"Error fetching SPY price: {e}")
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

# ========================= OPTIONS CHAIN & DELTA SELECTION =========================

def fetch_spxw_chain(expiry_date, option_type, strike_min, strike_max):
    """
    Fetch SPXW options chain from Alpaca for a given expiry, type, and strike range.
    Returns dict of {symbol: {bid, ask, mid, strike}} sorted by strike.
    """
    url = (
        f"{BASE_URL}/v1beta1/options/snapshots/SPXW"
        f"?feed=indicative"
        f"&expiration_date={expiry_date}"
        f"&type={option_type}"
        f"&strike_price_gte={strike_min}"
        f"&strike_price_lte={strike_max}"
        f"&limit=100"
    )
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        if res.status_code == 200:
            snapshots = res.json().get('snapshots', {})
            chain = {}
            for sym, snap in snapshots.items():
                quote = snap.get('latestQuote', {})
                bid = float(quote.get('bp', 0.0))
                ask = float(quote.get('ap', 0.0))
                mid = (bid + ask) / 2.0 if (bid + ask) > 0 else 0.0
                # Extract strike from symbol: SPXW260915C07600000 -> last 8 chars / 1000
                strike = int(sym[-8:]) / 1000.0
                chain[sym] = {'bid': bid, 'ask': ask, 'mid': mid, 'strike': strike}
            return dict(sorted(chain.items(), key=lambda x: x[1]['strike']))
        else:
            log(f"Options chain API returned status {res.status_code}: {res.text[:200]}")
    except Exception as e:
        log(f"Error fetching SPXW chain: {e}")
    return {}

def find_strike_by_delta(chain, spx_price, option_type, target_delta):
    """
    Given an options chain dict, compute BS delta for each strike and return
    the symbol + strike closest to the target delta (absolute value for puts).
    
    Returns: (best_symbol, best_strike, best_delta, delta_table)
    """
    T = compute_time_to_expiry_years()
    r = RISK_FREE_RATE
    
    delta_table = []
    
    for sym, data in chain.items():
        strike = data['strike']
        mid = data['mid']
        
        if mid <= 0.01:
            continue
        
        # Compute implied vol from market price
        iv = implied_vol_from_price(spx_price, strike, T, r, mid, option_type)
        
        # Compute delta using the IV
        delta = bs_delta(spx_price, strike, T, r, iv, option_type)
        abs_delta = abs(delta)
        
        delta_table.append({
            'symbol': sym,
            'strike': strike,
            'bid': data['bid'],
            'ask': data['ask'],
            'mid': mid,
            'iv': iv,
            'delta': delta,
            'abs_delta': abs_delta
        })
    
    if not delta_table:
        return None, None, None, []
    
    # Find closest to target delta
    best = min(delta_table, key=lambda x: abs(x['abs_delta'] - target_delta))
    
    return best['symbol'], best['strike'], best['delta'], delta_table

# ========================= PRICE FETCH ORCHESTRATION =========================

def fetch_spx_open_1000_and_1030():
    """Fetch 4-digit SPX Index (^GSPC) 09:30 Open, 10:00 Price, and 10:30 Confirmation Price"""
    log("Fetching 4-digit SPX Index (^GSPC) intraday price data for 09:30, 10:00, and 10:30 AM...")
    
    df = get_spx_index_bars()
    if df.empty:
        log("Fallback to SPY price scaling (SPY * 10)...")
        spy_p, _ = get_latest_price_spy()
        spy_p = spy_p * 10.0 if spy_p else 7600.0
        return spy_p, spy_p, spy_p

    # Filter 09:30 open bar
    df_0930 = df.between_time('09:30', '09:31')
    spx_open = float(df_0930['Open'].iloc[0]) if not df_0930.empty else float(df['Open'].iloc[0])

    # Filter 10:00 entry bar
    df_1000 = df.between_time('10:00', '10:01')
    if not df_1000.empty:
        spx_1000 = float(df_1000['Close'].iloc[0])
    else:
        spx_1000 = float(df['Close'].iloc[-1])

    # Filter 10:30 bar
    df_1030 = df.between_time('10:30', '10:31')
    if not df_1030.empty:
        spx_1030 = float(df_1030['Close'].iloc[0])
    else:
        spx_1030 = float(df['Close'].iloc[-1])

    return spx_open, spx_1000, spx_1030

# ========================= MAIN DAEMON LOOP =========================

def run_signal_tracker():
    init_db()
    print("=================================================================")
    print("   0DTE SPX CREDIT SPREAD LIVE DAEMON & DB TRACKER               ")
    print(f"   Storage: spx_0dte_strategy/spx_spread_trades.db")
    print(f"   Settings: Delta={TARGET_DELTA} | Width=${SPREAD_WIDTH} | Target={int(PROFIT_TARGET_PCT*100)}%")
    print(f"   Signal Logic: Decision based on 09:30 Open vs 10:30 AM Price")
    print(f"   Delta Source: LIVE SPXW chain from Alpaca + Black-Scholes")
    print("=================================================================\n")

    spx_open, spx_1000, spx_1030 = fetch_spx_open_1000_and_1030()

    ret_1000 = (spx_1000 / spx_open) - 1.0
    ret_1030 = (spx_1030 / spx_open) - 1.0  # Final decision return based on 09:30 vs 10:30
    
    today_str = datetime.datetime.now().strftime('%Y-%m-%d')
    now_time_str = datetime.datetime.now().strftime('%H:%M:%S')

    print("\n---------------- DIRECTION & PRICE SUMMARY ----------------")
    print(f"09:30 SPX Open Price:          ${spx_open:.2f}")
    print(f"10:00 SPX Price (1st Check):   ${spx_1000:.2f} (Return: {ret_1000*100:+.3f}%)")
    print(f"10:30 SPX Price (Decision):    ${spx_1030:.2f} (Return: {ret_1030*100:+.3f}%)")
    print("----------------------------------------------------------")

    # Signal Decision: Uses 09:30 AM vs 10:30 AM price
    if ret_1030 > 0:
        direction = "PUT"
        option_type = "put"
        print("SIGNAL: 10:30 AM Price ABOVE 09:30 Open -> SELL PUT CREDIT SPREAD (Bullish)")
    elif ret_1030 < 0:
        direction = "CALL"
        option_type = "call"
        print("SIGNAL: 10:30 AM Price BELOW 09:30 Open -> SELL CALL CREDIT SPREAD (Bearish)")
    else:
        print("SIGNAL: FLAT AT 10:30 AM -> NO TRADE")
        return

    # ---- REAL DELTA STRIKE SELECTION FROM LIVE CHAIN ----
    log("Fetching LIVE SPXW 0DTE options chain from Alpaca for delta calculation...")
    
    today_expiry = datetime.datetime.now().strftime('%Y-%m-%d')
    
    # Define strike search range: ±3% from current SPX for OTM options
    if option_type == 'call':
        strike_min = int(spx_1030)           # ATM
        strike_max = int(spx_1030 * 1.03)    # Up to 3% OTM for calls
    else:
        strike_min = int(spx_1030 * 0.97)    # Down to 3% OTM for puts
        strike_max = int(spx_1030)           # ATM
    
    chain = fetch_spxw_chain(today_expiry, option_type, strike_min, strike_max)
    
    if not chain:
        log("WARNING: Could not fetch SPXW options chain. Falling back to estimated strikes.")
        # Fallback: approximate 0.15 delta as ~0.3% OTM for 0DTE
        if option_type == 'call':
            estimated_short_strike = int(round((spx_1030 * 1.003) / 5.0) * 5)
            estimated_long_strike = estimated_short_strike + SPREAD_WIDTH
        else:
            estimated_short_strike = int(round((spx_1030 * 0.997) / 5.0) * 5)
            estimated_long_strike = estimated_short_strike - SPREAD_WIDTH
        short_sym = None
        long_sym = None
        best_delta = TARGET_DELTA
    else:
        best_sym, best_strike, best_delta, delta_table = find_strike_by_delta(
            chain, spx_1030, option_type, TARGET_DELTA
        )
        
        if best_sym is None:
            log("Could not determine delta from chain. Exiting.")
            return
        
        estimated_short_strike = best_strike
        if option_type == 'call':
            estimated_long_strike = estimated_short_strike + SPREAD_WIDTH
        else:
            estimated_long_strike = estimated_short_strike - SPREAD_WIDTH
        
        # Build the long leg symbol
        long_strike_int = int(estimated_long_strike * 1000)
        today_sym_date = datetime.datetime.now().strftime('%y%m%d')
        opt_char = 'C' if option_type == 'call' else 'P'
        long_sym = f"SPXW{today_sym_date}{opt_char}{long_strike_int:08d}"
        short_sym = best_sym
        
        # Print delta table (top candidates near target)
        print(f"\n---------------- LIVE DELTA TABLE ({option_type.upper()} OTM) ----------------")
        print(f"{'Strike':>8s}  {'Bid':>8s}  {'Ask':>8s}  {'Mid':>8s}  {'IV':>6s}  {'Delta':>8s}  {'|Δ|':>6s}")
        print("-" * 68)
        
        # Show ~10 strikes nearest to target delta
        sorted_by_delta = sorted(delta_table, key=lambda x: abs(x['abs_delta'] - TARGET_DELTA))
        for row in sorted_by_delta[:15]:
            marker = " <<<" if row['symbol'] == best_sym else ""
            print(f"  {row['strike']:>7.0f}  {row['bid']:>8.2f}  {row['ask']:>8.2f}  "
                  f"{row['mid']:>8.2f}  {row['iv']*100:>5.1f}%  {row['delta']:>+8.4f}  "
                  f"{row['abs_delta']:>6.4f}{marker}")
        print("-" * 68)

    print(f"\n---------------- RECOMMEND STRIKE SELECTION ----------------")
    print(f"Decision Direction:    {direction} CREDIT SPREAD")
    print(f"Short Strike ({TARGET_DELTA}Δ):    ${estimated_short_strike:.0f} (actual Δ = {abs(best_delta):.4f})")
    print(f"Long Strike:           ${estimated_long_strike:.0f} (${SPREAD_WIDTH:.0f} width)")
    if short_sym:
        print(f"Short Symbol:          {short_sym}")
    if long_sym:
        print(f"Long Symbol:           {long_sym}")
    print("-----------------------------------------------------------\n")

    user_input = input("Did you place this trade? (y/n): ").strip().lower()
    if user_input not in ['y', 'yes']:
        log("User skipped trade. Exiting tracker daemon.")
        return

    try:
        user_credit = float(input("Enter net credit collected per index point (e.g. 1.25): ").strip())
    except ValueError:
        user_credit = 1.25
        log("Defaulting net credit collected to $1.25")

    target_exit_debit = user_credit * (1.0 - PROFIT_TARGET_PCT)
    max_profit = user_credit * 100.0
    max_loss = (SPREAD_WIDTH - user_credit) * 100.0

    # If we don't have symbols from chain, build them
    if not short_sym:
        today_sym_date = datetime.datetime.now().strftime('%y%m%d')
        opt_char = 'C' if direction == 'CALL' else 'P'
        short_sym = f"SPXW{today_sym_date}{opt_char}{int(estimated_short_strike*1000):08d}"
        long_sym = f"SPXW{today_sym_date}{opt_char}{int(estimated_long_strike*1000):08d}"

    # Log trade entry into SQLite DB
    trade_id = log_trade_entry({
        'trade_date': today_str,
        'entry_time': now_time_str,
        'spx_open': spx_open,
        'spx_entry': spx_1030,
        'open_to_entry_return': ret_1030,
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
            spx_close, _, _ = fetch_spx_open_1000_and_1030()
            
            if direction == 'PUT':
                exp_loss = max(0.0, estimated_short_strike - spx_close) - max(0.0, estimated_long_strike - spx_close)
            else:
                exp_loss = max(0.0, spx_close - estimated_short_strike) - max(0.0, spx_close - estimated_long_strike)

            gross_pnl = (user_credit - exp_loss) * 100.0
            net_pnl = gross_pnl - 2.00  # Commission estimate
            win = net_pnl > 0

            update_trade_exit(trade_id, '16:00:00', exp_loss, 'EXPIRATION', gross_pnl, net_pnl, win)
            log(f"Trade ID {trade_id} updated in DB: EXPIRATION Settlement | Net PnL: ${net_pnl:+.2f}")
            break

        short_snap = get_option_snapshot(short_sym)
        long_snap = get_option_snapshot(long_sym)

        if short_snap and long_snap:
            current_spread_debit = short_snap['mid'] - long_snap['mid']
            unrealized_pnl = (user_credit - current_spread_debit) * 100.0
            
            log(f"Short({short_sym}) mid=${short_snap['mid']:.2f} | Long({long_sym}) mid=${long_snap['mid']:.2f}")
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
            p_spy, _ = get_latest_price_spy()
            if p_spy:
                log(f"Monitoring underlying price (SPY ${p_spy:.2f} / SPX ~${p_spy*10:.1f}) | Target Debit: ${target_exit_debit:.2f}")

        time.sleep(POLL_INTERVAL_SECONDS)

if __name__ == '__main__':
    run_signal_tracker()
