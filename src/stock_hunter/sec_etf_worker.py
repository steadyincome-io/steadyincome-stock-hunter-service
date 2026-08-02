import requests
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from .schema import migrate_db
from .logger import step, info, success, warning, error, ticker_start, ticker_done

try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None

# Fixed the typo here
SEC_USER_AGENT = "DrawdownAnalyzer Research research@drawdownanalyzer.com"
HEADERS = {"User-Agent": SEC_USER_AGENT}

def rate_limited_get(url, headers=HEADERS, timeout=10, min_interval=0.15):
    """Rate limited GET request to avoid overwhelming the SEC servers"""
    now = time.time()
    last = getattr(rate_limited_get, '_last_request_time', 0)
    elapsed = now - last
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    rate_limited_get._last_request_time = now
    return requests.get(url, headers=headers, timeout=timeout)


def _clean_number(text):
    if text is None:
        return 0.0
    value = str(text).strip()
    cleaned = ''.join(c for c in value if c.isdigit() or c in '.-')
    if not cleaned:
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _local_name(tag_name):
    if not tag_name:
        return ""
    return tag_name.split(':', 1)[-1].lower()


def _bs4_find_first(root, local_name):
    if root is None:
        return None
    for tag in root.find_all(True):
        if _local_name(getattr(tag, "name", "")) == local_name.lower():
            return tag
    return None


def _bs4_find_all(root, local_name):
    if root is None:
        return []
    return [
        tag for tag in root.find_all(True)
        if _local_name(getattr(tag, "name", "")) == local_name.lower()
    ]


def _extract_nport_with_bs4(xml_text):
    if BeautifulSoup is None:
        return {}, []

    soup = None
    try:
        soup = BeautifulSoup(xml_text, "xml")
    except Exception:
        try:
            soup = BeautifulSoup(xml_text, "html.parser")
        except Exception:
            soup = None

    if soup is None:
        return {}, []

    metrics = {}
    fund_info = _bs4_find_first(soup, "fundInfo")
    metric_map = {
        "total_assets": "totAssets",
        "net_assets": "netAssets",
    }
    for metric_name, tag_name in metric_map.items():
        value = None
        if fund_info is not None:
            elem = _bs4_find_first(fund_info, tag_name)
            if elem is not None:
                value = elem.get_text(strip=True)
        if value is None:
            elem = _bs4_find_first(soup, tag_name)
            if elem is not None:
                value = elem.get_text(strip=True)
        metrics[metric_name] = _clean_number(value)

    cash_assets = 0.0
    cash_elem = _bs4_find_first(soup, "assetsAmtCash")
    if cash_elem is not None:
        cash_assets = _clean_number(cash_elem.get_text(strip=True))
    metrics["cash_percentage"] = (
        (cash_assets / metrics["total_assets"]) * 100 if metrics.get("total_assets") else 0.0
    )
    metrics.setdefault("expense_ratio", 0.0)
    metrics.setdefault("turnover_rate", 0.0)

    holdings = []
    invst_or_seccs = _bs4_find_first(soup, "invstOrSecs")
    if invst_or_seccs is not None:
        investments = invst_or_seccs.find_all(recursive=False)
        info(f"Found {len(investments)} investment instruments")
        for investment in investments:
            holding = {"ticker": ""}

            # <cusip> is a direct child of <invstOrSec> in current NPORT-P schema.
            cusip_elem = _bs4_find_first(investment, "cusip")
            holding["cusip"] = cusip_elem.get_text(strip=True) if cusip_elem else ""

            # <isin value="..."/> lives under <identifiers>, value is an attribute not text.
            id_elem = _bs4_find_first(investment, "identifiers")
            isin_elem = _bs4_find_first(id_elem, "isin") if id_elem is not None else None
            holding["isin"] = (isin_elem.get("value", "") if isin_elem is not None else "") or ""

            name_elem = _bs4_find_first(investment, "name")
            if name_elem is None:
                issuer_elem = _bs4_find_first(investment, "issuer")
                name_elem = _bs4_find_first(issuer_elem, "name") if issuer_elem is not None else None
            holding["name"] = name_elem.get_text(strip=True) if name_elem else ""

            # <balance> holds the numeric quantity directly as text; <units> is a sibling unit label.
            balance_elem = _bs4_find_first(investment, "balance")
            total_bal_elem = _bs4_find_first(investment, "totalBalAmt")
            holding["shares"] = _clean_number(
                balance_elem.get_text(strip=True) if balance_elem else (
                    total_bal_elem.get_text(strip=True) if total_bal_elem else 0.0
                )
            )

            val_usd_elem = _bs4_find_first(investment, "valUSD")
            bsk_val_elem = _bs4_find_first(investment, "bskVal")
            holding["market_value"] = _clean_number(
                val_usd_elem.get_text(strip=True) if val_usd_elem else (
                    bsk_val_elem.get_text(strip=True) if bsk_val_elem else 0.0
                )
            )

            pct_val_elem = _bs4_find_first(investment, "pctVal")
            holding["weight_pct"] = _clean_number(pct_val_elem.get_text(strip=True) if pct_val_elem else 0.0)

            curr_elem = _bs4_find_first(investment, "curCd")
            if curr_elem is None:
                curr_elem = _bs4_find_first(investment, "currCd")
            holding["currency"] = curr_elem.get_text(strip=True) if curr_elem else "USD"

            # <invCountry> is a direct child of <invstOrSec> in current NPORT-P schema.
            country_elem = _bs4_find_first(investment, "invCountry")
            holding["country"] = country_elem.get_text(strip=True) if country_elem else "US"

            asset_cat_elem = _bs4_find_first(investment, "assetCat")
            holding["asset_category"] = asset_cat_elem.get_text(strip=True) if asset_cat_elem else "Equity"

            if holding.get("name") or holding.get("cusip") or holding.get("isin"):
                holdings.append(holding)

    success(f"Extracted {len(holdings)} holdings from N-PORT filing")
    return metrics, holdings

