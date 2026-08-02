import requests
import time

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

def debug_sec_cik_data(cik_str):
    url = f"https://data.sec.gov/submissions/CIK{cik_str}.json"
    print(f"[*] Fetching CIK data for {cik_str} from {url}")
    try:
        resp = rate_limited_get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            print("[*] Successfully fetched data")
            
            # Check recent filings
            recent = data.get('filings', {}).get('recent', {})
            forms = recent.get('form', [])
            filing_dates = recent.get('filingDate', [])
            accession_numbers = recent.get('accessionNumber', [])
            primary_docs = recent.get('primaryDocument', [])
            
            print(f"[*] Total recent filings: {len(forms)}")
            
            # Count form types
            form_counts = {}
            for form in forms:
                form_counts[form] = form_counts.get(form, 0) + 1
            
            print("[*] Form type counts:")
            for form, count in sorted(form_counts.items(), key=lambda x: x[1], reverse=True)[:20]:
                print(f"    {form}: {count}")
                
            # Look for PORT-related forms
            port_forms = [f for f in forms if 'PORT' in f]
            print(f"[*] PORT-related forms found: {len(port_forms)}")
            if port_forms:
                print("[*] Sample PORT forms:", port_forms[:10])
                
            # Show first few filings with details
            print("[*] First 10 filings:")
            for i in range(min(10, len(forms))):
                form = forms[i]
                filing_date = filing_dates[i] if i < len(filing_dates) else "N/A"
                accession = accession_numbers[i] if i < len(accession_numbers) else "N/A"
                print(f"  {i+1}. Form: {form}, Date: {filing_date}, Accession: {accession}")
                
        else:
            print(f"[!] Failed to fetch data: {resp.status_code}")
            print(f"[!] Response: {resp.text[:200]}")
    except Exception as e:
        print(f"[!] Error fetching data: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    # CIK for SPY
    cik = "0000884394"
    debug_sec_cik_data(cik)