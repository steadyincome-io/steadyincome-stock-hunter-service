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

def debug_fetch_cik_data(cik_str):
    url = f"https://data.sec.gov/submissions/CIK{cik_str}.json"
    print(f"[*] Fetching data for CIK {cik_str} from {url}")
    try:
        resp = rate_limited_get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            print("[*] Successfully fetched data")
            print("[*] Recent filings structure:")
            recent = data.get('filings', {}).get('recent', {})
            print(f"  - filings count: {len(recent.get('forms', []))}")
            print(f"  - recent forms: {list(recent.get('form', []))}")
            print(f"  - recent filing dates: {list(recent.get('filingDate', []))[:5]}")
            print(f"  - recent report dates: {list(recent.get('reportDate', []))[:5]}")
            print(f"  - recent accession numbers: {list(recent.get('accessionNumber', []))[:5]}")
            
            # Show first few forms with details
            forms = recent.get('forms', [])
            for i, form in enumerate(forms[:5]):
                print(f"  - Form {i}: {form}")
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