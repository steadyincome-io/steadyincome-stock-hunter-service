"""
Single fast downloader for 30m intraday bars
"""

import os
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

def fetch_fast():
    api_key = os.getenv('APCA_API_KEY_ID')
    secret_key = os.getenv('APCA_API_SECRET_KEY')
    headers = {
        'APCA-API-KEY-ID': api_key,
        'APCA-API-SECRET-KEY': secret_key
    }

    print("Starting single-pass download of 30-minute intraday bars...")
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
            print(f"Error ({res.status_code}): {res.text}")
            break
            
        data = res.json()
        bars = data.get('bars', {}).get('SPY', [])
        if not bars:
            break
            
        all_bars.extend(bars)
        page_token = data.get('next_page_token')
        if not page_token:
            break

    print(f"Downloaded {len(all_bars)} bars total.")
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
    out_path = 'backtest/data/spx_intraday_30m.csv'
    df.to_csv(out_path, index=False)
    print(f"Saved {len(df)} 30-minute intraday bars to {out_path}!")

if __name__ == '__main__':
    fetch_fast()
