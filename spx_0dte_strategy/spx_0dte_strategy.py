"""
Selective 0DTE SPX Credit Spread Strategy
Hybrid free data version:
- yfinance → SPX price, VIX, Opening Range, Gap
- Alpaca free → Options chain (indicative data)
Decision time: ~10:30 AM ET
"""

import yfinance as yf
import pandas as pd
from datetime import datetime, time, timedelta
import pytz
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionChainRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest
from alpaca.trading.enums import ContractType
import os
from math import floor
from dotenv import load_dotenv

# ====================== CONFIG ======================
load_dotenv()  # Load API keys from .env file

ALPACA_API_KEY = os.getenv("APCA_API_KEY_ID")
ALPACA_SECRET_KEY = os.getenv("APCA_API_SECRET_KEY")

# Strategy parameters (matching our agreed rules)
MAX_GAP_DOWN = -0.40          # %
MAX_GAP_UP = 0.60             # %
MAX_OPENING_RANGE_PCT = 0.70  # % (9:30-10:15)
MAX_VIX = 22.0
MAX_VIX_CHANGE_PCT = 8.0      # %
MIN_CREDIT = 0.25             # points
SPREAD_WIDTH = 15             # points
TARGET_DELTA_LOW = 0.12
TARGET_DELTA_HIGH = 0.18
RISK_PER_TRADE_PCT = 2.0      # % of account equity

# Timezone
ET = pytz.timezone("US/Eastern")

# ====================== HELPER FUNCTIONS ======================

def get_current_et_time():
    return datetime.now(ET)

def is_market_open():
    now = get_current_et_time()
    if now.weekday() >= 5:  # Weekend
        return False
    market_open = time(9, 30)
    market_close = time(16, 0)
    return market_open <= now.time() <= market_close

def get_spx_data():
    """Get SPX price, previous close, and intraday bars using yfinance"""
    spx = yf.Ticker("^SPX")
    
    # Previous close
    hist = spx.history(period="5d")
    prev_close = hist['Close'].iloc[-2]
    
    # Today's data
    today = spx.history(period="1d", interval="1m")
    
    if today.empty:
        raise ValueError("No intraday data available for SPX")
    
    current_price = today['Close'].iloc[-1]
    open_price = today['Open'].iloc[0]
    
    # Opening range 9:30 - 10:15
    # Filter bars between 9:30 and 10:15 ET
    today.index = today.index.tz_convert(ET)
    mask = (today.index.time >= time(9, 30)) & (today.index.time <= time(10, 15))
    opening_bars = today.loc[mask]
    
    if opening_bars.empty:
        high_1015 = today['High'].max()
        low_1015 = today['Low'].min()
    else:
        high_1015 = opening_bars['High'].max()
        low_1015 = opening_bars['Low'].min()
    
    opening_range = high_1015 - low_1015
    opening_range_pct = (opening_range / open_price) * 100
    
    gap_pct = ((open_price - prev_close) / prev_close) * 100
    
    return {
        "current_price": round(current_price, 2),
        "open_price": round(open_price, 2),
        "prev_close": round(prev_close, 2),
        "gap_pct": round(gap_pct, 3),
        "opening_range": round(opening_range, 2),
        "opening_range_pct": round(opening_range_pct, 3),
        "high_1015": round(high_1015, 2),
        "low_1015": round(low_1015, 2)
    }

def get_vix_data():
    """Get current VIX and previous close"""
    vix = yf.Ticker("^VIX")
    hist = vix.history(period="5d")
    
    current_vix = hist['Close'].iloc[-1]
    prev_vix = hist['Close'].iloc[-2]
    vix_change_pct = ((current_vix - prev_vix) / prev_vix) * 100
    
    return {
        "current_vix": round(current_vix, 2),
        "prev_vix": round(prev_vix, 2),
        "vix_change_pct": round(vix_change_pct, 2)
    }

