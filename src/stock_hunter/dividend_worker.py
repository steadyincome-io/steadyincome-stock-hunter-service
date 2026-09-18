"""Dividend/distribution history sync, sourced from yfinance.

SEC filings were considered and rejected as the source: N-PORT (ETFs) discloses
no distribution data at all, and the filing FORM TYPE that does (N-30D, N-30B-2,
N-CSR/N-CSRS -- confirmed to differ across SPY/QQQ/Vanguard) plus the XBRL tag
used by stocks (CommonStockDividendsPerShareCashPaid vs ...Declared, inconsistent
across companies, with overlapping cumulative/quarterly periods in the same
series) would both need real per-filer special-casing to parse reliably.
yfinance's Ticker.dividends gives one consistent (ex_date, amount_per_share)
series for both stocks and ETFs.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime

try:
    import yfinance as yf
except Exception:
    yf = None

from .logger import step, info, success, warning, error, ticker_start, ticker_done, progress
from .yfinance_throttle import throttle_yfinance


def _throttled_dividends(yf_ticker):
    throttle_yfinance()
    return yf.Ticker(yf_ticker).dividends


def sync_dividend_history(db_path="drawdown_analyzer.db", tickers=None):
    """Fetch each ticker's full dividend/distribution history from yfinance and
    upsert into dividend_history. Safe to re-run: UNIQUE(ticker, ex_date) plus
    INSERT OR IGNORE means already-stored payments are skipped, so this is cheap
    on repeat runs (only genuinely new ex-dates get inserted)."""
    step("Dividend history worker: start")

    if yf is None:
        warning("yfinance is unavailable; skipping dividend history sync")
        return 0

    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    cursor = conn.cursor()

    if tickers is None:
        cursor.execute("SELECT ticker FROM universe WHERE status = 'active'")
        tickers = [row[0] for row in cursor.fetchall()]

    total = len(tickers)
    inserted = 0
    tickers_with_new_data = 0

    for index, ticker in enumerate(tickers, start=1):
        yf_ticker = ticker.replace('.', '-')
        ticker_start(ticker, "fetching dividend history")
        try:
            dividends = _throttled_dividends(yf_ticker)
        except Exception as exc:
            error(f"{ticker}: dividend history fetch failed: {exc}")
            progress((index / max(total, 1)) * 100, f"Dividend history | Ticker {index}/{total}: {ticker} failed")
            continue

        if dividends is None or dividends.empty:
            ticker_done(ticker, "no dividend history")
            progress((index / max(total, 1)) * 100, f"Dividend history | Ticker {index}/{total}: {ticker} complete")
            continue

        ticker_new = 0
        for ex_date, amount in dividends.items():
            try:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO dividend_history (ticker, ex_date, amount_per_share)
                    VALUES (?, ?, ?)
                    """,
                    (ticker, ex_date.strftime("%Y-%m-%d"), float(amount)),
                )
                if cursor.rowcount > 0:
                    ticker_new += 1
                    inserted += 1
            except Exception as exc:
                error(f"{ticker}: error inserting dividend on {ex_date}: {exc}")

        conn.commit()
        if ticker_new:
            tickers_with_new_data += 1
        ticker_done(ticker, f"{len(dividends)} total payments on record, {ticker_new} new")
        progress((index / max(total, 1)) * 100, f"Dividend history | Ticker {index}/{total}: {ticker} complete")

    conn.close()
    success(f"Dividend history sync complete: {inserted} new payment(s) across {tickers_with_new_data} ticker(s)")
    return inserted


_TICKER_FIELDS = ("ticker", "symbol", "stock", "security")
_DATE_FIELDS = (
    "ex_date", "exDate", "ex_dividend_date", "exDividendDate",
    "dividend_date", "dividendDate", "date", "payment_date", "paymentDate",
)
_AMOUNT_FIELDS = (
    "amount_per_share", "amountPerShare", "dividend_per_share", "dividendPerShare",
    "cash_amount", "cashAmount", "amount", "dividend", "value",
)
_NESTED_DIVIDEND_FIELDS = ("dividends", "dividend_history", "history", "payments", "data")


