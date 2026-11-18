"""Backtest the SPX 0DTE market-condition filters.

Yahoo Finance limits intraday history.  For a 180-day run, the default 1h
interval approximates the 9:30-10:15 opening range with the first regular-
session bar (9:30-10:30).  Use --interval 5m for a more precise opening range
over the shorter history Yahoo makes available.
"""

from __future__ import annotations

import argparse
from datetime import time
from math import floor

import pandas as pd
import yfinance as yf


MAX_GAP_DOWN = -0.40
MAX_GAP_UP = 0.60
MAX_OPENING_RANGE_PCT = 0.70
MAX_VIX = 22.0
MAX_VIX_CHANGE_PCT = 8.0
ET = "America/New_York"
DECISION_TIME = time(10, 30)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest SPX 0DTE filter passes")
    parser.add_argument("--days", type=int, default=180, help="Calendar days to request (default: 180)")
    parser.add_argument(
        "--interval",
        choices=("1h", "5m", "1m"),
        default="1h",
        help="Yahoo bar interval; 1h supports the 180-day approximation",
    )
    parser.add_argument("--csv", help="Optional path for the detailed daily results CSV")
    parser.add_argument("--show-passes", action="store_true", help="Print every passing date")
    parser.add_argument("--show-failures", action="store_true", help="Print every evaluated date")
    return parser.parse_args()


def download(symbol: str, days: int, interval: str) -> pd.DataFrame:
    # Add a small buffer so the first requested session has a prior close.
    period_days = max(days + 10, 15)
    data = yf.download(
        symbol,
        period=f"{period_days}d",
        interval=interval,
        auto_adjust=False,
        prepost=False,
        progress=False,
        threads=False,
    )
    if data.empty:
        raise RuntimeError(f"Yahoo returned no data for {symbol}")
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    data.index = pd.to_datetime(data.index)
    if data.index.tz is None:
        data.index = data.index.tz_localize("UTC")
    return data.tz_convert(ET)


def regular_session(data: pd.DataFrame) -> pd.DataFrame:
    return data[(data.index.time >= time(9, 30)) & (data.index.time <= time(16, 0))].copy()


def session_rows(data: pd.DataFrame, days: int, interval: str) -> pd.DataFrame:
    data = regular_session(data)
    data["session"] = data.index.date
    sessions = list(data.groupby("session", sort=True))
    if len(sessions) < days:
        # Yahoo's period is calendar days, so this is normal on weekends.
        selected = sessions[-days:]
    else:
        selected = sessions[-days:]

    rows = []
    previous_close = None
    for session_date, bars in selected:
        if previous_close is None:
            previous_close = float(bars["Close"].iloc[0])
            continue

        opening = bars.iloc[:1] if interval == "1h" else bars[
            (bars.index.time >= time(9, 30)) & (bars.index.time <= time(10, 15))
        ]
        if opening.empty:
            previous_close = float(bars["Close"].iloc[-1])
            continue

        first_open = float(bars["Open"].iloc[0])
        decision_bars = bars[bars.index.time <= DECISION_TIME]
        if decision_bars.empty:
            previous_close = float(bars["Close"].iloc[-1])
            continue

        rows.append(
            {
                "date": str(session_date),
                "open": first_open,
                "previous_close": previous_close,
                "gap_pct": (first_open - previous_close) / previous_close * 100,
                "opening_range": float(opening["High"].max() - opening["Low"].min()),
                "opening_range_pct": float((opening["High"].max() - opening["Low"].min()) / first_open * 100),
                "current": float(decision_bars["Close"].iloc[-1]),
                "decision_price": float(decision_bars["Close"].iloc[-1]),
                "remaining_session_low": float(bars[bars.index > decision_bars.index[-1]]["Low"].min()),
            }
        )
        previous_close = float(bars["Close"].iloc[-1])
    return pd.DataFrame(rows)


