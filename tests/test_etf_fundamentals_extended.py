import sqlite3
import requests
import time
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from stock_hunter.schema import migrate_db
from stock_hunter.sec_etf_worker import fetch_sec_cik_mapping, fetch_nport_ncen_filings, parse_nport_xml

DB_PATH = "drawdown_analyzer.db"
TEST_ETF_TICKER = "SPY"
YEARS_BACK = 10  # Increased to capture older filings

def init_db():
    migrate_db(DB_PATH)

def get_cik_for_ticker(ticker):
    cik_map = fetch_sec_cik_mapping()
    clean_ticker = ticker.replace('.', '-')
    return cik_map.get(clean_ticker)

def test_etf_fundamentals_extended():
    print("[*] Initializing DB...")
    init_db()
    
    print(f"[*] Getting CIK for {TEST_ETF_TICKER}...")
    cik = get_cik_for_ticker(TEST_ETF_TICKER)
    if not cik:
        print(f"[!] No CIK found for {TEST_ETF_TICKER}")
        return
    print(f"[*] CIK for {TEST_ETF_TICKER}: {cik}")
    
    print(f"[*] Fetching N-PORT/N-CEN filings for {TEST_ETF_TICKER} (last {YEARS_BACK} years)...")
    filings = fetch_nport_ncen_filings(TEST_ETF_TICKER, cik, years_back=YEARS_BACK)
    print(f"[*] Found {len(filings)} filings")
    
    # Print details of each filing to see types
    for f in filings:
        print(f"  - Form: {f['form_type']}, Filing Date: {f['filing_date']}, Accession: {f['accession_number']}")
    
    # Process each filing
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    cursor = conn.cursor()
    
    existing_accessions = set()
    cursor.execute("SELECT accession_number FROM sec_etf_reports WHERE ticker = ?", (TEST_ETF_TICKER,))
    for row in cursor.fetchall():
        existing_accessions.add(row[0])
    
    new_filings = [f for f in filings if f['accession_number'] not in existing_accessions]
    print(f"[*] {len(new_filings)} new filings to process")
    
    for f in new_filings:
        form_type = f['form_type']
        end_date = f['period_end_date']
        accn = f['accession_number']
        print(f"[*] Processing {form_type} filing {accn} for {TEST_ETF_TICKER} (period end: {end_date})")
        
        if form_type == 'N-PORT':
            # Fetch the actual filing URL
            report_url = f['report_url']
            if not report_url:
                print(f"    [!] No report URL for {accn}")
                continue
                
            try:
                resp = requests.get(report_url, headers={"User-Agent": "DrawdownAnalyzer Research research@drawdownanalyzer.com"}, timeout=30)
                if resp.status_code != 200:
                    print(f"    [!] Failed to fetch filing {accn}: {resp.status_code}")
                    continue
                    
                # Parse the XML
                metrics, holdings = parse_nport_xml(resp.text)
                print(f"    [+] Parsed metrics: {metrics}")
                print(f"    [+] Parsed holdings: {len(holdings)} holdings")
                
                # Insert into sec_etf_reports
                cursor.execute("""
                    INSERT OR IGNORE INTO sec_etf_reports (
                        ticker, cik, form_type, filing_date, period_end_date,
                        accession_number, primary_doc_description,
                        total_assets, net_assets, nav_per_share,
                        expense_ratio, turnover_rate, cash_percentage, report_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    f['ticker'], f['cik'], form_type, f['filing_date'], end_date,
                    accn, f['primary_doc_description'],
                    metrics.get('total_assets'), metrics.get('net_assets'),
                    metrics.get('nav_per_share'), metrics.get('expense_ratio'),
                    metrics.get('turnover_rate'), metrics.get('cash_percentage'),
                    f['report_url']
                ))
                print(f"    [+] Inserted report record for {accn}")
                
                # Insert holdings
                for h in holdings:
                    cursor.execute("""
                        INSERT INTO etf_holdings (
                            ticker, filing_date, holding_name, holding_ticker,
                            cusip, isin, shares, market_value, weight_pct,
                            asset_category, country, currency
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        TEST_ETF_TICKER, end_date, h['name'], h['ticker'],
                        h['cusip'], h['isin'], h['shares'], h['market_value'],
                        h['weight_pct'], h['asset_category'], h['country'], h['currency']
                    ))
                conn.commit()
                print(f"    [+] Inserted {len(holdings)} holdings for {accn}")
                
            except Exception as e:
                print(f"    [!] Error processing filing {accn}: {e}")
                conn.rollback()
                continue
        else:
            print(f"    [*] Skipping {form_type} filing (no holdings expected)")
    
    conn.close()
    print("[+] Extended test completed.")

if __name__ == "__main__":
    test_etf_fundamentals_extended()
