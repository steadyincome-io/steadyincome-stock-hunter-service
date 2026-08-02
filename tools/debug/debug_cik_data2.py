import requests
import time
import xml.etree.ElementTree as ET

SEC_USER_AGENT = "DrawdownAnalyzer Research research@drawdownanalyzer.com"
HEADERS = {"User-Agent": SEC_USER_AGENT}

def rate_limited_get(url, headers=HEADERS, timeout=10, min_interval=0.15):
    now = time.time()
    last = getattr(rate_limited_get, '_last_request_time', 0)
    elapsed = now - last
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    rate_limited_get._last_request_time = now
    return requests.get(url, headers=headers, timeout=timeout)

def debug_fetch_cik_data(cik_str):
    url = f"https://data.sec.gov/submissions/CIK{cik_str}.json"
    print(f"[*] Fetching data for CIK {cik_str} from {url}")
    try:
        resp = rate_limited_get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            print("[*] Successfully fetched data")
            recent = data.get('filings', {}).get('recent', {})
            print(f"  - Total filings: {len(data.get('filings', {}).get('filings', []))}")
            print(f"  - Recent filings count: {len(recent.get('forms', []))}")
            print(f"  - Forms present: {list(recent.get('form', []))[:10]}...")
            
            # Extract filing details
            forms = recent.get('forms', [])
            filing_dates = recent.get('filingDate', [])
            report_dates = recent.get('reportDate', [])
            accession_numbers = recent.get('accessionNumber', [])
            primary_docs = recent.get('primaryDocument', [])
            doc_descs = recent.get('primaryDocDescription', [])
            
            print(f"  - Form count: {len(forms)}")
            print(f"  - Filing dates count: {len(filing_dates)}")
            print(f"  - Report dates count: {len(report_dates)}")
            print(f"  - Accession numbers count: {len(accession_numbers)}")
            
            # Print first 5 forms with their indices
            for i in range(min(5, len(forms))):
                form = forms[i]
                filing_date = filing_dates[i] if i < len(filing_dates) else "N/A"
                report_date = report_dates[i] if i < len(report_dates) else "N/A"
                accession = accession_numbers[i] if i < len(accession_numbers) else "N/A"
                doc_desc = doc_descs[i] if i < len(doc_descs) else "N/A"
                print(f"    [{i}] Form: {form}, Filing: {filing_date}, Report: {report_date}, Accession: {accession}, Desc: {doc_desc}")
                
            # Look for N-PORT-P forms
            nport_forms = [(i, f) for i, f in enumerate(forms) if 'PORT' in f and 'P' in f]
            print(f"  - N-PORT-P forms found: {len(nport_forms)}")
            for i, f in nport_forms[:5]:
                print(f"    [{i}] Form: {f}")
        else:
            print(f"[!] Failed to fetch data: {resp.status_code}")
    except Exception as e:
        print(f"[!] Error fetching data: {e}")

if __name__ == "__main__":
    # Get CIK for SPY
    cik_map = {
        'SPY': '0000884394',  # Hardcoded for debugging
    }
    cik = cik_map['SPY']
    debug_fetch_cik_data(cik)