"""
One-shot script to download and save all historical 30-minute intraday bars into backtest/data/spx_intraday_30m.csv
Includes rate limit handling & single local storage to prevent rate limits during backtests.
"""

import os
import time
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

def fetch_and_save_30m_bars():
    data_dir = 'backtest/data'
    os.makedirs(data_dir, exist_ok=True)
    out_csv = os.path.join(data_dir, 'spx_intraday_30m.csv')

    api_key = os.getenv('APCA_API_KEY_ID')
    secret_key = os.getenv('APCA_API_SECRET_KEY')
    polygon_key = os.getenv('POLYGON_API_KEY')

    # 1. Try Polygon.io if POLYGON_API_KEY exists
    if polygon_key:
        print("Fetching 30-minute SPX intraday bars from Polygon.io...")
        url = f"https://api.polygon.io/v2/aggs/ticker/I:SPX/range/30/minute/2016-09-01/2023-12-31?adjusted=true&sort=asc&limit=50000&apiKey={polygon_key}"
        res = requests.get(url)
        if res.status_code == 200:
            results = res.json().get('results', [])
            records = []
            for r in results:
                dt = pd.to_datetime(r['t'], unit='ms', utc=True).tz_convert('America/New_York')
                records.append({
                    'timestamp': dt.strftime('%Y-%m-%d %H:%M:%S'),
                    'date': dt.strftime('%Y-%m-%d'),
                    'time': dt.strftime('%H:%M:%S'),
                    'open': r['o'],
                    'high': r['h'],
                    'low': r['l'],
                    'close': r['c'],
                    'volume': r.get('v', 0)
                })
            df = pd.DataFrame(records)
            df.to_csv(out_csv, index=False)
            print(f"Successfully saved {len(df)} 30m bars from Polygon.io to {out_csv}")
            return

    # 2. Fallback to Alpaca Market Data API (which supports multi-year 30m historical bars)
    print("Fetching 30-minute intraday bars via Market Data API (Alpaca / SPY)...")
    headers = {
        'APCA-API-KEY-ID': api_key,
        'APCA-API-SECRET-KEY': secret_key
    }
    
    all_bars = []
    page_token = None
    start = '2016-09-01T00:00:00Z'
    end = '2023-12-31T23:59:59Z'

    while True:
        url = f'https://data.alpaca.markets/v2/stocks/bars?symbols=SPY&timeframe=30Min&start={start}&end={end}&limit=10000'
        if page_token:
            url += f'&page_token={page_token}'
        
        res = requests.get(url, headers=headers)
        if res.status_code != 200:
            print(f"API Error ({res.status_code}): {res.text}")
            break
            
        data = res.json()
        bars = data.get('bars', {}).get('SPY', [])
        if not bars:
            break
            
        all_bars.extend(bars)
        page_token = data.get('next_page_token')
        if not page_token:
            break
            
        time.sleep(0.1)  # Rate limit headroom

    records = []
    for b in all_bars:
        dt = pd.to_datetime(b['t']).tz_convert('America/New_York')
        records.append({
            'timestamp': dt.strftime('%Y-%m-%d %H:%M:%S'),
            'date': dt.strftime('%Y-%m-%d'),
            'time': dt.strftime('%H:%M:%S'),
            'open': b['o'],
            'high': b['h'],
            'low': b['l'],
            'close': b['c'],
            'volume': b['v']
        })

    df = pd.DataFrame(records)
    df.to_csv(out_csv, index=False)
    print(f"Single-call fetch complete! Saved {len(df)} 30-minute bars locally to {out_csv}")

if __name__ == '__main__':
    fetch_and_save_30m_bars()
