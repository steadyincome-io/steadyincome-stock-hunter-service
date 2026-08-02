import requests
import sqlite3
import time
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from .logger import step, info, success, warning, error, ticker_start, ticker_done

SEC_USER_AGENT = "DrawdownAnalyzer Research research@drawdownanalyzer.com"
HEADERS = {"User-Agent": SEC_USER_AGENT}

_last_request_time = 0.0

def rate_limited_get(url, headers=HEADERS, timeout=10, min_interval=0.15):
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_request_time = time.time()
    return requests.get(url, headers=headers, timeout=timeout)


def fetch_sec_cik_mapping():
    """Fetch official SEC EDGAR company tickers to CIK map."""
    url = "https://www.sec.gov/files/company_tickers.json"
    try:
        resp = rate_limited_get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            cik_map = {}
            for item in data.values():
                cik_map[item['ticker'].upper()] = str(item['cik_str']).zfill(10)
            return cik_map
    except Exception as e:
        error(f"SEC EDGAR map fetch failed: {e}")
    return {}

def parse_form4_xml(xml_text):
    """Parse Form 4 XML and return list of transactions."""
    xml_clean = re.sub(r'\sxmlns="[^"]+"', '', xml_text, count=1)
    try:
        root = ET.fromstring(xml_clean.encode('utf-8'))
    except Exception as e:
        error(f"Form 4 XML parse error: {e}")
        return []

    def find_txt(elem, xpath, default=''):
        found = elem.find(xpath)
        return found.text.strip() if found is not None and found.text else default

    owner_name = find_txt(root, './/reportingOwnerId/rptOwnerName', 'Unknown')
    is_director = find_txt(root, './/reportingOwnerRelationship/isDirector', '0') in ['1', 'true', 'True']
    is_officer = find_txt(root, './/reportingOwnerRelationship/isOfficer', '0') in ['1', 'true', 'True']
    officer_title = find_txt(root, './/reportingOwnerRelationship/officerTitle', '')

    title = officer_title if is_officer and officer_title else ('Director' if is_director else 'Insider')

    trades = []
    non_deriv_txs = root.findall('.//nonDerivativeTransaction')
    for tx in non_deriv_txs:
        trade_date = find_txt(tx, './/transactionDate/value')
        code = find_txt(tx, './/transactionCoding/transactionCode')
        shares = find_txt(tx, './/transactionAmounts/transactionShares/value')
        acq_disp = find_txt(tx, './/transactionAmounts/transactionAcquiredDisposedCode/value')
        price = find_txt(tx, './/transactionAmounts/transactionPricePerShare/value', '0.0')

        if not trade_date or not shares:
            continue

        try:
            shares_val = int(float(shares))
            price_val = float(price)
        except ValueError:
            continue

        total_value = shares_val * price_val
        tx_type = 'Purchase' if acq_disp == 'A' else 'Sale'
        sentiment = 'Bullish' if acq_disp == 'A' else 'Bearish'

        trades.append({
            'trade_date': trade_date,
            'insider_name': owner_name,
            'title': title,
            'shares': shares_val,
            'code': code,
            'transaction_type': tx_type,
            'price_per_share': price_val,
            'total_value': total_value,
            'sentiment': sentiment
        })
    return trades

def fetch_insider_filings(ticker, cik_str):
    """Fetch recent Form 4 insider filings for a given CIK from SEC EDGAR API and parse their XML."""
    url = f"https://data.sec.gov/submissions/CIK{cik_str}.json"
    insider_trades = []
    try:
        resp = rate_limited_get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return []
        
        data = resp.json()
        recent = data.get('filings', {}).get('recent', {})
        forms = recent.get('form', [])
        filing_dates = recent.get('filingDate', [])
        accession_numbers = recent.get('accessionNumber', [])
        p_docs = recent.get('primaryDocument', [])
        
        recent_form4_count = 0
        for i in range(len(forms)):
            if forms[i] == '4':
                f_date = filing_dates[i] if i < len(filing_dates) else datetime.now().strftime('%Y-%m-%d')
                acc_num = accession_numbers[i] if i < len(accession_numbers) else ''
                p_doc = p_docs[i] if i < len(p_docs) else ''
                
                if acc_num and p_doc:
                    acc_no_hyphens = acc_num.replace('-', '')
                    clean_p_doc = p_doc.split('/')[-1]
                    xml_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik_str)}/{acc_no_hyphens}/{clean_p_doc}"
                    
                    try:
                        xml_resp = rate_limited_get(xml_url, headers=HEADERS, timeout=10)
                        if xml_resp.status_code == 200:
                            parsed_trades = parse_form4_xml(xml_resp.text)
                            for t in parsed_trades:
                                t['ticker'] = ticker
                                t['filing_date'] = f_date
                                # Only record transactions with non-zero price to filter out non-market gifts/awards
                                if t['price_per_share'] > 0.0:
                                    insider_trades.append(t)
                            
                            recent_form4_count += 1
                            if recent_form4_count >= 20: # Limit to 20 Form 4 documents per stock to be friendly to SEC
                                break
                    except Exception as e:
                        error(f"{ticker}: Form 4 XML parse failed: {e}")
    except Exception as e:
        error(f"{ticker}: SEC filing fetch failed: {e}")
    return insider_trades

def sync_sec_insider_data(db_path="drawdown_analyzer.db"):
    step("SEC insider worker: start")
    cik_map = fetch_sec_cik_mapping()
    if not cik_map:
        warning("SEC CIK map unavailable")
        return 0

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT ticker FROM universe WHERE asset_type = 'Stock' AND status = 'active'")
    stocks = [row[0] for row in cursor.fetchall()]
    
    total_filings = 0
    for ticker in stocks:
        clean_ticker = ticker.replace('.', '-')
        cik = cik_map.get(clean_ticker)
        if cik:
            ticker_start(ticker, "fetching Form 4 filings")
            trades = fetch_insider_filings(ticker, cik)
            for t in trades:
                try:
                    cursor.execute("""
                        INSERT OR IGNORE INTO insider_trades 
                        (ticker, filing_date, trade_date, insider_name, title, shares, code, transaction_type, price_per_share, total_value, sentiment)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        t['ticker'], t['filing_date'], t['trade_date'], t['insider_name'],
                        t['title'], t['shares'], t['code'], t['transaction_type'],
                        t['price_per_share'], t['total_value'], t['sentiment']
                    ))
                    total_filings += 1
                except Exception:
                    pass
            conn.commit()
            ticker_done(ticker, f"stored {len(trades)} insider trades")
            
    conn.commit()
    conn.close()
    success(f"SEC insider sync complete: {total_filings} Form 4 entries stored")
    return total_filings

if __name__ == "__main__":
    sync_sec_insider_data()
