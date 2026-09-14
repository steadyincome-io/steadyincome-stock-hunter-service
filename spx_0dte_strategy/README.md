# SPX 0DTE Credit Spread Strategy Directory

This directory contains the dedicated live execution daemon and SQLite database tracker for the **0DTE SPX Credit Spread Strategy**.

## File Structure

```
spx_0dte_strategy/
├── db.py                   # SQLite database helper module
├── live_trader_daemon.py   # Live signal & 5-minute option price tracker daemon
└── spx_spread_trades.db    # Local SQLite database storing all live trade entries & exits
```

## Strategy Parameters (Pre-Configured)
- **Signal Time**: 09:30 AM Open vs 10:30 AM Price (with 10:00 AM as informational check)
- **Target Delta**: `0.15` (computed via Black-Scholes from live SPXW chain)
- **Spread Width**: `$25.00`
- **Profit Target**: `60%`
- **Rate Limit Control**: 5-minute polling interval (300 seconds)
- **Options Symbol**: `SPXW` (weekly 0DTE SPX options via Alpaca indicative feed)

## Delta Calculation

The daemon fetches the **real SPXW 0DTE options chain** from Alpaca's `/v1beta1/options/snapshots/SPXW` endpoint, then:

1. Computes **implied volatility** from market mid-prices using Newton-Raphson
2. Computes **Black-Scholes delta** for each available strike
3. Selects the strike closest to the target delta (0.15)

This replaces the old fixed-percentage offset estimation with actual market-based delta selection.

## How to Run Live

From the workspace root directory:

```bash
source venv/bin/activate
python spx_0dte_strategy/live_trader_daemon.py
```

## How to Query Live Trades Database

You can inspect all recorded trades at any time via Python:

```python
from spx_0dte_strategy.db import get_all_trades_df
df = get_all_trades_df()
print(df)
```

## Dependencies

- `scipy` (for `norm.cdf` / `norm.pdf` in Black-Scholes)
- `yfinance` (for ^GSPC SPX index intraday bars)
- `requests` (for Alpaca Market Data API)
- `pandas`, `numpy`, `python-dotenv`