def fetch_sec_cik_mapping():
    """Fetch official SEC EDGAR company tickers to CIK map."""
    url = "https://www.sec.gov/files/company_tickers.json"
    try:
        resp = rate_limited_get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            cik_map = {}
            for item in data.values():
                ticker = item['ticker'].upper()
                cik = str(item['cik_str']).zfill(10)
                cik_map[ticker] = cik
            return cik_map
        else:
            warning(f"SEC CIK map fetch failed: {resp.status_code}")
            return {}
    except Exception as e:
        error(f"SEC CIK map fetch failed: {e}")
        return {}

def fetch_nport_ncen_filings(ticker, cik_str, years_back=2):
    """Fetch N-PORT and N-CEN filings for an ETF from SEC EDGAR."""
    url = f"https://data.sec.gov/submissions/CIK{cik_str}.json"
    filings = []
    current_year = datetime.now().year
    min_year = current_year - years_back

    try:
        resp = rate_limited_get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return []
        
        data = resp.json()
        recent = data.get('filings', {}).get('recent', {})
        forms = recent.get('form', [])
        filing_dates = recent.get('filingDate', [])
        report_dates = recent.get('reportDate', [])
        accession_numbers = recent.get('accessionNumber', [])
        primary_docs = recent.get('primaryDocument', [])
        doc_descs = recent.get('primaryDocDescription', [])

        for i in range(len(forms)):
            form = forms[i]
            # Updated to include NPORT-P and NPORT-EX which are the actual form types we see
            if form in ['N-PORT', 'N-CEN', 'NPORT-P', 'NPORT-EX']:
                f_date = filing_dates[i] if i < len(filing_dates) else ''
                f_year = int(f_date.split('-')[0]) if f_date else current_year
                
                if f_year >= min_year:
                    acc_num = accession_numbers[i] if i < len(accession_numbers) else ''
                    acc_no_hyphens = acc_num.replace('-', '')
                    p_doc = primary_docs[i] if i < len(primary_docs) else ''
                    
                    report_url = ""
                    if acc_no_hyphens and p_doc:
                        clean_p_doc = p_doc.split("/")[-1] if "/" in p_doc else p_doc
                        # Ensure CIK is integer for URL construction and fetch raw XML
                        report_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik_str)}/{acc_no_hyphens}/{clean_p_doc}"
                    
                    p_date = report_dates[i] if i < len(report_dates) else f_date
                    doc_desc = doc_descs[i] if i < len(doc_descs) else form

                    filings.append({
                        'ticker': ticker,
                        'cik': cik_str,
                        'form_type': form,
                        'filing_date': f_date,
                        'period_end_date': p_date,
                        'accession_number': acc_num,
                        'primary_doc_description': doc_desc,
                        'report_url': report_url
                    })

    except Exception as e:
        error(f"{ticker}: error fetching N-PORT/N-CEN filings: {e}")

    return filings

