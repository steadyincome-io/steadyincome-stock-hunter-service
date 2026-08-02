import sqlite3
import os
from .logger import info, success

DB_NAME = "drawdown_analyzer.db"

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

DEFAULT_UNIVERSE = [
    # Technology / Semi
    ('AAPL', 'Apple Inc.', 'Stock', 'Technology', 'Consumer Electronics', 3450.0),
    ('MSFT', 'Microsoft Corporation', 'Stock', 'Technology', 'Software - Infrastructure', 3120.0),
    ('NVDA', 'NVIDIA Corporation', 'Stock', 'Technology', 'Semiconductors', 3150.0),
    ('AVGO', 'Broadcom Inc.', 'Stock', 'Technology', 'Semiconductors', 820.0),
    ('ORCL', 'Oracle Corporation', 'Stock', 'Technology', 'Software - Infrastructure', 380.0),
    ('CRM', 'Salesforce Inc.', 'Stock', 'Technology', 'Software - Application', 280.0),
    ('AMD', 'Advanced Micro Devices Inc.', 'Stock', 'Technology', 'Semiconductors', 250.0),
    ('CSCO', 'Cisco Systems Inc.', 'Stock', 'Technology', 'Communication Equipment', 210.0),
    ('QCOM', 'QUALCOMM Incorporated', 'Stock', 'Technology', 'Semiconductors', 220.0),
    ('IBM', 'International Business Machines', 'Stock', 'Technology', 'IT Services', 190.0),
    ('TXN', 'Texas Instruments Incorporated', 'Stock', 'Technology', 'Semiconductors', 180.0),
    ('AMAT', 'Applied Materials Inc.', 'Stock', 'Technology', 'Semiconductor Equipment', 180.0),
    ('INTU', 'Intuit Inc.', 'Stock', 'Technology', 'Software - Application', 180.0),
    ('NOW', 'ServiceNow Inc.', 'Stock', 'Technology', 'Software - Application', 170.0),
    ('ACN', 'Accenture plc', 'Stock', 'Technology', 'IT Services', 220.0),
    ('PANW', 'Palo Alto Networks Inc.', 'Stock', 'Technology', 'Software - Infrastructure', 110.0),
    ('ADBE', 'Adobe Inc.', 'Stock', 'Technology', 'Software - Infrastructure', 240.0),
    ('PLTR', 'Palantir Technologies Inc.', 'Stock', 'Technology', 'Software - Infrastructure', 105.0),
    ('ARM', 'Arm Holdings plc', 'Stock', 'Technology', 'Semiconductors', 140.0),
    ('ANET', 'Arista Networks Inc.', 'Stock', 'Technology', 'Computer Hardware', 110.0),
    ('KLAC', 'KLA Corporation', 'Stock', 'Technology', 'Semiconductor Equipment', 110.0),
    ('LRCX', 'Lam Research Corporation', 'Stock', 'Technology', 'Semiconductor Equipment', 115.0),
    ('SMCI', 'Super Micro Computer Inc.', 'Stock', 'Technology', 'Computer Hardware', 100.0),
    ('SNPS', 'Synopsys Inc.', 'Stock', 'Technology', 'Software - Infrastructure', 102.0),
    ('CDNS', 'Cadence Design Systems Inc.', 'Stock', 'Technology', 'Software - Infrastructure', 105.0),
    ('FI', 'Fiserv Inc.', 'Stock', 'Technology', 'Information Technology Services', 108.0),
    ('FIS', 'Fidelity National Information', 'Stock', 'Technology', 'Information Technology Services', 100.0),
    ('SHOP', 'Shopify Inc.', 'Stock', 'Technology', 'Software - Application', 110.0),
    ('MU', 'Micron Technology Inc.', 'Stock', 'Technology', 'Semiconductors', 120.0),
    ('INTC', 'Intel Corporation', 'Stock', 'Technology', 'Semiconductors', 100.0),
    ('ASML', 'ASML Holding N.V.', 'Stock', 'Technology', 'Semiconductor Equipment', 350.0),
    ('TSM', 'Taiwan Semiconductor Manufacturing', 'Stock', 'Technology', 'Semiconductors', 850.0),
    ('SAP', 'SAP SE', 'Stock', 'Technology', 'Software - Application', 240.0),
    ('SONY', 'Sony Group Corporation', 'Stock', 'Technology', 'Consumer Electronics', 110.0),
    ('ADI', 'Analog Devices Inc.', 'Stock', 'Technology', 'Semiconductors', 115.0),
    ('APH', 'Amphenol Corporation', 'Stock', 'Technology', 'Electronic Components', 105.0),

    # Communication Services
    ('GOOGL', 'Alphabet Inc. Class A', 'Stock', 'Communication Services', 'Internet Content & Information', 2250.0),
    ('GOOG', 'Alphabet Inc. Class C', 'Stock', 'Communication Services', 'Internet Content & Information', 2240.0),
    ('META', 'Meta Platforms Inc.', 'Stock', 'Communication Services', 'Internet Content & Information', 1350.0),
    ('NFLX', 'Netflix Inc.', 'Stock', 'Communication Services', 'Entertainment', 290.0),
    ('TMUS', 'T-Mobile US Inc.', 'Stock', 'Communication Services', 'Telecom Services', 210.0),
    ('T', 'AT&T Inc.', 'Stock', 'Communication Services', 'Telecom Services', 130.0),
    ('VZ', 'Verizon Communications Inc.', 'Stock', 'Communication Services', 'Telecom Services', 170.0),
    ('CMCSA', 'Comcast Corporation', 'Stock', 'Communication Services', 'Entertainment', 160.0),
    ('DIS', 'The Walt Disney Company', 'Stock', 'Communication Services', 'Entertainment', 190.0),
    ('CHTR', 'Charter Communications Inc.', 'Stock', 'Communication Services', 'Entertainment', 100.0),
    ('SPOT', 'Spotify Technology S.A.', 'Stock', 'Communication Services', 'Entertainment', 100.0),

    # Consumer Discretionary
    ('AMZN', 'Amazon.com Inc.', 'Stock', 'Consumer Cyclical', 'Internet Retail', 1980.0),
    ('TSLA', 'Tesla Inc.', 'Stock', 'Consumer Cyclical', 'Auto Manufacturers', 780.0),
    ('HD', 'The Home Depot Inc.', 'Stock', 'Consumer Cyclical', 'Home Improvement Retail', 360.0),
    ('NKE', 'NIKE Inc.', 'Stock', 'Consumer Cyclical', 'Footwear & Accessories', 120.0),
    ('MCD', 'McDonald\'s Corporation', 'Stock', 'Consumer Cyclical', 'Restaurants', 210.0),
    ('SBUX', 'Starbucks Corporation', 'Stock', 'Consumer Cyclical', 'Restaurants', 105.0),
    ('LOW', 'Lowe\'s Companies Inc.', 'Stock', 'Consumer Cyclical', 'Home Improvement Retail', 130.0),
    ('TJX', 'The TJX Companies Inc.', 'Stock', 'Consumer Cyclical', 'Apparel Retail', 125.0),
    ('BKNG', 'Booking Holdings Inc.', 'Stock', 'Consumer Cyclical', 'Travel Services', 140.0),
    ('ABNB', 'Airbnb Inc.', 'Stock', 'Consumer Cyclical', 'Travel Services', 100.0),
    ('MAR', 'Marriott International Inc.', 'Stock', 'Consumer Cyclical', 'Lodging', 100.0),
    ('ORLY', 'O\'Reilly Automotive Inc.', 'Stock', 'Consumer Cyclical', 'Specialty Retail', 102.0),
    ('AZO', 'AutoZone Inc.', 'Stock', 'Consumer Cyclical', 'Specialty Retail', 100.0),
    ('CMG', 'Chipotle Mexican Grill Inc.', 'Stock', 'Consumer Cyclical', 'Restaurants', 110.0),
    ('BABA', 'Alibaba Group Holding Ltd.', 'Stock', 'Consumer Cyclical', 'Internet Retail', 190.0),
    ('PDD', 'PDD Holdings Inc.', 'Stock', 'Consumer Cyclical', 'Internet Retail', 160.0),
    ('MELI', 'MercadoLibre Inc.', 'Stock', 'Consumer Cyclical', 'Internet Retail', 100.0),

    # Consumer Staples
    ('WMT', 'Walmart Inc.', 'Stock', 'Consumer Defensive', 'Discount Stores', 540.0),
    ('PG', 'Procter & Gamble Co.', 'Stock', 'Consumer Defensive', 'Household & Personal Products', 390.0),
    ('KO', 'The Coca-Cola Company', 'Stock', 'Consumer Defensive', 'Beverages - Non-Alcoholic', 280.0),
    ('PEP', 'PepsiCo Inc.', 'Stock', 'Consumer Defensive', 'Beverages - Non-Alcoholic', 230.0),
    ('COST', 'Costco Wholesale Corp.', 'Stock', 'Consumer Defensive', 'Discount Stores', 380.0),
    ('PM', 'Philip Morris International', 'Stock', 'Consumer Defensive', 'Tobacco', 180.0),
    ('MDLZ', 'Mondelez International Inc.', 'Stock', 'Consumer Defensive', 'Confectionery', 100.0),
    ('CL', 'Colgate-Palmolive Company', 'Stock', 'Consumer Defensive', 'Household & Personal Products', 100.0),
    ('MO', 'Altria Group Inc.', 'Stock', 'Consumer Defensive', 'Tobacco', 100.0),
    ('DEO', 'Diageo plc', 'Stock', 'Consumer Defensive', 'Beverages - Wineries & Distilleries', 110.0),
    ('KMB', 'Kimberly-Clark Corporation', 'Stock', 'Consumer Defensive', 'Household & Personal Products', 100.0),
    ('STZ', 'Constellation Brands Inc.', 'Stock', 'Consumer Defensive', 'Beverages - Wineries & Distilleries', 100.0),

    # Healthcare / Pharma
    ('LLY', 'Eli Lilly and Company', 'Stock', 'Healthcare', 'Drug Manufacturers - General', 810.0),
    ('NVO', 'Novo Nordisk A/S', 'Stock', 'Healthcare', 'Biotechnology', 580.0),
    ('JNJ', 'Johnson & Johnson', 'Stock', 'Healthcare', 'Drug Manufacturers - General', 380.0),
    ('UNH', 'UnitedHealth Group Inc.', 'Stock', 'Healthcare', 'Healthcare Plans', 510.0),
    ('ABBV', 'AbbVie Inc.', 'Stock', 'Healthcare', 'Drug Manufacturers - General', 320.0),
    ('MRK', 'Merck & Co. Inc.', 'Stock', 'Healthcare', 'Drug Manufacturers - General', 300.0),
    ('TMO', 'Thermo Fisher Scientific Inc.', 'Stock', 'Healthcare', 'Diagnostics & Research', 220.0),
    ('ABT', 'Abbott Laboratories', 'Stock', 'Healthcare', 'Medical Devices', 190.0),
    ('AMGN', 'Amgen Inc.', 'Stock', 'Healthcare', 'Drug Manufacturers - General', 160.0),
    ('DHR', 'Danaher Corporation', 'Stock', 'Healthcare', 'Diagnostics & Research', 180.0),
    ('PFE', 'Pfizer Inc.', 'Stock', 'Healthcare', 'Drug Manufacturers - General', 160.0),
    ('ISRG', 'Intuitive Surgical Inc.', 'Stock', 'Healthcare', 'Medical Instruments', 170.0),
    ('CVS', 'CVS Health Corporation', 'Stock', 'Healthcare', 'Healthcare Plans', 100.0),
    ('SYK', 'Stryker Corporation', 'Stock', 'Healthcare', 'Medical Devices', 130.0),
    ('REGN', 'Regeneron Pharmaceuticals', 'Stock', 'Healthcare', 'Biotechnology', 110.0),
    ('VRTX', 'Vertex Pharmaceuticals', 'Stock', 'Healthcare', 'Biotechnology', 120.0),
    ('BMY', 'Bristol-Myers Squibb', 'Stock', 'Healthcare', 'Drug Manufacturers - General', 110.0),
    ('MDT', 'Medtronic plc', 'Stock', 'Healthcare', 'Medical Devices', 110.0),
    ('ELV', 'Elevance Health Inc.', 'Stock', 'Healthcare', 'Healthcare Plans', 120.0),
    ('GILD', 'Gilead Sciences Inc.', 'Stock', 'Healthcare', 'Drug Manufacturers - General', 105.0),
    ('CI', 'The Cigna Group', 'Stock', 'Healthcare', 'Healthcare Plans', 105.0),
    ('ZTS', 'Zoetis Inc.', 'Stock', 'Healthcare', 'Drug Manufacturers - Specialty', 100.0),
    ('MCK', 'McKesson Corporation', 'Stock', 'Healthcare', 'Medical Distribution', 100.0),
    ('COR', 'Cencora Inc.', 'Stock', 'Healthcare', 'Medical Distribution', 100.0),

    # Financials / Banking / Insurance
    ('BRK.B', 'Berkshire Hathaway Inc.', 'Stock', 'Financial', 'Financial Data & Stock Exchanges', 980.0),
    ('JPM', 'JPMorgan Chase & Co.', 'Stock', 'Financial', 'Banks - Diversified', 590.0),
    ('V', 'Visa Inc.', 'Stock', 'Financial', 'Credit Services', 560.0),
    ('MA', 'Mastercard Incorporated', 'Stock', 'Financial', 'Credit Services', 430.0),
    ('BAC', 'Bank of America Corp.', 'Stock', 'Financial', 'Banks - Diversified', 310.0),
    ('WFC', 'Wells Fargo & Company', 'Stock', 'Financial', 'Banks - Diversified', 210.0),
    ('MS', 'Morgan Stanley', 'Stock', 'Financial', 'Capital Markets', 160.0),
    ('GS', 'The Goldman Sachs Group', 'Stock', 'Financial', 'Capital Markets', 160.0),
    ('C', 'Citigroup Inc.', 'Stock', 'Financial', 'Banks - Diversified', 120.0),
    ('BLK', 'BlackRock Inc.', 'Stock', 'Financial', 'Asset Management', 130.0),
    ('SCHW', 'The Charles Schwab Corp.', 'Stock', 'Financial', 'Capital Markets', 120.0),
    ('AXP', 'American Express Company', 'Stock', 'Financial', 'Credit Services', 180.0),
    ('SPGI', 'S&P Global Inc.', 'Stock', 'Financial', 'Financial Data & Stock Exchanges', 150.0),
    ('CME', 'CME Group Inc.', 'Stock', 'Financial', 'Financial Data & Stock Exchanges', 100.0),
    ('AON', 'Aon plc', 'Stock', 'Financial', 'Insurance Brokers', 100.0),
    ('MMC', 'Marsh & McLennan Companies', 'Stock', 'Financial', 'Insurance Brokers', 105.0),
    ('PGR', 'The Progressive Corp.', 'Stock', 'Financial', 'Insurance - Property & Casualty', 130.0),
    ('CB', 'Chubb Limited', 'Stock', 'Financial', 'Insurance - Property & Casualty', 110.0),
    ('TRV', 'The Travelers Companies', 'Stock', 'Financial', 'Insurance - Property & Casualty', 100.0),
    ('USB', 'U.S. Bancorp', 'Stock', 'Financial', 'Banks - Regional', 100.0),
    ('MET', 'MetLife Inc.', 'Stock', 'Financial', 'Insurance - Life', 100.0),
    ('PNC', 'PNC Financial Services Group', 'Stock', 'Financial', 'Banks - Regional', 100.0),
    ('BK', 'The Bank of New York Mellon', 'Stock', 'Financial', 'Asset Management', 100.0),
    ('AIG', 'American International Group', 'Stock', 'Financial', 'Insurance - Diversified', 100.0),
    ('ALL', 'The Allstate Corporation', 'Stock', 'Financial', 'Insurance - Property & Casualty', 100.0),
    ('COF', 'Capital One Financial Corp.', 'Stock', 'Financial', 'Credit Services', 100.0),

    # Industrials & Aerospace
    ('GE', 'GE Aerospace', 'Stock', 'Industrials', 'Aerospace & Defense', 190.0),
    ('CAT', 'Caterpillar Inc.', 'Stock', 'Industrials', 'Farm & Heavy Construction Machinery', 170.0),
    ('DE', 'Deere & Company', 'Stock', 'Industrials', 'Farm & Heavy Construction Machinery', 110.0),
    ('LMT', 'Lockheed Martin Corp.', 'Stock', 'Industrials', 'Aerospace & Defense', 110.0),
    ('RTX', 'RTX Corporation', 'Stock', 'Industrials', 'Aerospace & Defense', 150.0),
    ('HON', 'Honeywell International', 'Stock', 'Industrials', 'Conglomerates', 130.0),
    ('UNP', 'Union Pacific Corporation', 'Stock', 'Industrials', 'Railroads', 150.0),
    ('UPS', 'United Parcel Service Inc.', 'Stock', 'Industrials', 'Integrated Freight & Logistics', 110.0),
    ('BA', 'The Boeing Company', 'Stock', 'Industrials', 'Aerospace & Defense', 120.0),
    ('ETN', 'Eaton Corporation plc', 'Stock', 'Industrials', 'Specialty Industrial Machinery', 130.0),
    ('ITW', 'Illinois Tool Works Inc.', 'Stock', 'Industrials', 'Specialty Industrial Machinery', 100.0),
    ('PH', 'Parker-Hannifin Corp.', 'Stock', 'Industrials', 'Specialty Industrial Machinery', 100.0),
    ('GD', 'General Dynamics Corp.', 'Stock', 'Industrials', 'Aerospace & Defense', 100.0),
    ('NOC', 'Northrop Grumman Corp.', 'Stock', 'Industrials', 'Aerospace & Defense', 100.0),
    ('WM', 'Waste Management Inc.', 'Stock', 'Industrials', 'Waste Management', 100.0),
    ('FDX', 'FedEx Corporation', 'Stock', 'Industrials', 'Integrated Freight & Logistics', 100.0),
    ('RSG', 'Republic Services Inc.', 'Stock', 'Industrials', 'Waste Management', 100.0),

    # Energy & Materials
    ('XOM', 'Exxon Mobil Corporation', 'Stock', 'Energy', 'Oil & Gas Integrated', 470.0),
    ('CVX', 'Chevron Corporation', 'Stock', 'Energy', 'Oil & Gas Integrated', 280.0),
    ('COP', 'ConocoPhillips', 'Stock', 'Energy', 'Oil & Gas E&P', 130.0),
    ('SLB', 'Schlumberger Limited', 'Stock', 'Energy', 'Oil & Gas Equipment & Services', 100.0),
    ('EOG', 'EOG Resources Inc.', 'Stock', 'Energy', 'Oil & Gas E&P', 100.0),
    ('MPC', 'Marathon Petroleum Corp.', 'Stock', 'Energy', 'Oil & Gas Refining & Marketing', 100.0),
    ('PSX', 'Phillips 66', 'Stock', 'Energy', 'Oil & Gas Refining & Marketing', 100.0),
    ('VLO', 'Valero Energy Corp.', 'Stock', 'Energy', 'Oil & Gas Refining & Marketing', 100.0),
    ('WMB', 'The Williams Companies Inc.', 'Stock', 'Energy', 'Oil & Gas Midstream', 100.0),
    ('BKR', 'Baker Hughes Company', 'Stock', 'Energy', 'Oil & Gas Equipment & Services', 100.0),
    ('OXY', 'Occidental Petroleum Corp.', 'Stock', 'Energy', 'Oil & Gas E&P', 100.0),
    ('LIN', 'Linde plc', 'Stock', 'Basic Materials', 'Specialty Chemicals', 210.0),
    ('FCX', 'Freeport-McMoRan Inc.', 'Stock', 'Basic Materials', 'Copper', 100.0),
    ('NEM', 'Newmont Corporation', 'Stock', 'Basic Materials', 'Gold', 100.0),
    ('APD', 'Air Products and Chemicals', 'Stock', 'Basic Materials', 'Specialty Chemicals', 100.0),

    # Utilities & Real Estate
    ('NEE', 'NextEra Energy Inc.', 'Stock', 'Utilities', 'Utilities - Renewable', 150.0),
    ('SO', 'The Southern Company', 'Stock', 'Utilities', 'Utilities - Regulated Electric', 100.0),
    ('DUK', 'Duke Energy Corporation', 'Stock', 'Utilities', 'Utilities - Regulated Electric', 100.0),
    ('CEG', 'Constellation Energy Corp.', 'Stock', 'Utilities', 'Utilities - Renewable', 100.0),
    ('AMT', 'American Tower Corp.', 'Stock', 'Real Estate', 'REIT - Specialty', 110.0),
    ('PLD', 'Prologis Inc.', 'Stock', 'Real Estate', 'REIT - Industrial', 110.0),
    ('EQIX', 'Equinix Inc.', 'Stock', 'Real Estate', 'REIT - Specialty', 100.0),

    # Mega ETFs (> $100B AUM)
    ('SPY', 'SPDR S&P 500 ETF Trust', 'ETF', 'Broad Market', 'US Large Cap Blend', 560.0),
    ('IVV', 'iShares Core S&P 500 ETF', 'ETF', 'Broad Market', 'US Large Cap Blend', 490.0),
    ('VOO', 'Vanguard S&P 500 ETF', 'ETF', 'Broad Market', 'US Large Cap Blend', 510.0),
    ('VTI', 'Vanguard Total Stock Market ETF', 'ETF', 'Total Market', 'US Total Stock Market', 380.0),
    ('QQQ', 'Invesco QQQ Trust', 'ETF', 'Tech Heavy', 'US Large Cap Growth', 280.0),
    ('VUG', 'Vanguard Growth ETF', 'ETF', 'Large Cap Growth', 'US Large Cap Growth', 130.0),
    ('VTV', 'Vanguard Value ETF', 'ETF', 'Large Cap Value', 'US Large Cap Value', 120.0),
    ('SCHD', 'Schwab U.S. Dividend Equity ETF', 'ETF', 'Dividend', 'US High Dividend Yield', 104.0),
    ('BND', 'Vanguard Total Bond Market ETF', 'ETF', 'Fixed Income', 'US Broad Bond', 110.0),
    ('AGG', 'iShares Core U.S. Aggregate Bond ETF', 'ETF', 'Fixed Income', 'US Broad Bond', 105.0),
    ('VXUS', 'Vanguard Total International Stock ETF', 'ETF', 'International', 'Global Ex-US Equity', 400.0),
    ('VEA', 'Vanguard FTSE Developed Markets ETF', 'ETF', 'International', 'Developed Markets Ex-US', 130.0),
    ('VWO', 'Vanguard FTSE Emerging Markets ETF', 'ETF', 'International', 'Emerging Markets Equity', 110.0),
    ('IWM', 'iShares Russell 2000 ETF', 'ETF', 'Small Cap', 'US Small Cap Blend', 100.0),
    ('XLK', 'Technology Select Sector SPDR', 'ETF', 'Sector', 'US Technology Sector', 110.0),
    ('XLE', 'Energy Select Sector SPDR', 'ETF', 'Sector', 'US Energy Sector', 100.0),
    ('XLF', 'Financial Select Sector SPDR', 'ETF', 'Sector', 'US Financial Sector', 100.0),
    ('XLV', 'Health Care Select Sector SPDR', 'ETF', 'Sector', 'US Health Care Sector', 100.0),
    ('XLY', 'Consumer Discretionary Select Sector SPDR', 'ETF', 'Sector', 'US Consumer Discretionary', 100.0),
    ('XLP', 'Consumer Staples Select Sector SPDR', 'ETF', 'Sector', 'US Consumer Staples', 100.0),
    ('DIA', 'SPDR Dow Jones Industrial Average ETF', 'ETF', 'Broad Market', 'US Large Cap Value', 100.0)
]

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

    # Populate default universe if empty
    cursor.execute("SELECT COUNT(*) FROM universe")
    count = cursor.fetchone()[0]
    if count == 0:
        info("Seeding default >$100B stock and mega ETF universe")
        cursor.executemany("""
            INSERT INTO universe (ticker, name, asset_type, sector, industry, market_cap, status, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, 'active', datetime('now'))
        """, DEFAULT_UNIVERSE)
        conn.commit()
        success(f"Inserted {len(DEFAULT_UNIVERSE)} universe tickers")
        
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


if __name__ == "__main__":
    init_db()
    migrate_db()
