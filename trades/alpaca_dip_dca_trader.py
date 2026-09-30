#!/usr/bin/env python3
"""Paper-trade the rolling-peak dip/DCA strategy from RH_MCP_Trading_Strategy.md.

The SQLite database is the source of truth for *which* stocks qualify. Alpaca is
used only for current quotes, account/position reconciliation, and execution.
Trading is deliberately disabled unless ALPACA_ENABLE_TRADING=true is set.

Example:
    python trades/alpaca_dip_dca_trader.py --once
    python trades/alpaca_dip_dca_trader.py
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sqlite3
import time
from datetime import date
from pathlib import Path
from typing import Any

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - requests is enough for a dry import
    load_dotenv = None


LOG = logging.getLogger("alpaca_dip_dca")
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "drawdown_analyzer.db"
DEFAULT_STATE = Path(__file__).resolve().with_name(".alpaca_dip_dca_state.json")


def env_path(name: str, default: Path) -> Path:
    """Resolve relative path settings from the repository root, not cwd."""
    value = os.getenv(name)
    if not value:
        return default
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def eligible_symbols(db_path: Path) -> list[str]:
    """Return the documented quality/valuation/risk-filtered stock universe."""
    query = """
        SELECT ds.ticker
        FROM daily_snapshot AS ds
        JOIN universe AS u ON u.ticker = ds.ticker
        WHERE u.asset_type = 'Stock'
          AND u.status = 'active'
          AND ds.quality_score >= 60
          AND ds.investment_score >= 55
          AND ds.risk_score <= 55
          AND ds.distress_risk_level = 'Low financial-distress risk'
          AND ds.valuation_tier IN ('Deep Value', 'Fair Value')
          AND (ds.dcf_margin_of_safety_pct BETWEEN -30 AND 150
               OR ds.dcf_margin_of_safety_pct IS NULL)
        ORDER BY ds.investment_score DESC, ds.quality_score DESC, ds.ticker
    """
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite database does not exist: {db_path}")
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        try:
            return [row[0] for row in conn.execute(query).fetchall()]
        except sqlite3.OperationalError as exc:
            raise RuntimeError(
                "The drawdown database is missing one or more strategy columns; "
                "run the pipeline/schema migration first."
            ) from exc


def last_closing_prices(db_path: Path, symbols: list[str]) -> dict[str, float]:
    """Return each symbol's most recent close before today."""
    if not symbols:
        return {}
    placeholders = ",".join("?" for _ in symbols)
    query = f"""
        SELECT ph.ticker, ph.close_price
        FROM price_history AS ph
        JOIN (
            SELECT ticker, MAX(trade_date) AS trade_date
            FROM price_history
            WHERE ticker IN ({placeholders}) AND trade_date < ?
            GROUP BY ticker
        ) AS latest ON latest.ticker = ph.ticker AND latest.trade_date = ph.trade_date
    """
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        return {ticker: float(close) for ticker, close in conn.execute(query, [*symbols, date.today().isoformat()])}


class Alpaca:
    def __init__(self) -> None:
        key = os.getenv("APCA_API_KEY_ID")
        secret = os.getenv("APCA_API_SECRET_KEY")
        if not key or not secret:
            raise RuntimeError("APCA_API_KEY_ID and APCA_API_SECRET_KEY are required")
        self.trade_base = os.getenv("APCA_API_TRADING_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")
        self.data_base = os.getenv("APCA_API_DATA_BASE_URL", "https://data.alpaca.markets").rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        response = self.session.request(method, url, timeout=30, **kwargs)
        if not response.ok:
            raise RuntimeError(f"Alpaca {method} {url} failed ({response.status_code}): {response.text[:500]}")
        return response.json() if response.content else None

    def account(self) -> dict[str, Any]:
        return self.request("GET", f"{self.trade_base}/v2/account")

    def positions(self) -> dict[str, dict[str, Any]]:
        rows = self.request("GET", f"{self.trade_base}/v2/positions")
        return {row["symbol"]: row for row in rows}

    def open_orders(self) -> list[dict[str, Any]]:
        return self.request("GET", f"{self.trade_base}/v2/orders", params={"status": "open", "limit": 500, "nested": "false"})

    def latest_quotes(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}
        payload = self.request("GET", f"{self.data_base}/v2/stocks/quotes/latest", params={"symbols": ",".join(symbols)})
        quotes = payload.get("quotes", payload)
        prices = {}
        for symbol, quote in quotes.items():
            bid, ask = float(quote.get("bp", 0) or 0), float(quote.get("ap", 0) or 0)
            if bid > 0 and ask > 0:
                prices[symbol] = (bid + ask) / 2
            elif ask > 0:
                prices[symbol] = ask
            elif bid > 0:
                prices[symbol] = bid
        return prices

    def order(self, **payload: Any) -> dict[str, Any]:
        return self.request("POST", f"{self.trade_base}/v2/orders", json=payload)

    def cancel(self, order_id: str) -> None:
        self.request("DELETE", f"{self.trade_base}/v2/orders/{order_id}")


