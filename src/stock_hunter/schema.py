import csv
import sqlite3
import os
from .logger import info, success, warning

DB_NAME = "drawdown_analyzer.db"
# Produced weekly by universe_scanner.py (see .github/workflows/universe-scan.yml),
# not by pipeline.py itself -- see sync_universe_from_csv() below.
UNIVERSE_CSV_PATH = "data/universe_50b.csv"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS universe (
    ticker TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    asset_type TEXT NOT NULL, -- 'Stock' or 'ETF'
    sector TEXT,
    industry TEXT,
    market_cap REAL, -- in Billions USD
    status TEXT DEFAULT 'active',
    last_updated DATETIME
);

CREATE TABLE IF NOT EXISTS daily_snapshot (
    ticker TEXT PRIMARY KEY,
    price REAL NOT NULL,
    price_change_1d REAL,
    high_52w REAL,
    low_52w REAL,
    current_drawdown_pct REAL,
    max_drawdown_1y_pct REAL,
    pe_ratio REAL,
    forward_pe REAL,
    ev_ebitda REAL,
    fcf_yield_pct REAL,
    dividend_yield_pct REAL,
    quality_score INTEGER,
    investment_score INTEGER,
    valuation_tier TEXT,
    investment_verdict TEXT,
    updated_at DATETIME,
    FOREIGN KEY(ticker) REFERENCES universe(ticker)
);

CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    trade_date DATE NOT NULL,
    close_price REAL NOT NULL,
    volume INTEGER,
    UNIQUE(ticker, trade_date)
);

CREATE TABLE IF NOT EXISTS insider_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    filing_date DATE,
    trade_date DATE,
    insider_name TEXT,
    title TEXT,
    shares INTEGER,
    code TEXT,
    transaction_type TEXT,
    price_per_share REAL,
    total_value REAL,
    sentiment TEXT,
    UNIQUE(ticker, insider_name, filing_date, total_value)
);

CREATE TABLE IF NOT EXISTS congress_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    politician TEXT,
    chamber TEXT,
    party TEXT,
    transaction_type TEXT,
    amount_range TEXT,
    disclosure_date DATE,
    trade_date DATE
);

CREATE TABLE IF NOT EXISTS sec_financials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    cik TEXT NOT NULL,
    form_type TEXT NOT NULL, -- \'10-K\' or \'10-Q\'
    filing_date DATE NOT NULL,
    period_end_date DATE,
    fiscal_year INTEGER,
    fiscal_period TEXT, -- \'FY\', \'Q1\', \'Q2\', \'Q3\', \'Q4\'
    accession_number TEXT,
    primary_doc_description TEXT,
    revenue_usd REAL,
    net_income_usd REAL,
    operating_income_usd REAL,
    total_assets_usd REAL,
    total_liabilities_usd REAL,
    stockholders_equity_usd REAL,
    total_debt_usd REAL,
    debt_to_equity_ratio REAL,
    eps_diluted REAL,
    report_url TEXT,
    -- Qualitative narrative sections from 10-Q filings (stored as JSON text blobs)
    narrative_mda TEXT,          -- Management Discussion & Analysis
    narrative_risk_factors TEXT, -- Risk Factors (new/updated)
    narrative_legal TEXT,        -- Legal Proceedings
    narrative_commitments TEXT,  -- Commitments and Contingencies
    narrative_buybacks TEXT,     -- Share Repurchases
    narrative_liquidity TEXT,    -- Liquidity and Capital Resources
    narrative_subsequent TEXT,   -- Subsequent Events
    -- LLM-derived compact narrative metrics (populated by sec_financials_worker.py)
    risk_score INTEGER,          -- 0-100, higher = riskier
    risk_summary TEXT,           -- <=200 characters, plain English summary
    risk_sentiment TEXT,         -- 'positive', 'neutral', 'negative' for individual risk factors
    md_a_summary TEXT,           -- <=250 characters, short MD&A digest
    full_sentiment TEXT,         -- combined sentiment from ALL narrative sections
    comprehensive_summary TEXT,  -- comprehensive bullet points summary (JSON string)
    UNIQUE(ticker, form_type, filing_date, accession_number)
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    run_timestamp DATETIME,
    duration_seconds REAL,
    tickers_processed INTEGER,
    status TEXT,
    summary_json TEXT
);

