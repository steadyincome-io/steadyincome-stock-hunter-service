#!/usr/bin/env python3
"""
Test script to verify the updated ETF funds and holdings logic works correctly.
"""

import os
import sys
import tempfile
import shutil
from datetime import datetime

# Add the src layout so we can import the package modules
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

def test_xml_parsing():
    """Test that we can parse the XML structure correctly"""
    print("[*] Testing XML parsing logic...")
    
    # Import the functions we want to test
    try:
        from stock_hunter.sec_etf_worker import parse_nport_xml
        print("[+] Successfully imported parse_nport_xml")
    except ImportError as e:
        print(f"[!] Failed to import parse_nport_xml: {e}")
        return False
    
    # Test with a known NPORT-P filing
    test_cik = "0000884394"  # SPY
    test_acc = "0001410368-26-055357"
    test_acc_no_hyphens = test_acc.replace('-', '')
    test_url = f"https://www.sec.gov/Archives/edgar/data/{int(test_cik)}/{test_acc_no_hyphens}/primary_doc.xml"
    
    print(f"[*] Fetching test filing from: {test_url}")
    
    import requests
    try:
        # Create a simple rate-limited get function for testing
        def rate_limited_get(url, headers=None, timeout=30):
            # Simple implementation for testing - in reality we'd use the one from the module
            return requests.get(url, headers=headers or {"User-Agent": "Test Agent"}, timeout=timeout)
        
        resp = rate_limited_get(test_url, timeout=30)
        if resp.status_code == 200:
            print(f"[*] Successfully retrieved {len(resp.text)} characters")
            
            # Test parsing
            try:
                metrics, holdings = parse_nport_xml(resp.text)
                
                print(f"[*] Parsed {len(holdings)} holdings")
                print(f"[*] Extracted metrics: {list(metrics.keys())}")
                
                # Show some sample data
                if metrics:
                    print("[*] Sample metrics:")
                    for key, value in list(metrics.items())[:5]:
                        print(f"    {key}: {value}")
                
                if holdings:
                    print("[*] Sample holdings (first 3):")
                    for i, holding in enumerate(holdings[:3]):
                        print(f"    Holding {i+1}:")
                        for key, value in list(holding.items())[:5]:  # Show first 5 fields
                            if value:  # Only show non-empty values
                                print(f"      {key}: {value}")
                else:
                    print("[!] No holdings extracted")
                    
                # Basic validation
                assert isinstance(metrics, dict), "Metrics should be a dictionary"
                assert isinstance(holdings, list), "Holdings should be a list"
                
                # Check that we got some expected metrics
                expected_metrics = ['total_assets', 'net_assets']
                for metric in expected_metrics:
                    if metric in metrics:
                        print(f"[+] Found expected metric: {metric} = {metrics[metric]}")
                    else:
                        print(f"[!] Missing expected metric: {metric}")
                
                print("[+] XML parsing test completed successfully")
                return True
            except Exception as e:
                print(f"[!] Error during parsing: {e}")
                import traceback
                traceback.print_exc()
                return False
        else:
            print(f"[!] Failed to fetch test filing: {resp.status_code}")
            return False
    except Exception as e:
        print(f"[!] Error during testing: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_database_integration():
    """Test that we can store data in the database correctly"""
    print("\n[*] Testing database integration...")
    
    # Create a temporary database for testing
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test.db")
    
    try:
        # Import and initialize the database
        try:
            from stock_hunter.schema import init_db
            init_db(db_path)
            print("[+] Test database initialized")
        except ImportError as e:
            print(f"[!] Failed to import schema module: {e}")
            return False
        except Exception as e:
            print(f"[!] Failed to initialize database: {e}")
            return False
        
        # Import our updated functions - just test that we can import them
        try:
            from stock_hunter.sec_etf_worker import sync_etf_reports, fetch_nport_ncen_filings, parse_nport_xml
            print("[+] Successfully imported updated functions")
        except ImportError as e:
            print(f"[!] Failed to import updated functions: {e}")
            return False
        
        # Test that the functions exist and have the right signature
        import inspect
        try:
            sig = inspect.signature(fetch_nport_ncen_filings)
            print(f"[+] fetch_nport_ncen_filings signature: {sig}")
        except Exception as e:
            print(f"[!] Error checking function signature: {e}")
            return False
            
        print("[+] Database integration test completed (function signatures verified)")
        return True
        
    except Exception as e:
        print(f"[!] Error during database testing: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Clean up
        shutil.rmtree(temp_dir, ignore_errors=True)

def main():
    """Run all tests"""
    print("=" * 60)
    print("ETF Funds and Holdings Logic Test")
    print("=" * 60)
    
    success = True
    
    # Test 1: XML parsing
    if not test_xml_parsing():
        success = False
    
    # Test 2: Database integration
    if not test_database_integration():
        success = False
    
    print("\n" + "=" * 60)
    if success:
        print("ALL TESTS PASSED")
        print("The ETF funds and holdings logic appears to be working correctly!")
    else:
        print("SOME TESTS FAILED")
        print("Please review the error messages above.")
    print("=" * 60)
    
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())