def build_vix_rows(vix: pd.DataFrame, spx_rows: pd.DataFrame) -> pd.DataFrame:
    vix = regular_session(vix)
    vix["session"] = vix.index.date
    decision_vix = vix[vix.index.time <= DECISION_TIME]
    decision_values = decision_vix.groupby("session")["Close"].last()
    session_closes = vix.groupby("session")["Close"].last()
    result = []
    sessions = list(session_closes.index)
    for _, row in spx_rows.iterrows():
        date = str(row["date"])
        date_key = next((key for key in decision_values.index if str(key) == date), None)
        if date_key is None or date_key not in sessions:
            continue
        session_position = sessions.index(date_key)
        if session_position == 0:
            continue
        current = float(decision_values.loc[date_key])
        prior_close = float(session_closes.iloc[session_position - 1])
        result.append({"date": date, "vix": current, "vix_change_pct": (current - prior_close) / prior_close * 100})
    return pd.DataFrame(result)


def evaluate(spx: pd.DataFrame, vix: pd.DataFrame) -> pd.DataFrame:
    result = spx.merge(vix, on="date", how="inner")
    result["gap_pass"] = result["gap_pct"].between(MAX_GAP_DOWN, MAX_GAP_UP)
    result["opening_range_pass"] = result["opening_range_pct"] < MAX_OPENING_RANGE_PCT
    result["vix_level_pass"] = result["vix"] < MAX_VIX
    result["vix_change_pass"] = result["vix_change_pct"] < MAX_VIX_CHANGE_PCT
    result["price_action_pass"] = result["current"] >= result["open"]
    checks = [c for c in result.columns if c.endswith("_pass")]
    result["all_filters_pass"] = result[checks].all(axis=1)
    return result


def print_breach_summary(results: pd.DataFrame) -> None:
    passed = results[results["all_filters_pass"]].copy()
    print("\nHypothetical short-put breach check on filter-pass days:")
    print("(Strike rounded down to the nearest 5 SPX points; breach means the hourly low reached the strike.)")
    for offset in (0.6, 1.0, 1.5, 1.8):
        strikes = (passed["decision_price"] * (1 - offset / 100)).map(lambda value: floor(value / 5) * 5)
        breached = passed["remaining_session_low"] <= strikes
        print(f"  {offset:>3.1f}% OTM: breached {int(breached.sum())}/{len(passed)} days ({breached.mean() * 100:.1f}%)")


def main() -> None:
    args = parse_args()
    if args.days <= 0:
        raise SystemExit("--days must be positive")
    if args.interval in {"1m", "5m"} and args.days > 60:
        raise SystemExit("Yahoo generally limits 1m/5m intraday history to about 60 days; use --interval 1h for 180 days.")

    print(f"Downloading SPX/VIX data: days={args.days}, interval={args.interval}")
    spx = session_rows(download("^SPX", args.days, args.interval), args.days, args.interval)
    vix = download("^VIX", args.days, args.interval)
    results = evaluate(spx, build_vix_rows(vix, spx))
    if results.empty:
        raise SystemExit("No overlapping SPX/VIX sessions were available")

    print(f"Evaluated sessions: {len(results)}")
    print(f"All filters passed: {int(results['all_filters_pass'].sum())}")
    print(f"Pass rate: {results['all_filters_pass'].mean() * 100:.1f}%")
    print("Filter pass counts:")
    for column in ("gap_pass", "opening_range_pass", "vix_level_pass", "vix_change_pass", "price_action_pass"):
        print(f"  {column.removesuffix('_pass'):18} {int(results[column].sum())}/{len(results)}")
    print_breach_summary(results)
    if args.interval == "1h":
        print("Note: 1h mode approximates the 9:30-10:15 range with the first hourly bar.")

    display = results if args.show_failures else results[results["all_filters_pass"]] if args.show_passes else None
    if display is not None:
        columns = ["date", "gap_pct", "opening_range_pct", "vix", "vix_change_pct", "current", "all_filters_pass"]
        print(display[columns].to_string(index=False, formatters={
            "gap_pct": "{:.3f}".format,
            "opening_range_pct": "{:.3f}".format,
            "vix": "{:.2f}".format,
            "vix_change_pct": "{:.2f}".format,
            "current": "{:.2f}".format,
        }))
    if args.csv:
        results.to_csv(args.csv, index=False)
        print(f"Detailed results written to {args.csv}")


if __name__ == "__main__":
    main()