class Trader:
    def __init__(self, broker: Alpaca, state_path: Path, dry_run: bool = False) -> None:
        self.broker, self.state_path = broker, state_path
        self.dry_run = dry_run or not env_bool("ALPACA_ENABLE_TRADING")
        self.lot = float(os.getenv("ALPACA_DCA_LOT_NOTIONAL", "50"))
        self.cash_buffer = float(os.getenv("ALPACA_CASH_BUFFER", "500"))
        self.state = self.load_state()

    def load_state(self) -> dict[str, dict[str, Any]]:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Cannot read state file {self.state_path}: {exc}") from exc

    def save_state(self) -> None:
        self.state_path.write_text(json.dumps(self.state, indent=2, sort_keys=True) + "\n")

    def submit(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if self.dry_run:
            LOG.warning("DRY RUN: would submit %s", payload)
            return None
        return self.broker.order(**payload)

    def cancel(self, order_id: str) -> None:
        if self.dry_run:
            LOG.warning("DRY RUN: would cancel order %s", order_id)
        else:
            self.broker.cancel(order_id)

    def run_once(self, symbols: list[str], last_closes: dict[str, float]) -> None:
        positions = self.broker.positions()
        open_orders = self.broker.open_orders()
        tracked_symbols = list(dict.fromkeys([
            *symbols,
            *positions.keys(),
            *(order.get("symbol") for order in open_orders if order.get("symbol")),
        ]))
        held_symbols = {
            symbol for symbol, position in positions.items()
            if float(position.get("qty", 0) or 0) > 0
        }
        open_order_symbols = {order.get("symbol") for order in open_orders if order.get("symbol")}
        for symbol, entry in list(self.state.items()):
            if symbol in held_symbols or symbol in open_order_symbols:
                continue
            # Keep state for eligible flat symbols so they can establish an
            # initial entry from their prior close. Clear state for symbols
            # that left the universe, or whose previously tracked sell order
            # has disappeared after filling.
            if symbol not in symbols or entry.get("open_sell_order_id"):
                del self.state[symbol]
                LOG.info("%s: cleared stale strategy state; no position or open order", symbol)
        quotes = self.broker.latest_quotes(tracked_symbols)
        orders_by_symbol = {symbol: [] for symbol in tracked_symbols}
        for order in open_orders:
            if order.get("symbol") in orders_by_symbol:
                orders_by_symbol[order["symbol"]].append(order)

        account = self.broker.account()
        cash = float(account.get("cash", 0) or 0)
        buying_power = float(account.get("buying_power", 0) or 0)
        available_for_buy = min(cash, buying_power)
        LOG.info(
            "Eligible=%d tracked=%d quotes=%d cash=$%.2f buying_power=$%.2f available_for_buy=$%.2f mode=%s",
            len(symbols), len(tracked_symbols), len(quotes), cash, buying_power, available_for_buy,
            "DRY RUN" if self.dry_run else "PAPER TRADING",
        )

        for symbol in tracked_symbols:
            price = quotes.get(symbol)
            if not price or price <= 0:
                LOG.warning("%s: no usable Alpaca quote", symbol)
                continue
            position = positions.get(symbol)
            orders = orders_by_symbol[symbol]
            entry = self.state.setdefault(symbol, {})
            reference_close = last_closes.get(symbol)
            if reference_close and (
                not entry.get("rolling_peak")
                or entry.get("peak_source") not in {"last_trading_day_close", "intraday"}
            ):
                entry.update({
                    "rolling_peak": reference_close,
                    "steps_bought": 0,
                    "armed": False,
                    "peak_price_since_armed": None,
                    "open_sell_order_id": None,
                    "peak_source": "last_trading_day_close",
                })
                LOG.info("%s: seeded rolling peak from last close of $%.2f", symbol, reference_close)
            if not entry.get("rolling_peak"):
                entry.update({
                    "rolling_peak": price,
                    "steps_bought": 0,
                    "armed": False,
                    "peak_price_since_armed": None,
                    "open_sell_order_id": None,
                    "peak_source": "first_quote_fallback",
                })
            if not position or float(position.get("qty", 0) or 0) <= 0:
                for order in orders:
                    if order.get("side") == "sell":
                        self.cancel(order["id"])

            if price > float(entry["rolling_peak"]):
                entry["rolling_peak"], entry["steps_bought"], entry["peak_source"] = price, 0, "intraday"

            open_sell = next((o for o in orders if o.get("side") == "sell"), None)
            open_buy = next((o for o in orders if o.get("side") == "buy"), None)
            target_steps = math.floor(max(0.0, (float(entry["rolling_peak"]) - price) / float(entry["rolling_peak"])) / 0.03)
            drawdown_pct = (price / float(entry["rolling_peak"]) - 1) * 100
            has_signal = target_steps > int(entry.get("steps_bought", 0)) and not open_buy
            LOG.info(
                "%s: price=$%.2f peak=$%.2f drawdown=%+.2f%% steps=%d/%d %s",
                symbol, price, float(entry["rolling_peak"]), drawdown_pct,
                int(entry.get("steps_bought", 0)), target_steps,
                "SIGNAL" if has_signal else "no signal",
            )
            if target_steps > int(entry.get("steps_bought", 0)) and not open_buy:
                lots = target_steps - int(entry.get("steps_bought", 0))
                lots = min(lots, max(0, int((available_for_buy - self.cash_buffer) // self.lot)))
                if lots:
                    result = self.submit({"symbol": symbol, "notional": round(lots * self.lot, 2), "side": "buy", "type": "market", "time_in_force": "day"})
                    if not self.dry_run and result is not None:
                        entry["steps_bought"] = target_steps
                        available_for_buy -= lots * self.lot
                        if open_sell:
                            self.cancel(open_sell["id"])
                        entry.update({"armed": False, "peak_price_since_armed": None, "open_sell_order_id": None})
                        LOG.info("%s: DCA buy for %d x $%.2f lot(s) at $%.2f", symbol, lots, self.lot, price)
                        continue

            if not position or float(position.get("qty", 0) or 0) <= 0:
                continue

            avg_cost = float(position["avg_entry_price"])

            floor = avg_cost * 1.02
            if open_sell:
                open_sell_price = float(open_sell.get("limit_price", price))
                if price < open_sell_price:
                    self.cancel(open_sell["id"])
                    entry.update({"armed": False, "peak_price_since_armed": None, "open_sell_order_id": None})
                    if price >= floor:
                        result = self.submit({"symbol": symbol, "qty": position["qty"], "side": "sell", "type": "limit", "limit_price": round(price, 2), "time_in_force": "day"})
                        entry["open_sell_order_id"] = result.get("id") if result else None
                        LOG.info("%s: refreshed 2%% profit sell at $%.2f (avg cost $%.2f)", symbol, price, avg_cost)
                continue

            if price >= floor:
                result = self.submit({"symbol": symbol, "qty": position["qty"], "side": "sell", "type": "limit", "limit_price": round(price, 2), "time_in_force": "day"})
                entry["open_sell_order_id"] = result.get("id") if result else None
                LOG.info("%s: 2%% profit sell submitted at $%.2f (avg cost $%.2f)", symbol, price, avg_cost)
        self.save_state()


def main() -> None:
    if load_dotenv:
        # Load the trade-specific file first so it takes precedence over the
        # broader repository .env. Existing shell variables still win because
        # load_dotenv defaults to override=False.
        load_dotenv(Path(__file__).resolve().with_name(".env"))
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=env_path("DRAWDOWN_DB_PATH", DEFAULT_DB))
    parser.add_argument("--state-path", type=Path, default=env_path("ALPACA_STATE_PATH", DEFAULT_STATE))
    parser.add_argument("--interval", type=int, default=int(os.getenv("ALPACA_POLL_INTERVAL_SECONDS", "1800")))
    parser.add_argument("--once", action="store_true", help="Run one check and exit")
    parser.add_argument("--dry-run", action="store_true", help="Never submit or cancel orders")
    args = parser.parse_args()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    broker = Alpaca()
    trader = Trader(broker, args.state_path, args.dry_run)
    while True:
        symbols = eligible_symbols(args.db_path)
        if not symbols:
            LOG.warning("No stocks currently pass the SQLite strategy filters")
        else:
            trader.run_once(symbols, last_closing_prices(args.db_path, symbols))
        LOG.info(
            "Strategy cycle complete: evaluated %d symbol(s); next check in %d seconds",
            len(symbols), max(60, args.interval),
        )
        if args.once:
            return
        time.sleep(max(60, args.interval))


if __name__ == "__main__":
    main()
