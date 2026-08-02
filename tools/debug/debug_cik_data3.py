import requests
import time
import json

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
            print(f"  - Total filings: {len(data.get('filings', []))}")
            print(f"  - Filings structure keys: {list(data.get('filings', {}).keys())}")
            
            # Print recent filings info
            recent = data.get('filings', {}).get('recent', {})
            print(f"  - Recent filings count: {len(recent.get('forms', []))}")
            print(f"  - Recent forms: {list(recent.get('form', []))[:10]}")
            print(f"  - Recent filing dates count: {len(recent.get('filingDate', []))}")
            print(f"  - Recent report dates count: {len(recent.get('reportDate', []))}")
            
            # Print first few forms from recent
            forms = recent.get('forms', [])
            for i, form in enumerate(forms[:5]):
                print(f"    [{i}] Form: {form}")
                
            # Look for N-PORT-P forms in recent
            nport_forms = [(i, f) for i, f in enumerate(forms) if 'PORT' in f and 'P' in f]
            print(f"  - N-PORT-P forms in recent: {len(nport_forms)}")
            for i, f in nport_forms[:5]:
                print(f"    [{i}] Form: {f}")
                
            # Print all forms (first 10)
            all_forms = data.get('filings', {}).get('filings', [])
            print(f"  - All filings count: {len(all_forms)}")
            for i in range(min(10, len(all_forms))):
                form = all_forms[i]
                print(f"    [{i}] Form: {form}")
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