"""Small local backend for the web UI's per-ticker options-chain view.

Exists solely because Alpaca requires a real API key/secret -- those can't
live in the React app's client-side code (web/app is a static Vite bundle
with no backend of its own, see web/app/src/lib/sqlLoader.js). This process
holds the credentials server-side, proxies option-chain requests to Alpaca,
and enforces the free tier's 200 requests/minute cap so the UI can show a
clear "rate limited" state instead of silently failing.

Run with: PYTHONPATH=src python -m stock_hunter.options_api
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from collections import deque
from datetime import datetime, timedelta, timezone

import requests
from flask import Flask, jsonify, request
from flask_cors import CORS

from .logger import error, info, warning

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

APCA_DATA_BASE = os.getenv("APCA_API_BASE_URL") or "https://data.alpaca.markets"

# Free/Indicative tier limit (confirmed against the account's own docs page,
# same way GEMINI_MAX_RPS's ceiling was confirmed) -- kept a small safety
# margin under the real 200/min so a burst of near-simultaneous ticker
# switches in the UI doesn't tip over the actual Alpaca-side limit.
ALPACA_RPM_LIMIT = 200
ALPACA_RPM_SAFETY_MARGIN = 10

# All expirations from today through this many days out are returned (not
# just the single closest one) -- the UI groups and shows each separately.
DAYS_OUT_MAX = 21

# Strikes are limited to this many dollars either side of the current price
# (e.g. price=100 -> strikes 80 through 120), not a fixed number of strikes --
# confirmed against the user's own example.
STRIKE_BAND_DOLLARS = 20

# OCC option symbol format: root symbol + YYMMDD + C/P + strike*1000 (8 digits).
_OCC_RE = re.compile(r"^(?P<root>[A-Z]+)(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<side>[CP])(?P<strike>\d{8})$")

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "http://localhost:5173"}})

# Rolling window of outbound-Alpaca-call timestamps, shared across requests --
# this process is single-instance/dev-only, so a plain deque + no lock is fine
# (Flask's dev server is single-threaded by default; if that ever changes,
# this would need the same kind of lock ai_narrative.py's throttle uses).
_call_times: deque[float] = deque()


def _prune_call_times():
    cutoff = time.time() - 60
    while _call_times and _call_times[0] < cutoff:
        _call_times.popleft()


def _rate_limited() -> bool:
    _prune_call_times()
    return len(_call_times) >= (ALPACA_RPM_LIMIT - ALPACA_RPM_SAFETY_MARGIN)


def _record_call():
    _call_times.append(time.time())


def _parse_occ_symbol(symbol: str):
    m = _OCC_RE.match(symbol)
    if not m:
        return None
    yy, mm, dd = m.group("yy"), m.group("mm"), m.group("dd")
    return {
        "expiration_date": f"20{yy}-{mm}-{dd}",
        "side": "call" if m.group("side") == "C" else "put",
        "strike": int(m.group("strike")) / 1000.0,
    }




def _row_from_snapshot(symbol: str, parsed: dict, snapshot: dict) -> dict:
    quote = snapshot.get("latestQuote") or {}
    trade = snapshot.get("latestTrade") or {}
    greeks = snapshot.get("greeks") or {}
    return {
        "symbol": symbol,
        "strike": parsed["strike"],
        "bid": quote.get("bp"),
        "ask": quote.get("ap"),
        "last": trade.get("p"),
        "implied_volatility": snapshot.get("impliedVolatility"),
        "delta": greeks.get("delta"),
        "gamma": greeks.get("gamma"),
        "theta": greeks.get("theta"),
        "vega": greeks.get("vega"),
    }


@app.route("/api/options/<ticker>")
def options_chain(ticker: str):
    ticker = ticker.upper().strip()

    price_raw = request.args.get("price")
    if not price_raw:
        return jsonify({"error": "missing_price", "detail": "?price=<current price> is required to size the strike band"}), 400
    try:
        price = float(price_raw)
    except ValueError:
        return jsonify({"error": "invalid_price"}), 400

    if _rate_limited():
        warning(f"{ticker}: Alpaca options request blocked -- within {ALPACA_RPM_SAFETY_MARGIN} of the {ALPACA_RPM_LIMIT}/min cap")
        return jsonify({"error": "rate_limited", "limit_per_min": ALPACA_RPM_LIMIT}), 429

    key = os.getenv("APCA_API_KEY_ID")
    secret = os.getenv("APCA_API_SECRET_KEY")
    if not key or not secret:
        error("Alpaca credentials not configured (APCA_API_KEY_ID/APCA_API_SECRET_KEY)")
        return jsonify({"error": "not_configured"}), 500

    today = datetime.now(timezone.utc).date()
    params = {
        "feed": "indicative",
        "limit": 1000,
        "expiration_date_gte": today.isoformat(),
        "expiration_date_lte": (today + timedelta(days=DAYS_OUT_MAX)).isoformat(),
        "strike_price_gte": round(price - STRIKE_BAND_DOLLARS, 2),
        "strike_price_lte": round(price + STRIKE_BAND_DOLLARS, 2),
    }

    try:
        _record_call()
        resp = requests.get(
            f"{APCA_DATA_BASE}/v1beta1/options/snapshots/{ticker}",
            params=params,
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            timeout=15,
        )
        if resp.status_code == 429:
            warning(f"{ticker}: Alpaca itself returned 429 (rate limited upstream)")
            return jsonify({"error": "rate_limited", "limit_per_min": ALPACA_RPM_LIMIT}), 429
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as exc:
        error(f"{ticker}: Alpaca options request failed: {exc}")
        return jsonify({"error": "upstream_error", "detail": str(exc)}), 502

    snapshots = data.get("snapshots") or {}
    if not snapshots:
        return jsonify({"ticker": ticker, "expirations": []})

    # Group every returned contract by expiration date -- Alpaca already
    # filtered to the requested date/strike window, so this just buckets
    # what came back rather than picking a single "best" expiration.
    by_expiration: dict[str, dict] = {}
    for symbol, snapshot in snapshots.items():
        parsed = _parse_occ_symbol(symbol)
        if not parsed:
            continue
        bucket = by_expiration.setdefault(parsed["expiration_date"], {"calls": [], "puts": []})
        row = _row_from_snapshot(symbol, parsed, snapshot)
        (bucket["calls"] if parsed["side"] == "call" else bucket["puts"]).append(row)

    expirations = []
    for exp_date in sorted(by_expiration):
        bucket = by_expiration[exp_date]
        bucket["calls"].sort(key=lambda r: r["strike"])
        bucket["puts"].sort(key=lambda r: r["strike"])
        expirations.append({
            "expiration_date": exp_date,
            "days_to_expiration": (datetime.strptime(exp_date, "%Y-%m-%d").date() - today).days,
            "calls": bucket["calls"],
            "puts": bucket["puts"],
        })

    return jsonify({"ticker": ticker, "expirations": expirations})


### ---- Wheel-strategy trade tracker ---------------------------------------
# Deliberately a SEPARATE db from drawdown_analyzer.db -- this holds the
# user's own personal trade log (real money entered by hand), not data the
# pipeline can regenerate. Keeping it apart means a pipeline re-run/reset can
# never touch it, and it's simple to back up on its own.
WHEEL_DB_PATH = os.getenv("WHEEL_DB_PATH") or "wheel_trades.db"

_WHEEL_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    strategy TEXT NOT NULL CHECK(strategy IN ('cash_secured_put', 'covered_call')),
    strike REAL NOT NULL,
    qty INTEGER NOT NULL,
    premium_collected REAL NOT NULL,
    delta_at_open REAL,
    open_date DATE NOT NULL,
    expiration_date DATE NOT NULL,
    open_fees REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'expired', 'assigned', 'closed_early')),
    close_date DATE,
    close_cost REAL NOT NULL DEFAULT 0,
    fees REAL NOT NULL DEFAULT 0,
    notes TEXT,
    created_at DATETIME NOT NULL DEFAULT (datetime('now')),
    updated_at DATETIME NOT NULL DEFAULT (datetime('now'))
);
"""