def check_filters(spx_data, vix_data):
    """Apply all market condition filters"""
    results = {}
    
    # 1. Gap Filter
    gap_ok = MAX_GAP_DOWN <= spx_data["gap_pct"] <= MAX_GAP_UP
    results["gap"] = {
        "value": spx_data["gap_pct"],
        "threshold": f"{MAX_GAP_DOWN}% to {MAX_GAP_UP}%",
        "calculation": f"({spx_data['open_price']:.2f} - {spx_data['prev_close']:.2f}) / {spx_data['prev_close']:.2f} × 100 = {spx_data['gap_pct']:.3f}%",
        "passed": gap_ok
    }
    
    # 2. Opening Range Filter
    range_ok = spx_data["opening_range_pct"] < MAX_OPENING_RANGE_PCT
    results["opening_range"] = {
        "value": f"{spx_data['opening_range']} pts ({spx_data['opening_range_pct']}%)",
        "threshold": f"< {MAX_OPENING_RANGE_PCT}%",
        "calculation": f"({spx_data['high_1015']:.2f} - {spx_data['low_1015']:.2f}) / {spx_data['open_price']:.2f} × 100 = {spx_data['opening_range_pct']:.3f}%",
        "passed": range_ok
    }
    
    # 3. VIX Level
    vix_level_ok = vix_data["current_vix"] < MAX_VIX
    results["vix_level"] = {
        "value": vix_data["current_vix"],
        "threshold": f"< {MAX_VIX}",
        "calculation": f"{vix_data['current_vix']:.2f} < {MAX_VIX:.2f}",
        "passed": vix_level_ok
    }
    
    # 4. VIX Change
    vix_change_ok = vix_data["vix_change_pct"] < MAX_VIX_CHANGE_PCT
    results["vix_change"] = {
        "value": f"{vix_data['vix_change_pct']}%",
        "threshold": f"< {MAX_VIX_CHANGE_PCT}%",
        "calculation": f"({vix_data['current_vix']:.2f} - {vix_data['prev_vix']:.2f}) / {vix_data['prev_vix']:.2f} × 100 = {vix_data['vix_change_pct']:.2f}%",
        "passed": vix_change_ok
    }
    
    # 5. Price action (simple version)
    price_above_open = spx_data["current_price"] >= spx_data["open_price"]
    results["price_action"] = {
        "value": f"Current {spx_data['current_price']} vs Open {spx_data['open_price']}",
        "threshold": "Above or near open",
        "calculation": f"{spx_data['current_price']:.2f} >= {spx_data['open_price']:.2f}",
        "passed": price_above_open
    }
    
    all_passed = all(r["passed"] for r in results.values())
    
    return results, all_passed


def print_hypothetical_put_strikes(spx_price):
    """Print theoretical short-put strikes for the strategy's OTM range."""
    print("\n--- Hypothetical Short Put Strikes ---")
    print(f"Based on SPX price: {spx_price:.2f}")
    print("OTM level   Calculated level   Practical strike")
    for otm_pct in (0.6, 1.0, 1.5, 1.8):
        calculated = spx_price * (1 - otm_pct / 100)
        practical = floor(calculated / 5) * 5
        print(f"{otm_pct:>5.1f}%      {calculated:>10.2f}        {practical:>8.0f}")

def get_option_chain_alpaca(underlying_price):
    """
    Get SPX options chain from Alpaca (indicative data on free plan)
    Note: Free plan = indicative quotes only
    """
    try:
        # Using TradingClient to get contracts
        trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)
        
        # Get today's expiration (0DTE)
        today = datetime.now(ET).date()
        
        req = GetOptionContractsRequest(
            underlying_symbols=["SPX"],
            expiration_date=today,
            type=ContractType.PUT,          # We prefer puts
            strike_price_gte=underlying_price - 150,
            strike_price_lte=underlying_price + 50,
        )
        
        contracts = trading_client.get_option_contracts(req)
        
        if not contracts or not contracts.option_contracts:
            print("No option contracts returned from Alpaca")
            return None
            
        return contracts.option_contracts
        
    except Exception as e:
        print(f"Error fetching options from Alpaca: {e}")
        return None

