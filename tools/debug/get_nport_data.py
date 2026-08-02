import requests
import xml.etree.ElementTree as ET

SEC_USER_AGENT = "DrawdownAnalyzer Research research@drawdownanalyzer.com"
HEADERS = {"User-Agent": SEC_USER_AGENT}

def get_nport_data():
    """Extract data from an NPORT-P filing to understand the structure"""
    # Use the data we saw from the debug output
    cik_str = "0000884394"  # SPY
    accession_number = "0001410368-26-055357"
    
    # Remove hyphens from accession number for URL
    acc_no_hyphens = accession_number.replace('-', '')
    
    # Get the filing directly
    filing_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik_str)}/{acc_no_hyphens}/primary_doc.xml"
    print(f"[*] Fetching filing from: {filing_url}")
    
    try:
        resp = requests.get(filing_url, headers=HEADERS, timeout=30)
        if resp.status_code == 200:
            print(f"[*] Successfully fetched filing ({len(resp.text)} characters)")
            
            # Parse XML
            root = ET.fromstring(resp.text)
            
            # Determine namespace from root tag
            ns = {}
            if root.tag.startswith('{') and '}' in root.tag:
                namespace = root.tag[1:root.tag.index('}')]
                ns['n'] = namespace
                print(f"[*] Detected namespace: {namespace}")
            else:
                # Default to the known NPORT namespace
                ns = {'n': 'http://www.sec.gov/edgar/nport'}
                print(f"[*] Using default namespace: {ns['n']}")
            
            print(f"[*] Using namespace for lookup: {ns}")
            
            # Extract fund information - let's look for the specific elements we saw worked
            print("\n[*] Extracting fund information:")
            
            # These are the paths we know work from our earlier debugging
            fund_elements = {
                'Total Assets': './/n:totAssets',
                'Total Liabilities': './/n:totLiabs', 
                'Net Assets': './/n:netAssets',
            }
            
            for name, path in fund_elements.items():
                element = root.find(path, ns)
                if element is not None and element.text:
                    print(f"  {name}: {element.text}")
                else:
                    print(f"  {name}: Not found")
            
            # Now let's look for holdings - based on the tag names we saw in the inspection
            print("\n[*] Examining holdings structure:")
            
            # From our inspection, we saw these tags:
            # invstOrSc is the container for investments
            # Each invstOrSec contains the details of one investment
            
            # Find all invstOrSec elements (these are the individual holdings)
            holdings = root.findall('.//n:invstOrSec', ns)
            print(f"[*] Found {len(holdings)} investment instruments (invstOrSec elements)")
            
            if holdings:
                print("\n[*] Sample holdings (first 3):")
                for i, holding in enumerate(holdings[:3]):
                    print(f"  Holding {i+1}:")
                    
                    # Extract the key information we need for our etf_holdings table
                    # Based on the tags we saw in the inspection:
                    
                    # Identification
                    cusip_elem = holding.find('.//n:cusip', ns)
                    cusip = cusip_elem.text if cusip_elem is not None else ''
                    
                    isin_elem = holding.find('.//n:isin', ns)
                    isin = isin_elem.text if isin_elem is not None else ''
                    
                    # Name/description
                    name_elem = holding.find('.//n:name', ns)
                    name = name_elem.text if name_elem is not None else ''
                    
                    # Ticker symbol
                    ticker_elem = holding.find('.//n:ticker', ns)
                    ticker = ticker_elem.text if ticker_elem is not None else ''
                    
                    # Balance/Shares
                    shares_elem = holding.find('.//n:shares', ns)
                    shares = shares_elem.text if shares_elem is not None else '0'
                    
                    # Market value
                    val_usd_elem = holding.find('.//n:valUSD', ns)
                    market_value = val_usd_elem.text if val_usd_elem is not None else '0'
                    
                    # Weight/percentage
                    weight_elem = holding.find('.//n:pctVal', ns)
                    weight_pct = weight_elem.text if weight_elem is not None else '0'
                    
                    print(f"    Identifier: CUSIP={cusip}, ISIN={isin}")
                    print(f"    Name: {name[:60]}{'...' if len(name) > 60 else ''}")
                    print(f"    Ticker: {ticker}")
                    print(f"    Shares: {shares}")
                    print(f"    Market Value: ${float(market_value):,.2f}" if money_value.replace('.','').isdigit() else f"    Market Value: {market_value}")
                    print(f"    Weight: {float(weight_pct):.2f}%" if weight_pct.replace('.','').isdigit() else f"    Weight: {weight_pct}")
                    print()
                
                if len(holdings) > 3:
                    print(f"    ... and {len(holdings) - 3} more holdings")
            else:
                print("  No holdings found with standard path")
                
                # Let's see what's actually in the document by looking for these tags
                print("\n[*] Checking for presence of key tags:")
                tags_to_check = ['invstOrSec', 'cusip', 'isin', 'name', 'ticker', 'shares', 'valUSD', 'pctVal']
                for tag in tags_to_check:
                    elements = root.findall(f'.//n:{tag}', ns)
                    print(f"  <{tag}>: {len(elements)} elements found")
                    
        else:
            print(f"[!] Failed to fetch filing: {resp.status_code}")
    except Exception as e:
        print(f"[!] Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    get_nport_data()