def _init_wheel_db():
    conn = sqlite3.connect(WHEEL_DB_PATH, timeout=30)
    conn.executescript(_WHEEL_SCHEMA)
    # `fees` (pre-existing) is the CLOSE-time commission; open_fees is the
    # commission paid to open the trade -- a real trade can incur both, and
    # collapsing them into one column made it impossible to record a
    # commission paid at entry without it looking like a close cost.
    # ALTER TABLE, not part of _WHEEL_SCHEMA's CREATE TABLE IF NOT EXISTS,
    # so this actually reaches a wheel_trades.db created before this column
    # existed -- same pattern schema.py uses for drawdown_analyzer.db.
    try:
        conn.execute("ALTER TABLE trades ADD COLUMN open_fees REAL NOT NULL DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.close()


def _wheel_conn():
    conn = sqlite3.connect(WHEEL_DB_PATH, timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.row_factory = sqlite3.Row
    return conn


def _trade_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    # Collateral at risk: cash backing a CSP, or the covered stock's value for
    # a CC -- both are strike x 100 shares/contract x qty, same formula either
    # way (a CC's "collateral" is the shares themselves, valued at the strike
    # for this purpose same as the tracker design reference does).
    d["collateral_at_risk"] = d["strike"] * d["qty"] * 100
    # P/L: while open (close_cost/fees still 0), this is just premium minus
    # any entry commission -- same convention as the reference design, which
    # shows open positions' P/L as the premium collected, not a live mark.
    d["pl"] = d["premium_collected"] - d["open_fees"] - d["close_cost"] - d["fees"]
    end = d["close_date"] or datetime.now(timezone.utc).date().isoformat()
    try:
        d["days_held"] = (datetime.strptime(end, "%Y-%m-%d").date() - datetime.strptime(d["open_date"], "%Y-%m-%d").date()).days
    except ValueError:
        d["days_held"] = None
    return d


_TRADE_FIELDS = {"ticker", "strategy", "strike", "qty", "premium_collected", "delta_at_open", "open_date", "expiration_date", "open_fees", "notes"}
_CLOSE_FIELDS = {"status", "close_date", "close_cost", "fees", "notes"}


@app.route("/api/trades", methods=["GET"])
def list_trades():
    conn = _wheel_conn()
    rows = conn.execute("SELECT * FROM trades ORDER BY open_date DESC, id DESC").fetchall()
    conn.close()
    return jsonify([_trade_to_dict(r) for r in rows])


@app.route("/api/trades", methods=["POST"])
def create_trade():
    body = request.get_json(silent=True) or {}
    missing = [f for f in ("ticker", "strategy", "strike", "qty", "premium_collected", "open_date", "expiration_date") if not body.get(f)]
    if missing:
        return jsonify({"error": "missing_fields", "fields": missing}), 400
    if body["strategy"] not in ("cash_secured_put", "covered_call"):
        return jsonify({"error": "invalid_strategy"}), 400

    conn = _wheel_conn()
    cursor = conn.execute(
        """
        INSERT INTO trades (ticker, strategy, strike, qty, premium_collected, delta_at_open, open_date, expiration_date, open_fees, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            body["ticker"].upper().strip(),
            body["strategy"],
            float(body["strike"]),
            int(body["qty"]),
            float(body["premium_collected"]),
            float(body["delta_at_open"]) if body.get("delta_at_open") not in (None, "") else None,
            body["open_date"],
            body["expiration_date"],
            float(body["open_fees"]) if body.get("open_fees") not in (None, "") else 0,
            body.get("notes"),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM trades WHERE id = ?", (cursor.lastrowid,)).fetchone()
    conn.close()
    return jsonify(_trade_to_dict(row)), 201


@app.route("/api/trades/<int:trade_id>", methods=["PATCH"])
def update_trade(trade_id: int):
    body = request.get_json(silent=True) or {}
    updates = {k: v for k, v in body.items() if k in _TRADE_FIELDS | _CLOSE_FIELDS}
    if not updates:
        return jsonify({"error": "no_valid_fields"}), 400
    if "status" in updates and updates["status"] not in ("open", "expired", "assigned", "closed_early"):
        return jsonify({"error": "invalid_status"}), 400

    conn = _wheel_conn()
    existing = conn.execute("SELECT id FROM trades WHERE id = ?", (trade_id,)).fetchone()
    if not existing:
        conn.close()
        return jsonify({"error": "not_found"}), 404

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE trades SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
        (*updates.values(), trade_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    conn.close()
    return jsonify(_trade_to_dict(row))


@app.route("/api/trades/<int:trade_id>", methods=["DELETE"])
def delete_trade(trade_id: int):
    conn = _wheel_conn()
    conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
    conn.commit()
    conn.close()
    return "", 204


def _fetch_underlying_price(ticker: str, key: str, secret: str):
    """Live last-trade price for the underlying stock -- NOT included in the
    options snapshot response (confirmed: Alpaca's option snapshot has no
    underlying price field at all), so this is a second, separate Alpaca
    request. Counts against the same rate limit as the options-chain lookup.

    Deliberately last TRADE price, not (bid+ask)/2 from the quote endpoint --
    confirmed live on RTX: the free-tier quote endpoint returned bid=$198.74/
    ask=$213.45 (a $14.71 spread on a large-cap stock, clearly a thin/stale
    single-venue quote, not a real NBBO), which mid-priced to a wrong $206.10
    against a real last-trade price of $212.35. Unlike options (where bid/ask
    mid is the normal convention since contracts trade less frequently), a
    liquid stock's last trade is the more reliable "current price" signal on
    this tier."""
    _record_call()
    resp = requests.get(
        f"https://data.alpaca.markets/v2/stocks/{ticker}/trades/latest",
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
        timeout=15,
    )
    if resp.status_code != 200:
        return None
    trade = (resp.json() or {}).get("trade") or {}
    return trade.get("p")


@app.route("/api/trades/<int:trade_id>/live-quote", methods=["GET"])
def trade_live_quote(trade_id: int):
    """Live current delta + bid/ask/mid for one OPEN trade's specific contract,
    plus the underlying stock's current price, via Alpaca -- used for the
    open-positions table's "Current Delta", "Current Mid", and "Stock Price"
    columns. Two Alpaca requests per call (options snapshot + stock quote),
    both counted against the same rate limit."""
    conn = _wheel_conn()
    row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "not_found"}), 404
    if row["status"] != "open":
        return jsonify({"error": "not_open"}), 400

    if _rate_limited():
        return jsonify({"error": "rate_limited", "limit_per_min": ALPACA_RPM_LIMIT}), 429

    key = os.getenv("APCA_API_KEY_ID")
    secret = os.getenv("APCA_API_SECRET_KEY")
    if not key or not secret:
        return jsonify({"error": "not_configured"}), 500

    side = "call" if row["strategy"] == "covered_call" else "put"
    params = {
        "feed": "indicative",
        "limit": 10,
        "expiration_date": row["expiration_date"],
        "strike_price_gte": row["strike"],
        "strike_price_lte": row["strike"],
        "type": side,
    }
    try:
        _record_call()
        resp = requests.get(
            f"{APCA_DATA_BASE}/v1beta1/options/snapshots/{row['ticker']}",
            params=params,
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            timeout=15,
        )
        if resp.status_code == 429:
            return jsonify({"error": "rate_limited", "limit_per_min": ALPACA_RPM_LIMIT}), 429
        resp.raise_for_status()
        data = resp.json()
        underlying_price = _fetch_underlying_price(row["ticker"], key, secret)
    except requests.exceptions.RequestException as exc:
        error(f"{row['ticker']}: live-quote fetch failed: {exc}")
        return jsonify({"error": "upstream_error", "detail": str(exc)}), 502

    snapshots = data.get("snapshots") or {}
    for symbol, snapshot in snapshots.items():
        parsed = _parse_occ_symbol(symbol)
        if not parsed:
            continue
        if parsed["side"] == side and parsed["expiration_date"] == row["expiration_date"] and parsed["strike"] == row["strike"]:
            quote = snapshot.get("latestQuote") or {}
            bid = quote.get("bp")
            ask = quote.get("ap")
            mid = round((bid + ask) / 2, 2) if bid is not None and ask is not None else None
            return jsonify({
                "delta": (snapshot.get("greeks") or {}).get("delta"),
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "underlying_price": underlying_price,
            })

    return jsonify({"delta": None, "bid": None, "ask": None, "mid": None, "underlying_price": underlying_price})


_init_wheel_db()


@app.route("/api/health")
def health():
    _prune_call_times()
    return jsonify({"status": "ok", "calls_in_last_60s": len(_call_times), "limit": ALPACA_RPM_LIMIT})


if __name__ == "__main__":
    port = int(os.getenv("OPTIONS_API_PORT", "8787"))
    info(f"Options API starting on http://localhost:{port}")
    app.run(host="127.0.0.1", port=port, debug=False)
