"""Fetch daily NAV history from public ETF-issuer feeds.

The feeds are intentionally issuer-specific. Market close is not a substitute
for NAV, so an unsupported issuer is logged and left for a future adapter.
"""
from __future__ import annotations

import io
import sqlite3
from datetime import datetime

import pandas as pd
import requests

from .logger import info, success, warning

HEADERS = {"User-Agent": "DrawdownAnalyzer Research research@drawdownanalyzer.com"}
VANGUARD_URL = "https://investor.vanguard.com/irr/funds/profile/{ticker}-AdditionalFundData"
SPDR_URL = "https://www.ssga.com/us/en/intermediary/etfs/library-content/products/fund-data/etfs/us/navhistory-daily-us-{ticker}.xlsx"


def _number(value):
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _vanguard_rows(ticker, payload):
    history = payload.get("historicalPrice", {})
    rows = []
    for window in history.values():
        if not isinstance(window, dict):
            continue
        # Vanguard's public feed labels the official NAV series as `nav`.
        for item in window.get("nav", []) or []:
            nav = _number(item.get("price"))
            raw_date = item.get("asOfDate")
            if nav and nav > 0 and raw_date:
                try:
                    nav_date = datetime.strptime(raw_date, "%m/%d/%Y").date().isoformat()
                except ValueError:
                    continue
                rows.append((ticker, nav_date, nav, "Vanguard official historicalPrice"))
    return list({(t, d, s): (t, d, n, s) for t, d, n, s in rows}.values())


def _spdr_rows(ticker, content):
    frame = pd.read_excel(io.BytesIO(content), engine="openpyxl")
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    date_col = next((c for c in frame.columns if "date" in c), None)
    nav_col = next((c for c in frame.columns if "nav" in c), None)
    if not date_col or not nav_col:
        raise ValueError(f"SPDR NAV file columns not recognized: {list(frame.columns)}")
    rows = []
    for _, item in frame[[date_col, nav_col]].dropna().iterrows():
        nav = _number(item[nav_col])
        if nav and nav > 0:
            nav_date = pd.to_datetime(item[date_col]).date().isoformat()
            rows.append((ticker, nav_date, nav, "State Street SPDR official NAV history"))
    return rows


def sync_issuer_nav_history(db_path="drawdown_analyzer.db"):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    cur = conn.cursor()
    cur.execute("SELECT ticker, name FROM universe WHERE asset_type = 'ETF' AND status = 'active'")
    universe = cur.fetchall()
    fetched = inserted = 0
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    for ticker, name in universe:
        source = None
        rows = []
        try:
            if "vanguard" in (name or "").lower():
                response = requests.get(VANGUARD_URL.format(ticker=ticker), headers=HEADERS, timeout=30)
                response.raise_for_status()
                rows = _vanguard_rows(ticker, response.json())
                source = "Vanguard"
            elif "spdr" in (name or "").lower():
                response = requests.get(SPDR_URL.format(ticker=ticker), headers=HEADERS, timeout=30)
                response.raise_for_status()
                rows = _spdr_rows(ticker, response.content)
                source = "State Street"
            else:
                continue
            fetched += 1
            for row in rows:
                cur.execute("""
                    INSERT OR REPLACE INTO etf_nav_history
                    (ticker, nav_date, nav_per_share, source, fetched_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (*row, now))
                inserted += cur.rowcount
            conn.commit()
            info(f"{ticker}: issuer NAV sync via {source}, {len(rows)} observation(s)")
        except Exception as exc:
            warning(f"{ticker}: issuer NAV sync unavailable: {exc}")

    conn.close()
    success(f"Issuer NAV sync complete: {fetched} ETF source(s), {inserted} row(s) written")
    return inserted