CREATE TABLE IF NOT EXISTS sec_etf_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    cik TEXT NOT NULL,
    form_type TEXT NOT NULL, -- 'N-PORT' or 'N-CEN'
    filing_date DATE NOT NULL,
    period_end_date DATE,
    accession_number TEXT,
    primary_doc_description TEXT,
    total_assets REAL,
    net_assets REAL,
    nav_per_share REAL,
    expense_ratio REAL,
    turnover_rate REAL,
    cash_percentage REAL,
    report_url TEXT,
    UNIQUE(ticker, form_type, filing_date, accession_number)
);

CREATE TABLE IF NOT EXISTS etf_holdings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    filing_date DATE NOT NULL,
    holding_name TEXT NOT NULL,
    holding_ticker TEXT,
    cusip TEXT,
    isin TEXT,
    shares REAL,
    market_value REAL,
    weight_pct REAL,
    asset_category TEXT,
    country TEXT,
    currency TEXT,
    FOREIGN KEY(ticker) REFERENCES universe(ticker),
    UNIQUE(ticker, filing_date, holding_name, cusip, isin)
);
"""

def init_db(db_path=DB_NAME):
    info(f"Initializing SQLite database schema at: {db_path}")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.executescript(SCHEMA_SQL)

    # Add new columns to daily_snapshot if they don't exist
    try:
        cursor.execute("ALTER TABLE daily_snapshot ADD COLUMN investment_score INTEGER")
    except sqlite3.OperationalError:
        pass  # Column already exists

    conn.commit()
    conn.close()
    success("Database schema ready")

def migrate_db(db_path=DB_NAME):
    """Safely add new columns to existing databases without destroying data.
    Uses ALTER TABLE ... ADD COLUMN which is a no-op if column already exists (via try/except).
    """
    narrative_columns = [
        ("narrative_mda",          "TEXT"),
        ("narrative_risk_factors", "TEXT"),
        ("narrative_legal",        "TEXT"),
        ("narrative_commitments",  "TEXT"),
        ("narrative_buybacks",     "TEXT"),
        ("narrative_liquidity",    "TEXT"),
        ("narrative_subsequent",   "TEXT"),
        ("narrative_business",     "TEXT"),
        ("total_debt_usd",         "REAL"),
        ("debt_to_equity_ratio",   "REAL"),
        # Distress-scoring inputs (Altman Z / Piotroski F)
        ("current_assets_usd",       "REAL"),
        ("current_liabilities_usd",  "REAL"),
        ("retained_earnings_usd",    "REAL"),
        ("operating_cash_flow_usd",  "REAL"),
        ("cash_usd",                 "REAL"),
        ("shares_outstanding",       "REAL"),
        # Valuation-multiple inputs (so PE/EV-EBITDA/FCF-yield/dividend-yield can be
        # computed from SEC fundamentals instead of trusting yfinance's own figures)
        ("capex_usd",                    "REAL"),
        ("depreciation_amortization_usd", "REAL"),
        ("dividends_paid_usd",           "REAL"),
        # Structural concentration risk (product/customer/supplier/geographic),
        # extracted via LLM from the same risk-factor/MD&A text already fetched.
        ("concentration_risk_score",   "INTEGER"),
        ("concentration_risk_summary", "TEXT"),
        ("concentration_risk_type",    "TEXT"),
    ]
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    added = 0
    for col_name, col_type in narrative_columns:
        try:
            cursor.execute(f"ALTER TABLE sec_financials ADD COLUMN {col_name} {col_type}")
            added += 1
        except sqlite3.OperationalError:
            pass  # Column already exists — safe to ignore

    daily_snapshot_columns = [
        ("risk_score",                 "INTEGER"),
        ("distress_risk_level",        "TEXT"),
        ("insider_sentiment_score",    "INTEGER"),
        ("drawdown_opportunity_score", "INTEGER"),
        # FINRA settlement-based short interest (via yfinance's shortPercentOfFloat)
        # -- inherently stale by ~2-4 weeks (published twice monthly), refreshed
        # opportunistically on every pipeline run since it costs no extra API call
        # (comes from the same stock.info dict already fetched for PE/dividend yield).
        ("short_percent_of_float",     "REAL"),
        # Growth-adjusted DCF fair value per share -- a 3-point sensitivity range
        # (low/base/high), not a single confident target -- see dcf_valuation.py
        # module docstring and README "DCF fair value" section for the full
        # methodology and why a range instead of one number.
        ("dcf_fair_value_low",         "REAL"),
        ("dcf_fair_value_base",        "REAL"),
        ("dcf_fair_value_high",        "REAL"),
        ("dcf_margin_of_safety_pct",   "REAL"),
    ]
    for col_name, col_type in daily_snapshot_columns:
        try:
            cursor.execute(f"ALTER TABLE daily_snapshot ADD COLUMN {col_name} {col_type}")
            added += 1
        except sqlite3.OperationalError:
            pass

    # ETF-specific migrations
    etf_tables = [
        ("sec_etf_reports", """
            CREATE TABLE IF NOT EXISTS sec_etf_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                cik TEXT NOT NULL,
                form_type TEXT NOT NULL,
                filing_date DATE NOT NULL,
                period_end_date DATE,
                accession_number TEXT,
                primary_doc_description TEXT,
                total_assets REAL,
                net_assets REAL,
                nav_per_share REAL,
                expense_ratio REAL,
                turnover_rate REAL,
                cash_percentage REAL,
                report_url TEXT,
                UNIQUE(ticker, form_type, filing_date, accession_number)
            )
        """),
        ("eight_k_events", """
            CREATE TABLE IF NOT EXISTS eight_k_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                cik TEXT,
                accession_number TEXT NOT NULL,
                filing_date DATE NOT NULL,
                item_codes TEXT,
                is_debt_related INTEGER DEFAULT 0,
                is_bankruptcy_related INTEGER DEFAULT 0,
                description TEXT,
                filing_url TEXT,
                UNIQUE(ticker, accession_number)
            )
        """),
        ("drawdown_events", """
            CREATE TABLE IF NOT EXISTS drawdown_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                peak_date DATE,
                peak_price REAL,
                bottom_date DATE,
                bottom_price REAL,
                drawdown_pct REAL,
                recovery_date DATE,
                recovery_price REAL,
                days_to_bottom INTEGER,
                days_underwater INTEGER,
                recovery_duration_days INTEGER,
                is_ongoing INTEGER DEFAULT 0,
                UNIQUE(ticker, peak_date, bottom_date)
            )
        """),
        ("drawdown_summary", """
            CREATE TABLE IF NOT EXISTS drawdown_summary (
                ticker TEXT PRIMARY KEY,
                completed_drawdowns INTEGER,
                drawdowns_over_10pct INTEGER,
                drawdowns_over_20pct INTEGER,
                drawdowns_over_30pct INTEGER,
                drawdowns_over_40pct INTEGER,
                avg_drawdown_pct REAL,
                median_drawdown_pct REAL,
                worst_drawdown_pct REAL,
                avg_recovery_days REAL,
                longest_recovery_days INTEGER,
                current_drawdown_pct REAL,
                years_of_history REAL,
                recovery_probability_json TEXT,
                updated_at DATETIME
            )
        """),
        ("distress_scores", """
            CREATE TABLE IF NOT EXISTS distress_scores (
                ticker TEXT PRIMARY KEY,
                altman_z REAL,
                piotroski_f INTEGER,
                distress_risk_score INTEGER,
                risk_level TEXT,
                confidence REAL,
                primary_drivers TEXT,
                warning_signals TEXT,
                data_completeness TEXT,
                updated_at DATETIME
            )
        """),
        ("etf_holdings", """
            CREATE TABLE IF NOT EXISTS etf_holdings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                filing_date DATE NOT NULL,
                holding_name TEXT NOT NULL,
                holding_ticker TEXT,
                cusip TEXT,
                isin TEXT,
                shares REAL,
                market_value REAL,
                weight_pct REAL,
                asset_category TEXT,
                country TEXT,
                currency TEXT,
                FOREIGN KEY(ticker) REFERENCES universe(ticker),
                UNIQUE(ticker, filing_date, holding_name, cusip, isin)
            )
        """),
    ]
    for table_name, create_sql in etf_tables:
        try:
            cursor.execute(create_sql)
            added += 1
        except sqlite3.OperationalError:
            pass

    try:
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_etf_holdings_dedup
            ON etf_holdings (ticker, filing_date, holding_name, cusip, isin)
        """)
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()
    if added:
        success(f"Migration added {added} table(s)/column(s)")
    else:
        success("Migration: schema already up to date")


