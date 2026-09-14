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
- **Signal Time**: 10:00 AM ET (evaluates 09:30 Open vs. 10:00 AM Entry price)
- **Target Delta**: `0.15`
- **Spread Width**: `$25.00`
- **Profit Target**: `60%`
- **Rate Limit Control**: 5-minute polling interval (300 seconds)

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
