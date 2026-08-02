import requests
import time
import xml.etree.ElementTree as ET
import re

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

def inspect_nport_xml():
    """Inspect the structure of an NPORT-P XML filing to find the correct elements"""
    # Use the data we saw from the debug output
    cik_str = "0000884394"  # SPY
    accession_number = "0001410368-26-055357"
    
    # Remove hyphens from accession number for URL
    acc_no_hyphens = accession_number.replace('-', '')
    
    # Get the filing
    index_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik_str)}/{acc_no_hyphens}/index.json"
    
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
            
            # Find primary document (prefer XML)
            primary_doc = None
            for filename in filenames:
                if filename.endswith('.xml'):
                    primary_doc = filename
                    break
            
            if not primary_doc and filenames:
                # Prefer common document types
                for filename in filenames:
                    if any(filename.endswith(ext) for ext in ['.xml', '.txt', '.html', '.htm']):
                        primary_doc = filename
                        break
                # If still nothing, take the first file
                if not primary_doc:
                    primary_doc = filenames[0]
            
            if primary_doc:
                # Get the filing
                filing_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik_str)}/{acc_no_hyphens}/{primary_doc}"
                filing_resp = requests.get(filing_url, headers=HEADERS, timeout=30)
                
                if filing_resp.status_code == 200:
                    print(f"[*] Successfully retrieved {len(filing_resp.text)} character XML file")
                    
                    # Parse XML
                    root = ET.fromstring(filing_resp.text)
                    
                    # Determine namespace
                    ns = {}
                    if root.tag.startswith('{'):
                        namespace_end = root.tag.index('}')
                        namespace = root.tag[1:namespace_end]
                        ns['n'] = namespace
                    
                    print(f"[*] Namespace: {ns}")
                    
                    # Let's examine the structure by looking at some elements
                    print("\n[*] Examining XML structure...")
                    
                    # Collect all unique tag names to see what's available
                    tag_counts = {}
                    for elem in root.iter():
                        # Get local name (without namespace)
                        if '}' in elem.tag:
                            local_name = elem.tag.split('}')[1]
                        else:
                            local_name = elem.tag
                        tag_counts[local_name] = tag_counts.get(local_name, 0) + 1
                    
                    # Show most common tags
                    print("[*] Top 20 element types in document:")
                    sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
                    for tag, count in sorted_tags[:20]:
                        print(f"    {tag}: {count}")
                    
                    # Now let's search for financial data elements by looking for patterns
                    print("\n[*] Searching for potential financial data elements:")
                    financial_keywords = ['asset', 'liab', 'equit', 'revenue', 'income', 'value', 'amount', 
                                        'price', 'rate', 'yield', 'nav', 'cash']
                    
                    # Look at elements with text content that might be financial data
                    potential_financial = []
                    for elem in root.iter():
                        if elem.text and elem.text.strip():
                            text = elem.text.strip()
                            # Check if it looks like a monetary value or percentage
                            if re.search(r'[\d,]+\.?\d*', text) and len(text) < 30:
                                # Get the element's tag (local name)
                                if '}' in elem.tag:
                                    tag_name = elem.tag.split('}')[1]
                                else:
                                    tag_name = elem.tag
                                
                                # Check if tag name suggests financial data
                                if any(keyword in tag_name.lower() for keyword in financial_keywords):
                                    # Get parent context
                                    parent = ""
                                    for parent_elem in root.iter():
                                        if elem in list(parent_elem):
                                            if '}' in parent_elem.tag:
                                                parent = parent_elem.tag.split('}')[1]
                                            else:
                                                parent = parent_elem.tag
                                            break
                                    
                                    potential_financial.append((tag_name, text[:50], parent))
                    
                    # Show unique financial-related elements
                    print(f"[*] Found {len(potential_financial)} potential financial data elements")
                    seen = set()
                    for tag, text, parent in potential_financial:
                        key = (tag, parent)
                        if key not in seen:
                            seen.add(key)
                            print(f"    <{tag}> in <{parent}>: '{text}'")
                    
                    # Now let's specifically look for holdings-related elements
                    print("\n[*] Searching for holding/position related elements:")
                    holding_keywords = ['holding', 'position', 'investment', 'security', 'stock', 'bond']
                    
                    holding_related = []
                    for elem in root.iter():
                        if '}' in elem.tag:
                            tag_name = elem.tag.split('}')[1]
                        else:
                            tag_name = elem.tag
                        
                        if any(keyword in tag_name.lower() for keyword in holding_keywords):
                            # Get some context
                            parent_tag = ""
                            for parent in root.iter():
                                if elem in list(parent):
                                    if '}' in parent.tag:
                                        parent_tag = parent.tag.split('}')[1]
                                    else:
                                        parent_tag = parent.tag
                                    break
                            
                            # Get a sample of text/content
                            content_preview = ""
                            if elem.text and elem.text.strip():
                                content_preview = elem.text.strip()[:30]
                            elif len(list(elem)) > 0:
                                child_tags = [child.tag.split('}')[1] if '}' in child.tag else child.tag 
                                            for child in list(elem)[:3]]
                                content_preview = f"[{len(list(elem))} children: {','.join(child_tags)}]"
                            
                            holding_related.append((tag_name, parent_tag, content_preview))
                    
                    # Show unique holding-related elements
                    print(f"[*] Found {len(holding_related)} holding-related elements")
                    seen_holdings = set()
                    for tag, parent, preview in holding_related:
                        key = (tag, parent)
                        if key not in seen_holdings:
                            seen_holdings.add(key)
                            print(f"    <{tag}> (in <{parent}>): {preview}")
                    
                    # Let's look at the actual structure around where we think holdings might be
                    print("\n[*] Examining document structure for potential holding containers:")
                    # Look for elements that might contain multiple similar items (suggesting a list)
                    for elem in root.iter():
                        children = list(elem)
                        if len(children) >= 3:  # Element with at least 3 children might be a list container
                            # Check if children have similar tags
                            if len(children) > 0:
                                first_child_tag = children[0].tag
                                same_tag_count = sum(1 for c in children if c.tag == first_child_tag)
                                if same_tag_count >= len(children) * 0.8:  # 80% same tag
                                    # Get local names
                                    if '}' in first_child_tag:
                                        first_child_tag = first_child_tag.split('}')[1]
                                    
                                    print(f"    Element <{elem.tag.split('}')[1] if '}' in elem.tag else elem.tag}> has {len(children)} children")
                                    print(f"      First child tag: {first_child_tag}")
                                    print(f"      {same_call_count}/{len(children)} children have same tag")
                                    
                                    # Show what the first child contains
                                    if len(children) > 0:
                                        child = children[0]
                                        child_info = []
                                        for grandchild in list(child)[:5]:  # First 5 grandchildren
                                            if '}' in grandchild.tag:
                                                gchild_tag = grandchild.tag.split('}')[1]
                                            else:
                                                gchild_tag = grandchild.tag
                                            child_info.append(gchild_tag)
                                        print(f"      First child's elements: {','.join(child_info)}")
                                        break  # Just show one example for now
                    
                    # Let's also look at the raw XML around where we suspect data might be
                    print("\n[*] Looking at actual XML content (first 2000 chars):")
                    print(filing_resp.text[:2000])
                    print("...")
                    
                else:
                    print(f"[!] Failed to fetch filing: {filing_resp.status_code}")
            else:
                print("[!] Could not determine document to fetch")
        else:
            print(f"[!] Failed to fetch index: {resp.status_code}")
    except Exception as e:
        print(f"[!] Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    inspect_nport_xml()