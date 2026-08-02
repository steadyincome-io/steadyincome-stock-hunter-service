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

def test_nport_parsing():
    """Test parsing of an actual NPORT-P filing"""
    # Use the data we saw from the debug output
    cik_str = "0000884394"  # SPY
    accession_number = "0001410368-26-055357"
    
    # Remove hyphens from accession number for URL
    acc_no_hyphens = accession_number.replace('-', '')
    
    # First, let's try to get the filing index to see what documents are available
    index_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik_str)}/{acc_no_hyphens}/index.json"
    print(f"[*] Fetching index from: {index_url}")
    
    try:
        resp = rate_limited_get(index_url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            index_data = resp.json()
            print(f"[*] Index data keys: {list(index_data.keys())}")
            
            # Extract filenames from the directory structure
            filenames = []
            if 'directory' in index_data and 'item' in index_data['directory']:
                items = index_data['directory']['item']
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict) and 'name' in item:
                            filenames.append(item['name'])
                elif isinstance(items, dict) and 'name' in items:
                    # Handle case where there's only one item
                    filenames.append(items['name'])
            
            print(f"[*] Extracted filenames: {filenames}")
            
            # Look for the primary document (often the main XML file)
            primary_doc = None
            for filename in filenames:
                if filename.endswith('.xml') and ('NPORT' in filename or 'primary' in filename.lower()):
                    primary_doc = filename
                    break
            
            # Fallback: look for any XML file
            if not primary_doc:
                for filename in filenames:
                    if filename.endswith('.xml'):
                        primary_doc = filename
                        break
            
            # Last resort: if we have files, take the first one that looks like a document
            if not primary_doc and filenames:
                # Prefer .xml, .txt, or common document types
                for filename in filenames:
                    if any(filename.endswith(ext) for ext in ['.xml', '.txt', '.html']):
                        primary_doc = filename
                        break
                # If still nothing, take the first file
                if not primary_doc:
                    primary_doc = filenames[0]
            
            print(f"[*] Selected primary document: {primary_doc}")
            
            if primary_doc:
                # Construct the URL for the actual filing
                filing_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik_str)}/{acc_no_hyphens}/{primary_doc}"
                print(f"[*] Fetching filing from: {filing_url}")
                
                filing_resp = rate_limited_get(filing_url, headers=HEADERS, timeout=30)
                if filing_resp.status_code == 200:
                    print(f"[*] Successfully fetched filing ({len(filing_resp.text)} characters)")
                    
                    # Try to parse as XML
                    try:
                        # Check if it looks like XML
                        if filing_resp.text.strip().startswith('<?xml') or '<' in filing_resp.text[:100]:
                            root = ET.fromstring(filing_resp.text)
                            print("[*] Successfully parsed as XML")
                            
                            # Define namespace
                            ns = {'n': 'http://www.sec.gov/edgar/nport'}
                            
                            # Try to find some key elements
                            total_assets = root.find('.//n:totalAssets', ns)
                            if total_assets is not None:
                                print(f"[*] Total Assets: {total_assets.text}")
                            else:
                                print("[!] Total Assets element not found")
                                
                            # Try to find net assets
                            net_assets = root.find('.//n:netAssets', ns)
                            if net_assets is not None:
                                print(f"[*] Net Assets: {net_assets.text}")
                            else:
                                print("[!] Net Assets element not found")
                                
                            # Try to find NAV per share
                            nav = root.find('.//n:navPerShare', ns)
                            if nav is not None:
                                print(f"[*] NAV per Share: {nav.text}")
                            else:
                                print("[!] NAV per Share element not found")
                                
                            # Try to find expense ratio
                            expense = root.find('.//n:expenseRatio', ns)
                            if expense is not None:
                                print(f"[*] Expense Ratio: {expense.text}")
                            else:
                                print("[!] Expense Ratio element not found")
                                
                            # Try to find turnover rate
                            turnover = root.find('.//n:turnoverRate', ns)
                            if turnover is not None:
                                print(f"[*] Turnover Rate: {turnover.text}")
                            else:
                                print("[!] Turnover Rate element not found")
                                
                            # Try to find cash percentage
                            cash = root.find('.//n:cashPercentage', ns)
                            if cash is not None:
                                print(f"[*] Cash Percentage: {cash.text}")
                            else:
                                print("[!] Cash Percentage element not found")
                            
                            # Try to find holdings
                            holdings = root.findall('.//n:holding', ns)
                            print(f"[*] Found {len(holdings)} holding elements")
                            if holdings:
                                # Show first few holdings details
                                for i, holding in enumerate(holdings[:3]):  # Show first 3 holdings
                                    name = holding.find('n:name', ns)
                                    ticker = holding.find('n:ticker', ns)
                                    cusip = holding.find('n:cusip', ns)
                                    value = holding.find('n:marketValue', ns)
                                    weight = holding.find('n:weight', ns)
                                    print(f"    Holding {i+1}: Name={name.text if name is not None else 'N/A'}, "
                                          f"Ticker={ticker.text if ticker is not None else 'N/A'}, "
                                          f"CUSIP={cusip.text if cusip is not None else 'N/A'}, "
                                          f"Value={value.text if value is not None else 'N/A'}, "
                                          f"Weight={weight.text if weight is not None else 'N/A'}")
                            else:
                                print("[!] No holding elements found")
                        else:
                            print("[!] Response doesn't appear to be XML")
                            print(f"[*] First 200 chars: {filing_resp.text[:200]}")
                    except ET.ParseError as e:
                        print(f"[!] Failed to parse as XML: {e}")
                        # Maybe it's not XML? Let's check the first few chars
                        print(f"[*] First 200 chars: {filing_resp.text[:200]}")
                else:
                    print(f"[!] Failed to fetch filing: {filing_resp.status_code}")
                    print(f"[!] Response: {filing_resp.text[:200]}")
            else:
                print("[!] Could not determine primary document")
        else:
            print(f"[!] Failed to fetch index: {resp.status_code}")
            print(f"[!] Response: {resp.text[:200]}")
    except Exception as e:
        print(f"[!] Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_nport_parsing()