def parse_nport_xml(xml_text):
    """Parse N-PORT XML and extract key metrics and holdings."""
    try:
        root = ET.fromstring(xml_text)
    except Exception as e:
        warning(f"N-PORT XML parse error with strict parser: {e}")
        if BeautifulSoup is not None:
            metrics, holdings = _extract_nport_with_bs4(xml_text)
            if metrics or holdings:
                warning("Recovered N-PORT parse using tolerant XML parser")
                return metrics, holdings
        error(f"N-PORT XML parse error: {e}")
        return {}, []
    
    # Define namespace - based on our analysis, this is the correct namespace
    ns = {'n': 'http://www.sec.gov/edgar/nport'}
    
    # Extract key metrics
    metrics = {}
    
    # Try to find fundInfo section which contains the financial data
    fund_info = None
    # Try with namespace first
    fund_info = root.find('.//n:fundInfo', ns)
    if fund_info is None:
        # Try without namespace
        fund_info = root.find('.//fundInfo')
    
    if fund_info is not None:
        # Extract various financial metrics
        metric_map = {
            'total_assets': 'totAssets',
            'net_assets': 'netAssets',
            # Note: We don't have a direct expense ratio in the main fundInfo for ETFs
            # It might be in a different section or calculated
        }
        
        for metric_name, tag_name in metric_map.items():
            element = fund_info.find(f'n:{tag_name}', ns)
            if element is not None and element.text:
                try:
                    # Try to convert to float for numeric values
                    value = element.text.strip()
                    # Remove commas and other non-numeric characters except decimal point and minus
                    cleaned = ''.join(c for c in value if c.isdigit() or c in '.-')
                    if cleaned:
                        metrics[metric_name] = float(cleaned)
                    else:
                        metrics[metric_name] = 0.0
                except ValueError:
                    # If conversion fails, store as string or 0
                    try:
                        metrics[metric_name] = float(element.text)
                    except ValueError:
                        metrics[metric_name] = 0.0
            else:
                # Try direct search in the document
                element = root.find(f'.//n:{tag_name}', ns)
                if element is not None and element.text:
                    try:
                        value = element.text.strip()
                        cleaned = ''.join(c for c in value if c.isdigit() or c in '.-')
                        if cleaned:
                            metrics[metric_name] = float(cleaned)
                        else:
                            metrics[metric_name] = 0.0
                    except ValueError:
                        try:
                            metrics[metric_name] = float(element.text)
                        except ValueError:
                            metrics[metric_name] = 0.0
                else:
                    metrics[metric_name] = 0.0
    else:
        # Fallback: try to find elements directly
        info("Could not find fundInfo section, trying direct element search")
        direct_mapping = {
            'total_assets': 'totAssets',
            'net_assets': 'netAssets',
        }
        
        for metric_name, tag_name in direct_mapping.items():
            element = root.find(f'.//n:{tag_name}', ns)
            if element is not None and element.text:
                try:
                    value = element.text.strip()
                    cleaned = ''.join(c for c in value if c.isdigit() or c in '.-')
                    if cleaned:
                        metrics[metric_name] = float(cleaned)
                    else:
                        metrics[metric_name] = 0.0
                except ValueError:
                    try:
                        metrics[metric_name] = float(element.text)
                    except ValueError:
                        metrics[metric_name] = 0.0
            else:
                metrics[metric_name] = 0.0
    
    # Calculate cash percentage if we have the components
    if 'total_assets' in metrics and metrics['total_assets'] > 0:
        # Look for cash assets
        cash_assets = 0.0
        # Try to find cash position
        cash_elem = root.find('.//n:assetsAmtCash', ns)
        if cash_elem is not None and cash_elem.text:
            try:
                cash_assets = float(cash_elem.text)
            except ValueError:
                pass
        
        if cash_assets > 0:
            metrics['cash_percentage'] = (cash_assets / metrics['total_assets']) * 100
        else:
            # If we can't find direct cash, set to 0 or try to estimate
            metrics['cash_percentage'] = 0.0
    else:
        metrics['cash_percentage'] = 0.0
    
    # Set default values for metrics we couldn't find
    if 'total_assets' not in metrics:
        metrics['total_assets'] = 0.0
    if 'net_assets' not in metrics:
        metrics['net_assets'] = 0.0
    if 'expense_ratio' not in metrics:
        # For ETFs, expense ratio might be in a different location or not available
        # We'll set a default or try to find it elsewhere
        metrics['expense_ratio'] = 0.0
    if 'turnover_rate' not in metrics:
        metrics['turnover_rate'] = 0.0
    
    # Extract holdings
    holdings = []
    
    # Find the investments section
    invst_or_seccs = None
    # Try various paths to find the investments
    paths_to_try = [
        './/n:invstOrSecs',
        './/invstOrSecs',
    ]
    
    for path in paths_to_try:
        invst_or_seccs = root.find(path, ns)
        if invst_or_seccs is not None:
            break
    
    if invst_or_seccs is not None:
        # Find individual investments/securities
        # Based on our inspection, these are direct children of invstOrSecs
        investments = list(invst_or_seccs)  # Get all direct children
        
        info(f"Found {len(investments)} investment instruments")
        
        for investment in investments:
            holding = {'ticker': ''}

            # <cusip> is a direct child of <invstOrSec>; <isin value="..."/> is an attribute
            # nested under <identifiers> in the current NPORT-P schema (not text content).
            cusip_elem = investment.find('n:cusip', ns)
            holding['cusip'] = cusip_elem.text.strip() if cusip_elem is not None and cusip_elem.text else ''

            isin_value = ''
            id_elem = investment.find('n:identifiers', ns)
            if id_elem is not None:
                isin_elem = id_elem.find('n:isin', ns)
                if isin_elem is not None:
                    isin_value = isin_elem.get('value', '') or ''
            holding['isin'] = isin_value

            # <name> is a direct child of <invstOrSec>; fall back to a nested <issuer>/<name>.
            name_elem = investment.find('n:name', ns)
            if name_elem is None or not name_elem.text:
                issuer_elem = investment.find('.//n:issuer', ns)
                name_elem = issuer_elem.find('.//n:name', ns) if issuer_elem is not None else None
            holding['name'] = name_elem.text.strip() if name_elem is not None and name_elem.text else ''

            # <balance> holds the numeric quantity as direct text; <units> is a sibling unit label.
            balance_elem = investment.find('n:balance', ns)
            if balance_elem is not None and balance_elem.text:
                try:
                    holding['shares'] = float(balance_elem.text.strip())
                except ValueError:
                    holding['shares'] = 0.0
            else:
                total_bal_elem = investment.find('n:totalBalAmt', ns)
                if total_bal_elem is not None and total_bal_elem.text:
                    try:
                        holding['shares'] = float(total_bal_elem.text.strip())
                    except ValueError:
                        holding['shares'] = 0.0
                else:
                    holding['shares'] = 0.0
            
            # Extract market value
            val_usd_elem = investment.find('.//n:valUSD', ns)
            if val_usd_elem is not None and val_usd_elem.text:
                try:
                    holding['market_value'] = float(val_usd_elem.text)
                except ValueError:
                    holding['market_value'] = 0.0
            else:
                # Try alternative: bskVal (basket value?)
                bsk_val_elem = investment.find('.//n:bskVal', ns)
                if bsk_val_elem is not None and bsk_val_elem.text:
                    try:
                        holding['market_value'] = float(bsk_val_elem.text)
                    except ValueError:
                        holding['market_value'] = 0.0
                else:
                    holding['market_value'] = 0.0
            
            # Extract percentage/weight
            pct_val_elem = investment.find('.//n:pctVal', ns)
            if pct_val_elem is not None and pct_val_elem.text:
                try:
                    # This is already a percentage
                    holding['weight_pct'] = float(pct_val_elem.text)
                except ValueError:
                    holding['weight_pct'] = 0.0
            else:
                # If we don't have a direct percentage, we might need to calculate it
                # based on market value and total assets
                holding['weight_pct'] = 0.0  # Will calculate later if needed
            
            # Extract currency (default to USD if not specified)
            curr_elem = investment.find('n:curCd', ns)
            if curr_elem is None:
                curr_elem = investment.find('.//n:currCd', ns)
            holding['currency'] = curr_elem.text.strip() if curr_elem is not None and curr_elem.text else 'USD'

            # <invCountry> is a direct child of <invstOrSec> in the current NPORT-P schema.
            country_elem = investment.find('n:invCountry', ns)
            holding['country'] = country_elem.text.strip() if country_elem is not None and country_elem.text else 'US'
            
            # Asset category - try to determine from investment type or default
            # This is complex to determine accurately, so we'll use a default or try to infer
            asset_cat_elem = investment.find('.//n:assetCat', ns)
            if asset_cat_elem is not None and asset_cat_elem.text:
                holding['asset_category'] = asset_cat_elem.text.strip()
            else:
                # Default to 'Equity' for most ETF holdings (this is a simplification)
                # In a real implementation, we'd need to map this properly
                holding['asset_category'] = 'Equity'
            
            # Only add the holding if we have at least a name or identifier
            if holding['name'] or holding['cusip'] or holding['isin']:
                holdings.append(holding)
    
    success(f"Extracted {len(holdings)} holdings from N-PORT filing")
    
    return metrics, holdings

