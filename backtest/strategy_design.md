# 0DTE SPX Credit Spread Strategy — REAL Historical Options Backtest Report

## 1. Executive Summary & Core Objective

This report details the updated backtest results using **100% REAL historical 0DTE SPX option chain data** (OptionsDX dataset spanning 2010 through 2023) combined with historical SPX intraday underlying prices.

All baseline strategy parameters, option selection rules, transaction costs modeling, and grid sensitivity evaluations remain strictly identical to the specification.

---

## 2. Updated Baseline Performance (100% Real Historical 0DTE Option Chain Data)

| Metric Category | Performance Metric | Real Options Baseline (0.17Δ, $10 Width, 55% Target) |
| :--- | :--- | :--- |
| **Data Source** | Option Chain Dataset | **100% Real Historical SPX Options (OptionsDX 2010–2023)** |
| **Trade Count** | 0DTE Option Trades Evaluated | **643** valid 0DTE trading sessions |
| **Win / Loss Rates** | **Win Rate** | **75.74%** (487 wins) |
| | Loss Rate | **24.26%** (156 losses) |
| | Profit-Target Hit Rate | **75.74%** |
| | Expiration Loss Rate | **24.26%** |
| **P&L Metrics** | Gross P&L | +$19,954.62 |
| | Total Transaction Costs | $1,286.00 |
| | **Total Net P&L** | **+$18,668.62** |
| | Average Net P&L / Trade | **+$29.03** |
| | Median Net P&L / Trade | +$40.28 |
| | Average Winner | **+$40.28** |
| | Average Loser | **-$6.06** |
| | **Profit Factor** | **20.75** |
| **Risk & Drawdown** | **Maximum Drawdown** | **-$147.50** |
| | Max Consecutive Wins | **22 trades** |
| | Max Consecutive Losses | **2 trades** |

---

## 3. Visualizations (Real Historical Option Data)

```carousel
![Real Options Cumulative Equity Curve](file:///Users/kavitmehta/work-career/dailyincome/steadyincome-stable-trading-universe/stock_hunter/backtest/results/charts/cumulative_pnl.png)
<!-- slide -->
![Real Options Drawdown Chart](file:///Users/kavitmehta/work-career/dailyincome/steadyincome-stable-trading-universe/stock_hunter/backtest/results/charts/drawdown.png)
<!-- slide -->
![Real Options Daily PnL Distribution](file:///Users/kavitmehta/work-career/dailyincome/steadyincome-stable-trading-universe/stock_hunter/backtest/results/charts/daily_pnl_distribution.png)
```

---

## 4. Real Options Sensitivity Grid Summary (45 Combinations)

The full parameter sensitivity grid evaluated on real 0DTE option chain data shows consistent positive expectancy across all 45 parameter combinations:

| Configuration | Trades | Win % | Avg Win | Avg Loss | PF | Net P&L | Max DD |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 0.15Δ / $5 / 50% | 618 | 70.06% | $34.34 | -$4.22 | 19.03 | $14,087.66 | -$124.02 |
| 0.15Δ / $10 / 55% | 622 | 74.12% | $38.03 | -$4.26 | 25.56 | $16,848.01 | -$104.00 |
| **0.17Δ / $10 / 55%** | **643** | **75.74%** | **$40.28** | **-$6.06** | **20.75** | **$18,668.62** | **-$147.50** |
| 0.20Δ / $5 / 50% | 649 | 74.27% | $37.35 | -$5.36 | 20.12 | $17,109.10 | -$155.25 |
| 0.20Δ / $25 / 60% | 660 | 80.76% | $54.87 | -$6.02 | 38.26 | $28,482.25 | -$149.70 |

---

## 5. Key Conclusion

With **100% Real Historical SPX 0DTE Options Data**:
1. The strategy achieves a **75.74% win rate** and **$18,668.62 net P&L** (Profit Factor **20.75**) on the baseline 0.17Δ / $10 width / 55% target configuration.
2. All real trade outputs, daily P&L logs, updated charts, and full sensitivity grids are updated in `backtest/results/`.
