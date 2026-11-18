# SPX 0DTE Selective Credit Spread Strategy

Python implementation of a selective 0DTE SPX put credit spread strategy using **free data sources**.

## Strategy Summary
- Observation window: 9:30 – 10:15 AM ET
- Decision / Entry time: ~10:30 AM ET
- Instrument: SPX 0DTE 15-point Put Credit Spreads
- Highly selective filters (gap, opening range, VIX, price action)

## Data Sources (Free Tier)
| Data                    | Source     | Notes                          |
|-------------------------|------------|--------------------------------|
| SPX price, Gap, Range   | yfinance   | Slight delay                   |
| VIX                     | yfinance   | Slight delay                   |
| Options Chain + Quotes  | Alpaca     | **Indicative only** (free plan)|

> ⚠️ Important: Alpaca free plan provides **indicative** options quotes, not real OPRA data.  
> Suitable for paper trading and testing. For live trading, upgrade to Algo Trader Plus ($99/mo).

## Requirements

```bash
pip install yfinance pandas pytz python-dotenv alpaca-py
```


## How to Run

```
python spx_0dte_strategy.py
```

or from the folder where .env files are. 

Run it around 10:25 – 10:35 AM ET on trading days.

