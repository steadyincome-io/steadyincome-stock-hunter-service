# Alpaca Dip/DCA Trader

`alpaca_dip_dca_trader.py` paper-trades the rolling-peak dip/DCA strategy. The SQLite database determines which stocks qualify; Alpaca provides quotes, account and position data, and order execution.

Trading is disabled by default. The script only submits or cancels orders when `ALPACA_ENABLE_TRADING=true` is explicitly set.

## Setup

From the repository root:

```bash
source venv/bin/activate
```

If this checkout uses `.venv` instead, activate that environment:

```bash
source .venv/bin/activate
```

Copy the example configuration and edit `trades/.env` with your Alpaca paper credentials:

```bash
cp trades/.env.example trades/.env
```

You can also launch the trader from inside `trades/`; it still loads the `.env` next to the script:

```bash
cd trades
source ../venv/bin/activate
python alpaca_dip_dca_trader.py --once --dry-run
```

Relative database and state paths from `.env` are resolved from the repository root, so they work from either directory.

The default trading endpoint is:

```text
https://paper-api.alpaca.markets/v2
```

Quotes use `https://data.alpaca.markets`. Keep `ALPACA_ENABLE_TRADING=false` while testing.

## Run a dry run once

From the repository root:

```bash
source venv/bin/activate
python trades/alpaca_dip_dca_trader.py --once --dry-run
```

`--dry-run` prevents order submission and cancellation regardless of the value of `ALPACA_ENABLE_TRADING`.

## Run continuously

The default polling interval is 1,800 seconds (30 minutes):

```bash
source venv/bin/activate
python trades/alpaca_dip_dca_trader.py
```

To enable actual paper orders, set this in `trades/.env` after reviewing dry-run logs:

```text
ALPACA_ENABLE_TRADING=true
```

Then start the trader without `--dry-run`:

```bash
python trades/alpaca_dip_dca_trader.py
```

Stop the continuous process with `Ctrl-C`.

Each cycle runs immediately at startup and then sleeps for the configured interval. A normal cycle may only log the universe/account summary and the cycle-complete message when no buy or sell signal is present.

For each eligible symbol, the trader seeds its initial rolling peak from the most recent closing price before today (Friday's close when starting on Monday). It then logs the current quote, reference peak, drawdown, step count, and either `SIGNAL` or `no signal`. A symbol that is at least one 3% step below that reference can receive an initial DCA lot even if it has no existing position.

## Command-line flags

| Flag | Default | Description |
| --- | --- | --- |
| `--once` | off | Run one strategy check and exit. |
| `--dry-run` | off | Never submit or cancel orders. |
| `--db-path PATH` | `drawdown_analyzer.db` | SQLite database containing the stock universe and strategy snapshots. |
| `--state-path PATH` | `trades/.alpaca_dip_dca_state.json` | File used to persist rolling peaks, DCA steps, and sell-order state. |
| `--interval SECONDS` | `1800` | Polling interval for continuous mode. The script enforces a minimum of 60 seconds. |
| `--help` | — | Show argparse help and exit. |

Examples:

```bash
# One check using a specific database
python trades/alpaca_dip_dca_trader.py --once --dry-run \
  --db-path /path/to/drawdown_analyzer.db

# Check every five minutes
python trades/alpaca_dip_dca_trader.py --interval 300
```

## Environment variables

Required:

| Variable | Description |
| --- | --- |
| `APCA_API_KEY_ID` | Alpaca paper API key ID. |
| `APCA_API_SECRET_KEY` | Alpaca paper API secret. |

Endpoints and safety:

| Variable | Default | Description |
| --- | --- | --- |
| `APCA_API_TRADING_BASE_URL` | `https://paper-api.alpaca.markets` | Alpaca trading API base URL; `/v2` is appended by the script. |
| `APCA_API_DATA_BASE_URL` | `https://data.alpaca.markets` | Alpaca market-data base URL. |
| `ALPACA_ENABLE_TRADING` | `false` | Must be `true` to allow order submission/cancellation. |
| `LOG_LEVEL` | `INFO` | Logging level, such as `DEBUG`, `INFO`, or `WARNING`. |

Strategy and paths:

| Variable | Default | Description |
| --- | --- | --- |
| `DRAWDOWN_DB_PATH` | `drawdown_analyzer.db` | Database path used unless overridden by `--db-path`. |
| `ALPACA_STATE_PATH` | `trades/.alpaca_dip_dca_state.json` | State path used unless overridden by `--state-path`. |
| `ALPACA_POLL_INTERVAL_SECONDS` | `1800` | Interval used unless overridden by `--interval`. |
| `ALPACA_DCA_LOT_NOTIONAL` | `50` | Dollar amount per DCA lot. |
| `ALPACA_CASH_BUFFER` | `500` | Cash reserved and not used for buys. |

The script loads `trades/.env` first, then fills in missing values from the repository `.env`. This makes the trade-specific configuration take precedence. Existing shell environment variables are not overwritten by dotenv loading.

## Safety notes

- Use paper credentials with the paper endpoint.
- Start with `--once --dry-run` and inspect the logs.
- The SQLite database must contain the expected strategy columns and tables.
- Do not commit `trades/.env` or the state file.
- The state file is operational state; preserve it between runs unless you intentionally want to reset strategy tracking.
- A position gets a sell order when the latest quote is at least 2% above its average entry price. The strategy no longer waits for a trailing pullback.
