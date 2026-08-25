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

import sqlite3
import time

try:
    import yfinance as yf
except Exception:
    yf = None

from .logger import step, info, success, warning, error, ticker_start, ticker_done, progress

# yfinance has no documented/official rate limit (unlike Alpaca's 200/min) --
# it's an unofficial scrape of Yahoo's endpoints. The existing pipeline already
# calls yf.Ticker(...).history() for every ticker with no throttle and it works,
# but Yahoo is known to rate-limit/block high-volume or cloud-IP traffic (same
# class of risk as the SEC-blocking incident this project already hit once).
# This is a new call path, so it gets a light proactive throttle rather than
# waiting for a real failure to force the issue.
_MIN_INTERVAL_SEC = 0.2
_last_call_time = 0.0


def _throttled_dividends(yf_ticker):
    global _last_call_time
    elapsed = time.time() - _last_call_time
    if elapsed < _MIN_INTERVAL_SEC:
        time.sleep(_MIN_INTERVAL_SEC - elapsed)
    _last_call_time = time.time()
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