def sync_universe_from_csv(db_path=DB_NAME, csv_path=UNIVERSE_CSV_PATH):
    """Reconciles the `universe` table against a weekly $50B-crossing scan
    (see universe_scanner.py): every ticker in the CSV is upserted as active,
    and every ticker that's currently active but NOT in the CSV gets marked
    inactive -- not deleted, so its price_history/sec_financials/etc. stay
    intact, it just stops being actively screened. This is what makes
    "crossing" membership two-way: a ticker that later drops below $50B
    naturally falls off the active universe on the next pipeline run.

    This CSV (checked into git, see .github/workflows/universe-scan.yml) is
    the sole source of the active universe -- there is no hardcoded fallback
    seed list. No-ops (keeps the existing universe as-is) if the CSV is
    missing or empty, but escalates to a WARNING rather than a quiet INFO
    when that leaves the `universe` table itself empty, since that's a
    genuinely broken setup (nothing for the rest of the pipeline to screen),
    not routine "no changes this week" behavior. Returns the number of
    tickers synced as active (0 if no-op).
    """
    def _warn_if_universe_empty(reason):
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM universe").fetchone()[0]
        conn.close()
        if count == 0:
            warning(
                f"{reason}, and the universe table is EMPTY -- the pipeline has nothing to screen this run. "
                f"Check that {csv_path} exists and is committed (it's the sole source of the active universe, "
                "no hardcoded fallback seed exists)."
            )
        else:
            info(f"{reason} -- keeping existing universe as-is ({count} tickers)")

    if not os.path.exists(csv_path):
        _warn_if_universe_empty(f"No universe scan CSV found at {csv_path}")
        return 0

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        _warn_if_universe_empty(f"{csv_path} is empty")
        return 0

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    csv_tickers = set()
    for row in rows:
        ticker = row["ticker"]
        csv_tickers.add(ticker)
        cursor.execute("""
            INSERT INTO universe (ticker, name, asset_type, sector, industry, market_cap, status, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, 'active', datetime('now'))
            ON CONFLICT(ticker) DO UPDATE SET
                name = excluded.name,
                asset_type = excluded.asset_type,
                sector = excluded.sector,
                industry = excluded.industry,
                market_cap = excluded.market_cap,
                status = 'active',
                last_updated = datetime('now')
        """, (
            ticker, row["name"], row["asset_type"], row["sector"], row["industry"],
            float(row["market_cap"]),
        ))

    cursor.execute("SELECT ticker FROM universe WHERE status = 'active'")
    currently_active = {r[0] for r in cursor.fetchall()}
    dropped = currently_active - csv_tickers
    for ticker in dropped:
        cursor.execute(
            "UPDATE universe SET status = 'inactive', last_updated = datetime('now') WHERE ticker = ?",
            (ticker,),
        )

    conn.commit()
    conn.close()
    success(
        f"Universe synced from {csv_path}: {len(csv_tickers)} active, "
        f"{len(dropped)} marked inactive (dropped below threshold)"
    )
    return len(csv_tickers)


if __name__ == "__main__":
    init_db()
    migrate_db()
