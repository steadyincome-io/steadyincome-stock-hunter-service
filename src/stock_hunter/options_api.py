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


@app.route("/api/health")
def health():
    _prune_call_times()
    return jsonify({"status": "ok", "calls_in_last_60s": len(_call_times), "limit": ALPACA_RPM_LIMIT})


if __name__ == "__main__":
    port = int(os.getenv("OPTIONS_API_PORT", "8787"))
    info(f"Options API starting on http://localhost:{port}")
    app.run(host="127.0.0.1", port=port, debug=False)