def fetch_and_parse_etf_filing(report_url, form_type):
    """Fetch and parse an ETF filing (N-PORT or N-CEN)."""
    if not report_url:
        return {}, []
    
    try:
        resp = rate_limited_get(report_url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            return {}, []
        
        xml_text = resp.text
        
        if form_type in ['N-PORT', 'NPORT-P', 'NPORT-EX']:
            return parse_nport_xml(xml_text)
        elif form_type == 'N-CEN':
            # For N-CEN filings, we might need different parsing
            # For now, return empty as N-CEN typically doesn't have detailed holdings
            return {}, []
        else:
            warning(f"Unknown form type: {form_type}")
            return {}, []
            
    except Exception as e:
        error(f"Error fetching/parsing {form_type} from {report_url}: {e}")
        return {}, []

def sync_etf_reports(db_path="drawdown_analyzer.db", years_back=2):
    """Sync N-PORT and N-CEN filings for all ETFs in the universe."""
    step(f"ETF reports worker: start (last {years_back} years)")
    
    # Ensure database schema is up to date
    mcount = migrate_db(db_path)
    if mcount:
        success(f"Migration applied {mcount} schema change(s)")
    else:
        success("Database schema is up to date")
    
    # Get CIK mapping
    cik_map = fetch_sec_cik_mapping()
    if not cik_map:
        warning("SEC CIK map unavailable")
        return 0
    
    # Connect to database
    import sqlite3
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    cursor = conn.cursor()
    
    # Get all active ETFs
    cursor.execute("SELECT ticker FROM universe WHERE asset_type = 'ETF' AND status = 'active'")
    etfs = [row[0] for row in cursor.fetchall()]
    success(f"Found {len(etfs)} active ETFs to process")
    
    total_records = 0
    new_count = 0
    holdings_count = 0
    skipped_count = 0
    
    for ticker in etfs:
        clean_ticker = ticker.replace('.', '-')
        cik = cik_map.get(clean_ticker)
        if not cik:
            continue
        
        # Check what we already have for this ticker
        cursor.execute("""
            SELECT accession_number FROM sec_etf_reports WHERE ticker = ?
        """, (ticker,))
        existing_accessions = {row[0] for row in cursor.fetchall()}
        
        # Fetch filings
        filings = fetch_nport_ncen_filings(ticker, cik, years_back)
        
        # Filter for new filings only
        new_filings = [f for f in filings if f['accession_number'] not in existing_accessions]
        skip_count = len(filings) - len(new_filings)
        skipped_count += skip_count
        
        if not new_filings:
            ticker_done(ticker, f"all {len(filings)} filings already up to date; skipped {skip_count}")
            continue
        
        ticker_start(ticker, f"{len(new_filings)} new filings, {skip_count} already complete")
        
        # Process each new filing
        for f in new_filings:
            form_type = f['form_type']
            end_date = f['period_end_date']
            accn = f['accession_number']
            
            # Parse the filing
            metrics, holdings = fetch_and_parse_etf_filing(f['report_url'], form_type)
            
            # Insert into sec_etf_reports
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO sec_etf_reports (
                        ticker, cik, form_type, filing_date, period_end_date,
                        accession_number, primary_doc_description,
                        total_assets, net_assets, nav_per_share,
                        expense_ratio, turnover_rate, cash_percentage, report_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    f['ticker'], f['cik'], form_type, f['filing_date'], end_date,
                    accn, f['primary_doc_description'],
                    metrics.get('total_assets'), metrics.get('net_assets'),
                    metrics.get('nav_per_share'), metrics.get('expense_ratio'),
                    metrics.get('turnover_rate'), metrics.get('cash_percentage'),
                    f['report_url']
                ))
                if cursor.rowcount > 0:
                    new_count += 1
                total_records += 1
            except Exception as e:
                error(f"{ticker}: error inserting {form_type}: {e}")
                conn.rollback()
                continue
            
            # Insert holdings for N-PORT filings
            if form_type in ['N-PORT', 'NPORT-P', 'NPORT-EX'] and holdings:
                for h in holdings:
                    try:
                        cursor.execute("""
                        INSERT OR IGNORE INTO etf_holdings (
                            ticker, filing_date, holding_name, holding_ticker,
                            cusip, isin, shares, market_value, weight_pct,
                            asset_category, country, currency
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            ticker, end_date, h['name'], h.get('ticker', ''),
                            h['cusip'], h['isin'], h['shares'], h['market_value'],
                            h['weight_pct'], h['asset_category'], h['country'],
                            h['currency']
                        ))
                        holdings_count += 1
                        total_records += 1
                    except Exception as e:
                        error(f"{ticker}: error inserting holding: {e}")
                        conn.rollback()
                        continue
            
            # Commit after each filing to avoid losing too much data on failure
            conn.commit()
    
    # Final commit and cleanup
    conn.commit()
    conn.close()
    
    success("ETF reports sync complete")
    info(f"New filings processed: {new_count}")
    info(f"Holdings inserted: {holdings_count}")
    info(f"Already up to date: {skipped_count} skipped")
    info(f"Total records touched: {total_records}")
    
    return total_records

if __name__ == "__main__":
    sync_etf_reports()
