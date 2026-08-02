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

def debug_nport_xml():
    """Debug the structure of an NPORT-P XML filing"""
    # Use the data we saw from the debug output
    cik_str = "0000884394"  # SPY
    accession_number = "0001410368-26-055357"
    
    # Remove hyphens from accession number for URL
    acc_no_hyphens = accession_number.replace('-', '')
    
    # Get the filing
    index_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik_str)}/{acc_no_hyphens}/index.json"
    print(f"[*] Fetching index from: {index_url}")
    
    try:
        resp = requests.get(index_url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            index_data = resp.json()
            
            # Extract filenames
            filenames = []
            if 'directory' in index_data and 'item' in index_data['directory']:
                items = index_data['directory']['item']
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict) and 'name' in item:
                            filenames.append(item['name'])
                elif isinstance(items, dict) and 'name' in items:
                    filenames.append(items['name'])
            
            # Find primary document
            primary_doc = None
            for filename in filenames:
                if filename.endswith('.xml') and ('NPORT' in filename or 'primary' in filename.lower()):
                    primary_doc = filename
                    break
            
            if not primary_doc:
                for filename in filenames:
                    if filename.endswith('.xml'):
                        primary_doc = filename
                        break
            
            if not primary_doc and filenames:
                # Prefer .xml, .txt, or common document types
                for filename in filenames:
                    if any(filename.endswith(ext) for ext in ['.xml', '.txt', '.html']):
                        primary_doc = filename
                        break
                # If still nothing, take the first file
                if not primary_doc:
                    primary_doc = filenames[0]
            
            if primary_doc:
                # Get the filing
                filing_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik_str)}/{acc_no_hyphens}/{primary_doc}"
                print(f"[*] Fetching filing from: {filing_url}")
                
                filing_resp = requests.get(filing_url, headers=HEADERS, timeout=30)
                if filing_resp.status_code == 200:
                    print(f"[*] Successfully fetched filing ({len(filing_resp.text)} characters)")
                    
                    # Parse and examine structure
                    try:
                        root = ET.fromstring(filing_resp.text)
                        print(f"[*] Root tag: {root.tag}")
                        print(f"[*] Root attributes: {list(root.attrib.keys())[:5]}...")  # Show first 5 attrs
                        
                        # Determine namespace
                        ns = {}
                        if root.tag.startswith('{'):
                            # Extract namespace from tag like {namespace}tag
                            namespace_end = root.tag.index('}')
                            namespace = root.tag[1:namespace_end]
                            ns['n'] = namespace
                            print(f"[*] Detected namespace: {namespace}")
                        
                        # Define elements to find
                        elements_to_find = {
                            'totalAssets': './/n:totalAssets',
                            'netAssets': './/n:netAssets',
                            'navPerShare': './/n:navPerShare',
                            'expenseRatio': './/n:expenseRatio',
                            'turnoverRate': './/n:turnoverRate',
                            'cashPercentage': './/n:cashPercentage',
                        }
                        
                        print("\n[*] Financial metrics:")
                        for elem_name, path in elements_to_find.items():
                            element = root.find(path, ns)
                            if element is not None and element.text:
                                print(f"    {elem_name}: {element.text}")
                            else:
                                print(f"    {elem_name}: Not found")
                        
                        # Now let's look for holdings
                        print("\n[*] Searching for holdings:")
                        holdings_path = './/n:holding'
                        holdings = root.findall(holdings_path, ns)
                        print(f"[*] Found {len(holdings)} holding elements using path '{holdings_path}'")
                        
                        if holdings:
                            # Examine first few holdings
                            for i, holding in enumerate(holdings[:3]):  # First 3 holdings
                                print(f"    Holding {i+1}:")
                                
                                # Define sub-elements we want to extract
                                sub_elements = {
                                    'name': 'n:name',
                                    'ticker': 'n:ticker',
                                    'cusip': 'n:cusip',
                                    'isin': 'n:isin',
                                    'shares': 'n:shares',
                                    'marketValue': 'n:marketValue',
                                    'weight': 'n:weight',
                                }
                                
                                for elem_name, path in sub_elements.items():
                                    element = holding.find(path, ns)
                                    value = element.text if element is not None else None
                                    print(f"      {elem_name}: {value}")
                                
                                print()  # Empty line between holdings
                            
                            # If we have more than 3 holdings, say so
                            if len(holdings) > 3:
                                print(f"    ... and {len(holdings) - 3} more holdings")
                        else:
                            print("    No holdings found with standard path")
                            
                            # Try alternative paths for holdings
                            print("[*] Trying alternative paths for holdings:")
                            alt_paths = [
                                './/holding',  # No namespace
                                './/*[local-name()="holding"]',  # XPath local-name
                            ]
                            
                            for path in alt_paths:
                                try:
                                    # Need to handle namespace properly
                                    if ':' in path and 'n' in ns:
                                        alt_holdings = root.findall(path, ns)
                                    else:
                                        alt_holdings = root.findall(path)
                                    print(f"    Path '{path}': Found {len(alt_holdings)} elements")
                                    if alt_holdings and len(alt_holdings) > 0:
                                        # Show first one
                                        holding = alt_holdings[0]
                                        print(f"      Tag: {holding.tag}")
                                        # Show children
                                        children = list(holding)
                                        if children:
                                            child_tags = [child.tag for child in children[:5]]
                                            print(f"      Children (first 5): {child_tags}")
                                except Exception as e:
                                    print(f"    Path '{path}': Error - {e}")
                    except ET.ParseError as e:
                        print(f"[!] Failed to parse as XML: {e}")
                        print(f"[*] First 500 chars: {filing_resp.text[:500]}")
                else:
                    print(f"[!] Failed to fetch filing: {filing_resp.status_code}")
                    print(f"[!] Response text preview: {filing_resp.text[:200]}")
            else:
                print("[!] Could not determine primary document from index")
        else:
            print(f"[!] Failed to fetch index: {resp.status_code}")
    except Exception as e:
        print(f"[!] Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    debug_nport_xml()