def _first_value(document, fields):
    for field in fields:
        value = document.get(field)
        if value is not None and value != "":
            return value
    return None


def _normalise_mongo_date(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict) and "$date" in value:
        return _normalise_mongo_date(value["$date"])
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            try:
                return date.fromisoformat(value[:10]).isoformat()
            except ValueError:
                return None
    return None


def _extract_mongo_dividends(document, inherited_ticker=None):
    """Extract rows from either one-payment or ticker-with-nested-payments docs."""
    if not isinstance(document, dict):
        return []
    ticker = _first_value(document, _TICKER_FIELDS) or inherited_ticker
    rows = []
    for field in _NESTED_DIVIDEND_FIELDS:
        nested = document.get(field)
        if isinstance(nested, list):
            for item in nested:
                rows.extend(_extract_mongo_dividends(item, ticker))
    ex_date = _normalise_mongo_date(_first_value(document, _DATE_FIELDS))
    amount = _first_value(document, _AMOUNT_FIELDS)
    if ticker and ex_date and amount is not None:
        try:
            rows.append((str(ticker).strip().upper(), ex_date, float(amount)))
        except (TypeError, ValueError):
            pass
    return rows


def sync_dividend_history_from_mongo(
    db_path="drawdown_analyzer.db", tickers=None, mongo_url=None,
    mongo_db_name=None, collection_name=None,
):
    """Import configured MongoDB dividend data into ``dividend_history``.

    Set MONGO_CLIENT_URL, MONGO_DB_NAME, and optionally
    MONGO_DIVIDEND_COLLECTION (default ``ticker.infos``). Missing configuration or
    an unavailable pymongo dependency safely skips this optional source.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(".env")
    except Exception:
        pass
    mongo_url = mongo_url or os.getenv("MONGO_CLIENT_URL")
    mongo_db_name = mongo_db_name or os.getenv("MONGO_DB_NAME")
    collection_name = collection_name or os.getenv("MONGO_DIVIDEND_COLLECTION") or "ticker.infos"
    if not mongo_url or not mongo_db_name:
        info("MongoDB dividend sync skipped: MONGO_CLIENT_URL/MONGO_DB_NAME not configured")
        return 0
    try:
        from pymongo import MongoClient
    except Exception:
        warning("MongoDB dividend sync skipped: pymongo is not installed")
        return 0

    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    cursor = conn.cursor()
    if tickers is None:
        cursor.execute("SELECT ticker FROM universe WHERE status = 'active'")
        tickers = [row[0] for row in cursor.fetchall()]
    ticker_map = {str(t).strip().upper(): t for t in tickers}
    client = None
    try:
        client = MongoClient(mongo_url, serverSelectionTimeoutMS=10000)
        client.admin.command("ping")
        collection = client[mongo_db_name][collection_name]
        ticker_values = set(ticker_map)
        ticker_values.update(value.lower() for value in ticker_map)
        query = {"$or": [{field: {"$in": list(ticker_values)}} for field in _TICKER_FIELDS]}
        imported = 0
        for document in collection.find(query):
            for ticker, ex_date, amount in _extract_mongo_dividends(document):
                canonical_ticker = ticker_map.get(ticker)
                if canonical_ticker is None:
                    continue
                cursor.execute(
                    "INSERT OR REPLACE INTO dividend_history (ticker, ex_date, amount_per_share) VALUES (?, ?, ?)",
                    (canonical_ticker, ex_date, amount),
                )
                imported += 1
        conn.commit()
        success(f"MongoDB dividend sync complete: {imported} payment(s) imported from {collection_name}")
        return imported
    except Exception as exc:
        warning(f"MongoDB dividend sync failed; continuing pipeline: {exc}")
        return 0
    finally:
        conn.close()
        if client is not None:
            client.close()