def find_credit_spread(contracts, underlying_price):
    """
    Find a suitable 15-point put credit spread
    Target delta roughly 0.12 - 0.18
    """
    if not contracts:
        return None
    
    # Sort by strike descending
    puts = sorted(
        [c for c in contracts if c.type == "put"],
        key=lambda x: float(x.strike_price),
        reverse=True
    )
    
    candidates = []
    
    for short in puts:
        short_strike = float(short.strike_price)
        
        # We want short strike roughly 0.8% - 1.5% OTM for 0DTE
        otm_pct = (underlying_price - short_strike) / underlying_price * 100
        if not (0.6 <= otm_pct <= 1.8):
            continue
            
        long_strike = short_strike - SPREAD_WIDTH
        
        # Find matching long put
        long = next((c for c in puts if float(c.strike_price) == long_strike), None)
        if not long:
            continue
        
        # Note: On free plan, bid/ask are indicative
        short_bid = float(short.close_price or 0)  # fallback
        long_ask = float(long.close_price or 0)
        
        credit = short_bid - long_ask
        
        if credit >= MIN_CREDIT:
            candidates.append({
                "short_strike": short_strike,
                "long_strike": long_strike,
                "credit": round(credit, 2),
                "short_symbol": short.symbol,
                "long_symbol": long.symbol,
                "width": SPREAD_WIDTH
            })
    
    # Return the one with highest credit among reasonable ones
    if candidates:
        return max(candidates, key=lambda x: x["credit"])
    return None

# ====================== MAIN ======================

def main():
    print("=" * 60)
    print("SPX 0DTE Selective Credit Spread Strategy")
    print("Decision Time Window: ~10:30 AM ET")
    print("=" * 60)
    
    now = get_current_et_time()
    print(f"Current ET Time: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
    
    if not is_market_open():
        print("Market is closed. Exiting.")
        return
    
    # 1. Get market data from yfinance
    print("Fetching SPX data from yfinance...")
    try:
        spx_data = get_spx_data()
        vix_data = get_vix_data()
    except Exception as e:
        print(f"Error fetching market data: {e}")
        return
    
    print("\n--- Market Data ---")
    print(f"SPX Current     : {spx_data['current_price']}")
    print(f"SPX Open        : {spx_data['open_price']}")
    print(f"Previous Close  : {spx_data['prev_close']}")
    print(f"Gap             : {spx_data['gap_pct']}%")
    print(f"Opening Range   : {spx_data['opening_range']} pts ({spx_data['opening_range_pct']}%)")
    print(f"VIX             : {vix_data['current_vix']} (change {vix_data['vix_change_pct']}%)")
    print_hypothetical_put_strikes(spx_data["current_price"])
    
    # 2. Run filters
    print("\n--- Filter Check ---")
    filter_results, all_passed = check_filters(spx_data, vix_data)
    
    for name, res in filter_results.items():
        status = "✅ PASS" if res["passed"] else "❌ FAIL"
        print(f"{name:15} : {res['value']}  (Threshold: {res['threshold']}) → {status}")
        print(f"{'':18} Calculation: {res['calculation']}")
    
    if not all_passed:
        print("\n🚫 Trade NOT allowed. One or more filters failed.")
        return
    
    print("\n✅ All filters passed → Looking for trade setup...")
    
    # 3. Get options chain from Alpaca
    print("\nFetching options chain from Alpaca (indicative data)...")
    contracts = get_option_chain_alpaca(spx_data["current_price"])
    
    if not contracts:
        print("Could not retrieve options chain. Exiting.")
        return
    
    # 4. Find credit spread
    spread = find_credit_spread(contracts, spx_data["current_price"])
    
    if not spread:
        print("No suitable credit spread found that meets minimum credit.")
        return
    
    # 5. Output trade
    print("\n" + "=" * 60)
    print("🎯 SUGGESTED TRADE")
    print("=" * 60)
    print(f"Type           : Put Credit Spread (Bull Put Spread)")
    print(f"Short Strike   : {spread['short_strike']}")
    print(f"Long Strike    : {spread['long_strike']}")
    print(f"Width          : {spread['width']} points")
    print(f"Estimated Credit: {spread['credit']} points (${spread['credit'] * 100:.0f})")
    print(f"Max Risk       : {(spread['width'] - spread['credit']) * 100:.0f} per spread")
    print(f"Short Symbol   : {spread['short_symbol']}")
    print(f"Long Symbol    : {spread['long_symbol']}")
    print("=" * 60)
    print("\n⚠️  Note: Alpaca free plan uses INDICATIVE options data.")
    print("   Double-check prices in your broker before entering live.")
    print("=" * 60)

if __name__ == "__main__":
    main()
