"""Weekly $50B-crossing universe scanner.

Downloads the free, keyless NASDAQ Trader listed-company files (covers
NYSE/NASDAQ/etc., stocks + ETFs, ~13,000 symbols), checks current market cap
(stocks) or AUM (ETFs -- they don't have a market cap in the traditional
sense) for every symbol, and writes a CSV of everything currently
>= MARKET_CAP_THRESHOLD_USD to universe_scanner.OUTPUT_PATH.

Stocks headquartered outside the US are excluded even if they clear the cap
threshold (see _enrich_with_sector_industry) -- non-US companies listed on a
US exchange (ADRs, e.g. BHP, ASML, TSM, SAP, SONY, NVO, BABA) are foreign
private issuers under SEC rules: they file 20-F/6-K, not 10-K/10-Q, which
this pipeline's SEC-fundamentals workers don't parse. Keeping them in the
universe would mean screening/scoring names this pipeline structurally
can't get good fundamentals data for.

Run weekly (see .github/workflows/universe-scan.yml), not on every
pipeline.yml run -- market caps rarely cross this threshold day-to-day, and a
full scan takes roughly an hour even at a deliberately conservative,
sequential pace (chosen to minimize the risk of Yahoo Finance
throttling/blocking a scan this size, not for speed).

pipeline.py calls schema.sync_universe_from_csv() on every run to reconcile
the `universe` table against whatever this script last produced; this
script's only job is producing that CSV.
"""
import csv
import os
import time

import requests

try:
    import yfinance as yf
except Exception:
    yf = None

from .logger import banner, success, warning, error, progress, info

MARKET_CAP_THRESHOLD_USD = 50_000_000_000
NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
# Relative to CWD, same convention as schema.DB_NAME -- every workflow in
# this repo already runs with the repo root as CWD.
OUTPUT_PATH = "data/universe_50b.csv"
CSV_HEADER = ["ticker", "name", "asset_type", "sector", "industry", "market_cap"]

_HEADERS = {"User-Agent": "Mozilla/5.0"}
MAX_RETRIES = 2
RETRY_DELAY_SEC = 1.5
# Small, deliberate pause between yfinance calls -- this scan is ~13,000
# requests; a conservative pace matters more here than anywhere else in this
# project for not getting throttled/blocked.
REQUEST_DELAY_SEC = 0.05


