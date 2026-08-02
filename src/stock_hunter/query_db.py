import sqlite3
import pandas as pd

DB_PATH = "drawdown_analyzer.db"

def query_top_drawdowns():
    print("\n📉 TOP DRAWDOWN OPPORTUNITIES (Ordered by Deepest Drop from 52W Peak):")
    print("-" * 80)
    conn = sqlite3.connect(DB_PATH)
    query = """
    SELECT 
        s.ticker, 
        u.name, 
        u.asset_type, 
        s.price, 
        s.high_52w, 
        s.current_drawdown_pct AS `drawdown_%`, 
        s.quality_score, 
        s.valuation_tier
    FROM daily_snapshot s
    JOIN universe u ON s.ticker = u.ticker
    ORDER BY s.current_drawdown_pct ASC
    LIMIT 15;
    """
    df = pd.read_sql_query(query, conn)
    print(df.to_string(index=False))
    conn.close()

def query_sec_insider_trades():
    print("\n🏛️ RECENT SEC EDGAR FORM 4 INSIDER TRANSACTIONS:")
    print("-" * 80)
    conn = sqlite3.connect(DB_PATH)
    query = """
    SELECT 
        ticker, 
        filing_date, 
        insider_name, 
        title, 
        transaction_type, 
        shares, 
        price_per_share, 
        total_value, 
        sentiment
    FROM insider_trades
    ORDER BY filing_date DESC
    LIMIT 10;
    """
    try:
        df = pd.read_sql_query(query, conn)
        if df.empty:
            print("No insider trades recorded yet. Run the service pipeline first.")
        else:
            print(df.to_string(index=False))
    except Exception as e:
        print(f"Error querying insider trades: {e}")
    conn.close()

def query_sec_financials(ticker=None):
    print("\n📄 SEC EDGAR 10-K (ANNUAL) & 10-Q (QUARTERLY) FINANCIAL REPORTS (LAST 5 YEARS):")
    print("-" * 80)
    conn = sqlite3.connect(DB_PATH)
    where_clause = f"WHERE ticker = '{ticker.upper()}'" if ticker else ""
    query = f"""
    SELECT 
        ticker, 
        form_type, 
        filing_date, 
        fiscal_year, 
        fiscal_period, 
        revenue_usd, 
        net_income_usd, 
        report_url
    FROM sec_financials
    {where_clause}
    ORDER BY filing_date DESC
    LIMIT 15;
    """
    try:
        df = pd.read_sql_query(query, conn)
        if df.empty:
            print("No 10-K/10-Q financial filings recorded yet. Run the service pipeline first.")
        else:
            print(df.to_string(index=False))
    except Exception as e:
        print(f"Error querying SEC financials: {e}")
    conn.close()

def query_pipeline_history():
    print("\n⚙️ RECENT PIPELINE EXECUTION LOGS:")
    print("-" * 80)
    conn = sqlite3.connect(DB_PATH)
    query = "SELECT run_id, run_timestamp, duration_seconds, tickers_processed, status FROM pipeline_runs ORDER BY run_timestamp DESC LIMIT 5;"
    try:
        df = pd.read_sql_query(query, conn)
        print(df.to_string(index=False))
    except Exception as e:
        print(f"Error querying pipeline runs: {e}")
    conn.close()

if __name__ == "__main__":
    query_top_drawdowns()
    query_sec_insider_trades()
    query_sec_financials()
    query_pipeline_history()
