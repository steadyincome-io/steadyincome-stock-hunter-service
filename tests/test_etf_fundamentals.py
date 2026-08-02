import sqlite3
import time
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from stock_hunter.schema import migrate_db
from stock_hunter.sec_etf_worker import sync_etf_reports, fetch_sec_cik_mapping

DB_PATH = "drawdown_analyzer.db"
TEST_ETF_TICKER = "SPY"
YEARS_BACK = 1

def init_db():
    migrate_db(DB_PATH)

def test_etf_fundamentals():
    print("[*] Initializing DB...")
    init_db()
    
    print(f"[*] Syncing ETF fundamentals for {TEST_ETF_TICKER} (last {YEARS_BACK} years)...")
    # Run sync for just this ticker to limit fetch
    # We'll manually fetch and insert to avoid full scan
    cik_map = fetch_sec_cik_mapping()
    if not cik_map:
        print("[!] CIK map unavailable")
        return
    
    clean_ticker = TEST_ETF_TICKER.replace('.', '-')
    cik = cik_map.get(clean_ticker)
    if not cik:
        print(f"[!] No CIK found for {TEST_ETF_TICKER}")
        return
    
    # Fetch filings for this ticker only
    from stock_hunter.sec_etf_worker import fetch_nport_ncen_filings
    filings = fetch_nport_ncen_filings(TEST_ETF_TICKER, cik, years_back=YEARS_BACK)
    print(f"[*] Found {len(filings)} filings for {TEST_ETF_TICKER}")
    
    # Insert or update DB records
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
        metrics, holdings = None, []
        if form_type == 'N-PORT':
            # For N-PORT, we need to fetch the actual filing URL to parse
            resp = requests.get(f['report_url'], headers={"User-Agent": "DrawdownAnalyzer Research research@drawdownanalyzer.com"}, timeout=30)
            if resp.status_code == 200:
                try:
                    import xml.etree.ElementTree as ET
                    root = ET.fromstring(resp.text)
                    # Use existing parse_nport_xml logic
                    ns = {'n': 'http://www.sec.gov/edgar/nport'}
                    metrics = {}
                    # Extract metrics (simplified)
                    total_assets_elem = root.find('.//n:totalAssets', ns)
                    if total_assets_elem is not None:
                        metrics['total_assets'] = float(total_assets_elem.text or 0)
                    net_assets_elem = root.find('.//n:netAssets', ns)
                    if net_assets_elem is not None:
                        metrics['net_assets'] = float(net_assets_elem.text or 0)
                    nav_elem = root.find('.//n:navPerShare', ns)
                    if nav_elem is not None:
                        metrics['nav_per_share'] = float(nav_elem.text or 0)
                    expense_elem = root.find('.//n:expenseRatio', ns)
                    if expense_elem is not None:
                        metrics['expense_ratio'] = float(expense_elem.text or 0)
                    turnover_elem = root.find('.//n:turnoverRate', ns)
                    if turnover_elem is not None:
                        metrics['turnover_rate'] = float(turnover_elem.text or 0)
                    cash_elem = root.find('.//n:cashPercentage', ns)
                    if cash_elem is not None:
                        metrics['cash_percentage'] = float(cash_elem.text or 0)
                    
                    # Extract holdings
                    holdings = []
                    for inv in root.findall('.//n:holding', ns):
                        holding = {}
                        holding['name'] = inv.find('n:name', ns).text if inv.find('n:name', ns) is not None else ''
                        holding['ticker'] = inv.find('n:ticker', ns).text if inv.find('n:ticker', ns) is not None else ''
                        holding['cusip'] = inv.find('n:cusip', ns).text if inv.find('n:cusip', ns) is not None else ''
                        holding['isin'] = inv.find('n:isin', ns).text if inv.find('n:isin', ns) is not None else ''
                        shares_elem = inv.find('n:shares', ns)
                        holding['shares'] = float(shares_elem.text) if shares_elem is not None and shares_elem.text else 0.0
                        mv_elem = inv.find('n:marketValue', ns)
                        holding['market_value'] = float(mv_elem.text) if mv_elem is not None and mv_elem.text else 0.0
                        weight_elem = inv.find('n:weight', ns)
                        holding['weight_pct'] = float(weight_elem.text) if weight_elem is not None and weight_elem.text else 0.0
                        cat_elem = inv.find('n:assetCategory', ns)
                        holding['asset_category'] = cat_elem.text if cat_elem is not None else ''
                        country_elem = inv.find('n:country', ns)
                        holding['country'] = country_elem.text if country_elem is not None else ''
                        curr_elem = inv.find('n:currency', ns)
                        holding['currency'] = curr_elem.text if curr_elem is not None else 'USD'
                        if holding['name']:
                            holdings.append(holding)
                except Exception as e:
                    print(f"[!] Error parsing N-PORT filing {f['accession_number']}: {e}")
        
        # Insert into sec_etf_reports
        try:
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
            conn.commit()
        except Exception as e:
            print(f"[!] DB insert error for {f['accession_number']}: {e}")
            conn.rollback()
            continue
        
        # Insert holdings for N-PORT
        if form_type == 'N-PORT' and holdings:
            for h in holdings:
                try:
                    cursor.execute("""
                        INSERT INTO etf_holdings (
                            ticker, filing_date, holding_name, holding_ticker,
                            cusip, isin, shares, market_value, weight_pct,
                            asset_category, country, currency
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        TEST_ETF_TICKER, end_date, h['name'], h['ticker'],
                        h['cusip'], h['isin'], h['shares'], h['market_value'],
                        h['weight_pct'], h['asset_category'], h['country'], h['currency']
                    ))
                    conn.commit()
                except Exception as e:
                    print(f"[!] Holdings insert error: {e}")
                    conn.rollback()
                    continue
    
    conn.close()
    print("[+] Test completed.")

if __name__ == "__main__":
    test_etf_fundamentals()
