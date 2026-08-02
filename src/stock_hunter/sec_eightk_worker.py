"""8-K material-event ingestion (architecture doc section 4.4).

Focuses on item codes relevant to debt, solvency, and other material events
so the distress/risk-scoring layer has a timely signal between 10-Q filings.
The SEC submissions feed already reports each 8-K's item codes, so no filing
document needs to be downloaded or parsed.
"""
import sqlite3
from datetime import datetime, timedelta

from .logger import step, info, success, warning, error, ticker_start, ticker_done
from .sec_edgar_worker import rate_limited_get, HEADERS

DEBT_RELATED_ITEMS = {"1.01", "1.02", "2.03", "2.04", "2.05", "2.06", "3.01", "4.01"}
BANKRUPTCY_ITEMS = {"1.03"}


def _days_back_cutoff(days_back: int) -> str:
    return (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")


def fetch_8k_filings(ticker, cik_str, days_back=180):
    """Fetch recent 8-K filings for a CIK, keeping only those with a
    debt/bankruptcy-relevant item code."""
    url = f"https://data.sec.gov/submissions/CIK{cik_str}.json"
    cutoff_date = _days_back_cutoff(days_back)
    events = []
    try:
        resp = rate_limited_get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return []

        data = resp.json()
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        accession_numbers = recent.get("accessionNumber", [])
        items_list = recent.get("items", [])
        primary_docs = recent.get("primaryDocument", [])

        for i, form in enumerate(forms):
            if form != "8-K":
                continue
            f_date = filing_dates[i] if i < len(filing_dates) else ""
            if f_date and f_date < cutoff_date:
                continue

            items_raw = items_list[i] if i < len(items_list) else ""
            item_codes = [c.strip() for c in items_raw.split(",") if c.strip()]
            is_debt_related = any(code in DEBT_RELATED_ITEMS for code in item_codes)
            is_bankruptcy_related = any(code in BANKRUPTCY_ITEMS for code in item_codes)

            # Only store events with at least one item code we actively track,
            # to keep the table focused on debt/solvency signal per the doc.
            if not (is_debt_related or is_bankruptcy_related):
                continue

            acc_num = accession_numbers[i] if i < len(accession_numbers) else ""
            acc_no_hyphens = acc_num.replace("-", "")
            p_doc = primary_docs[i] if i < len(primary_docs) else ""
            filing_url = ""
            if acc_no_hyphens and p_doc:
                filing_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik_str)}/{acc_no_hyphens}/{p_doc}"

            events.append({
                "ticker": ticker,
                "cik": cik_str,
                "accession_number": acc_num,
                "filing_date": f_date,
                "item_codes": ",".join(item_codes),
                "is_debt_related": int(is_debt_related),
                "is_bankruptcy_related": int(is_bankruptcy_related),
                "description": f"8-K items {', '.join(item_codes)}",
                "filing_url": filing_url,
            })
    except Exception as e:
        error(f"{ticker}: error fetching 8-K filings: {e}")

    return events


def sync_8k_events(db_path="drawdown_analyzer.db", days_back=180):
    step("SEC 8-K worker: start (debt/bankruptcy-relevant items)")
    from .sec_edgar_worker import fetch_sec_cik_mapping

    cik_map = fetch_sec_cik_mapping()
    if not cik_map:
        warning("SEC CIK map unavailable")
        return 0

    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    cursor = conn.cursor()
    cursor.execute("SELECT ticker FROM universe WHERE asset_type = 'Stock' AND status = 'active'")
    stocks = [row[0] for row in cursor.fetchall()]

    total_events = 0
    for ticker in stocks:
        clean_ticker = ticker.replace('.', '-')
        cik = cik_map.get(clean_ticker)
        if not cik:
            continue

        ticker_start(ticker, "fetching 8-K debt/material events")
        events = fetch_8k_filings(ticker, cik, days_back=days_back)
        for e in events:
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO eight_k_events (
                        ticker, cik, accession_number, filing_date, item_codes,
                        is_debt_related, is_bankruptcy_related, description, filing_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    e["ticker"], e["cik"], e["accession_number"], e["filing_date"],
                    e["item_codes"], e["is_debt_related"], e["is_bankruptcy_related"],
                    e["description"], e["filing_url"],
                ))
                if cursor.rowcount > 0:
                    total_events += 1
            except Exception as ex:
                error(f"{ticker}: error inserting 8-K event: {ex}")
        conn.commit()
        ticker_done(ticker, f"stored {len(events)} debt/bankruptcy-relevant 8-K events")

    conn.close()
    success(f"SEC 8-K sync complete: {total_events} new events stored")
    return total_events


if __name__ == "__main__":
    sync_8k_events()