def _download_symbol_directory():
    """Returns list of (symbol, name, asset_type) from NASDAQ Trader's free,
    keyless listed-company files, deduped, test issues excluded."""
    symbols = {}

    resp = requests.get(NASDAQ_LISTED_URL, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    lines = resp.text.splitlines()
    for line in lines[1:-1]:  # header row + trailing "File Creation Time" footer
        parts = line.split("|")
        if len(parts) < 8:
            continue
        symbol, name, market_category, test_issue, fin_status, lot, etf, nextshares = parts
        if test_issue == "Y" or not symbol:
            continue
        symbols[symbol] = (name, "ETF" if etf == "Y" else "Stock")

    resp = requests.get(OTHER_LISTED_URL, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    lines = resp.text.splitlines()
    for line in lines[1:-1]:
        parts = line.split("|")
        if len(parts) < 8:
            continue
        act_symbol, name, exchange, cqs_symbol, etf, lot, test_issue, nasdaq_symbol = parts
        if test_issue == "Y" or not act_symbol:
            continue
        symbols.setdefault(act_symbol, (name, "ETF" if etf == "Y" else "Stock"))

    return [(sym, name, asset_type) for sym, (name, asset_type) in symbols.items()]


def _yf_symbol(ticker):
    return ticker.replace(".", "-")


def _fetch_market_cap_or_aum(ticker, asset_type):
    """Returns a market-cap-equivalent USD figure (market cap for stocks via
    the cheaper fast_info, AUM via the heavier .info for ETFs -- confirmed
    fast_info doesn't expose AUM), or None if unavailable/the ticker failed
    after retries. Never raises -- a bad/delisted/stale symbol in the NASDAQ
    file is common and must not abort the whole scan."""
    yf_symbol = _yf_symbol(ticker)
    for attempt in range(MAX_RETRIES + 1):
        try:
            handle = yf.Ticker(yf_symbol)
            if asset_type == "ETF":
                value = handle.info.get("totalAssets")
            else:
                value = handle.fast_info.get("marketCap")
            return float(value) if value else None
        except Exception as exc:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SEC)
                continue
            warning(f"{ticker}: market cap/AUM lookup failed after {MAX_RETRIES + 1} attempts: {exc}")
            return None


def _enrich_with_sector_industry(ticker, name, asset_type, market_cap_usd):
    """Phase 2 -- only run on the small survivor list. sector/industry are
    only meaningful for stocks; ETFs get sector='ETF' so the universe table
    can still tell stocks and ETFs apart at a glance.

    Returns None for a non-US-headquartered stock (see module docstring for
    why) -- ETFs are never excluded here, since a US-domiciled fund's
    holdings being international doesn't change that the fund itself files
    normally; the exclusion is specifically about foreign private issuers
    not filing 10-K/10-Q."""
    sector, industry = "", ""
    if asset_type == "Stock":
        try:
            info_dict = yf.Ticker(_yf_symbol(ticker)).info
            country = info_dict.get("country") or ""
            if country and country != "United States":
                info(f"{ticker}: excluded -- headquartered in {country}, files 20-F/6-K not 10-K/10-Q")
                return None
            sector = info_dict.get("sector") or ""
            industry = info_dict.get("industry") or ""
            name = info_dict.get("longName") or info_dict.get("shortName") or name
        except Exception as exc:
            warning(f"{ticker}: sector/industry lookup failed: {exc}")
    else:
        sector = "ETF"
    return {
        "ticker": ticker,
        "name": name,
        "asset_type": asset_type,
        "sector": sector,
        "industry": industry,
        "market_cap": round(market_cap_usd / 1e9, 1),  # billions -- the universe table's convention
    }


def scan_universe(output_path=OUTPUT_PATH, threshold_usd=MARKET_CAP_THRESHOLD_USD):
    if yf is None:
        error("yfinance is unavailable; cannot scan the universe")
        return []

    banner("Universe scan: Phase 1 -- market cap/AUM check across all NYSE/NASDAQ symbols")
    symbols = _download_symbol_directory()
    success(f"Loaded {len(symbols)} symbols from NASDAQ Trader (test issues excluded)")

    survivors = []
    total = len(symbols)
    for index, (ticker, name, asset_type) in enumerate(symbols, start=1):
        value = _fetch_market_cap_or_aum(ticker, asset_type)
        if value is not None and value >= threshold_usd:
            survivors.append((ticker, name, asset_type, value))
        if index % 500 == 0 or index == total:
            progress((index / total) * 70, f"Phase 1: scanned {index}/{total} symbols, {len(survivors)} so far >= threshold")
        time.sleep(REQUEST_DELAY_SEC)

    success(f"Phase 1 complete: {len(survivors)} symbols currently >= ${threshold_usd / 1e9:.0f}B")

    banner("Universe scan: Phase 2 -- sector/industry enrichment for survivors")
    rows = []
    excluded_non_us = 0
    survivor_count = len(survivors)
    for index, (ticker, name, asset_type, market_cap_usd) in enumerate(survivors, start=1):
        row = _enrich_with_sector_industry(ticker, name, asset_type, market_cap_usd)
        if row is None:
            excluded_non_us += 1
        else:
            rows.append(row)
        if index % 25 == 0 or index == survivor_count:
            progress(70 + (index / max(survivor_count, 1)) * 30, f"Phase 2: enriched {index}/{survivor_count}")
        time.sleep(REQUEST_DELAY_SEC)

    if excluded_non_us:
        info(f"Excluded {excluded_non_us} non-US-headquartered stock(s) -- no 10-K/10-Q data available for them")

    rows.sort(key=lambda r: r["market_cap"], reverse=True)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
        writer.writeheader()
        writer.writerows(rows)

    success(f"Wrote {len(rows)} tickers to {output_path}")
    return rows


if __name__ == "__main__":
    scan_universe()
