# SEC-Driven Equity Intelligence Platform

## 1. Project Overview

This project is a large-cap stock and ETF research platform built around official SEC filings, structured financial data, deterministic analytics, and LLM-assisted narrative analysis.

The initial investment universe contains approximately **186 stocks and ETFs** with market capitalization or assets under management above approximately **$100 billion**.

The platform processes every active security, collects the latest available SEC and market data, stores normalized results in SQLite, and then performs higher-level analysis such as:

- Financial quality analysis
- Fundamental trend analysis
- Solvency and financial-distress analysis
- Filing-derived risk analysis
- Insider activity analysis
- Debt and liquidity analysis
- Dividend quality analysis
- Drawdown and recovery analysis
- Valuation analysis
- Composite risk, quality, and investment scores
- LLM-generated research summaries

The core design principle is to separate:

1. Raw source data
2. Normalized structured data
3. Deterministic analytics
4. LLM-generated narrative insights

This separation keeps the platform auditable, reproducible, and maintainable.

---

## 2. Investment Universe

The starting universe contains approximately **186 securities**.

The universe may include:

- U.S.-listed public companies
- Foreign issuers with U.S. listings or ADRs
- Large ETFs
- Stocks with market capitalization above approximately $100 billion
- ETFs with assets under management above approximately $100 billion

Example configuration:

```yaml
universe:
  min_stock_market_cap: 100000000000
  min_etf_aum: 100000000000
  include_stocks: true
  include_etfs: true
  active_only: true
```

Each security should be stored in a central `universe` table.

Suggested fields:

| Field | Description |
|---|---|
| ticker | Trading symbol |
| company_name | Company or fund name |
| asset_type | Stock, ETF, ADR, REIT, etc. |
| exchange | Primary exchange |
| cik | SEC Central Index Key |
| sector | Sector |
| industry | Industry |
| country | Country of domicile |
| market_cap | Latest market capitalization |
| aum | ETF assets under management |
| active | Whether the security is currently in the universe |
| first_seen_at | Date first added |
| last_verified_at | Last universe verification date |

Securities that fall below the threshold should normally be marked inactive instead of deleted so historical results remain available.

---

## 3. High-Level Architecture

```text
                    ┌──────────────────────────┐
                    │   Security Universe      │
                    │  ~186 Stocks and ETFs    │
                    └─────────────┬────────────┘
                                  │
                                  ▼
                    ┌──────────────────────────┐
                    │ Pipeline Orchestrator    │
                    │ Scheduling / Retries     │
                    │ Parallel Processing      │
                    └─────────────┬────────────┘
                                  │
       ┌──────────────────────────┼──────────────────────────┐
       │                          │                          │
       ▼                          ▼                          ▼
┌──────────────┐          ┌──────────────┐          ┌──────────────┐
│ SEC Filings  │          │ Market Data  │          │ Reference    │
│ 10-K / 10-Q  │          │ Prices       │          │ Data         │
│ 8-K / Form 4 │          │ Dividends    │          │ Ticker / CIK │
└──────┬───────┘          └──────┬───────┘          └──────┬───────┘
       │                          │                          │
       ▼                          ▼                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                 Raw Data and Document Cache                     │
│ Filing metadata, HTML, XBRL, XML, JSON, prices, dividends       │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Parsing and Normalization                    │
│ Narrative extraction, XBRL mapping, Form 4 parsing, validation  │
└───────────────────────────────┬─────────────────────────────────┘
                                │
             ┌──────────────────┴──────────────────┐
             │                                     │
             ▼                                     ▼
┌──────────────────────────┐          ┌──────────────────────────┐
│ Deterministic Analytics  │          │ LLM Analysis Layer       │
│ Ratios, trends, scores   │          │ Summaries and synthesis  │
│ Drawdowns, solvency      │          │ Risk-factor comparison   │
└─────────────┬────────────┘          └─────────────┬────────────┘
              │                                     │
              └──────────────────┬──────────────────┘
                                 ▼
                    ┌──────────────────────────┐
                    │ SQLite Research DB       │
                    │ Raw + normalized +       │
                    │ derived analytics        │
                    └─────────────┬────────────┘
                                  │
                                  ▼
                    ┌──────────────────────────┐
                    │ Reports / Dashboard / API│
                    │ CLI / Excel / HTML       │
                    └──────────────────────────┘
```

---

## 4. Pipeline Workflow

For every active stock or ETF, the pipeline performs the following sequence.

### 4.1 Resolve Security Identity

- Normalize ticker
- Resolve SEC CIK
- Verify company name
- Determine whether the asset is an operating company or ETF
- Detect unsupported filing structures
- Store issuer metadata

ETFs may not have the same filing and fundamental structure as operating companies. The pipeline should use asset-type-specific logic instead of assuming every ticker has conventional 10-K and 10-Q financial statements.

### 4.2 Retrieve Latest 10-K

For each operating company:

- Find the most recent 10-K
- Store filing metadata
- Download or cache the filing document
- Extract structured XBRL data
- Extract selected narrative sections
- Send approved narrative sections to the LLM
- Store the LLM summary and source references

Target narrative sections include:

- Risk Factors
- Management's Discussion and Analysis
- Legal Proceedings
- Liquidity and Capital Resources
- Share Repurchases and Buybacks
- Debt and Financing Discussion
- Commitments and Contingencies
- Critical Accounting Estimates
- Business Overview
- Cybersecurity disclosures, when available
- Material subsequent events

The latest 10-K serves as the annual baseline for company quality, solvency, strategic risks, capital allocation, and long-term trends.

### 4.3 Retrieve Recent 10-Q Filings

For each company, retrieve all 10-Q filings submitted within the prior **365 days**.

Normally this produces approximately three or four quarterly filings.

For each 10-Q:

- Store filing metadata
- Download or cache the filing
- Parse XBRL facts
- Extract quarter-specific financial values
- Extract selected narrative sections
- Compare current-quarter language with prior filings
- Store normalized quarterly metrics

The quarterly filings provide the most recent trend information for:

- Revenue
- Gross profit
- Operating income
- Net income
- Earnings per share
- Cash and cash equivalents
- Accounts receivable
- Inventory
- Current assets and liabilities
- Long-term debt
- Total debt
- Stockholders' equity
- Operating cash flow
- Capital expenditures
- Free cash flow
- Share repurchases
- Dividends
- Segment results
- Liquidity and financing activity

### 4.4 Retrieve Recent 8-K Filings

The platform should retrieve recent 8-K filings, with particular focus on material events related to:

- New debt issuance
- Credit facilities
- Debt refinancing
- Defaults or covenant issues
- Material acquisitions
- Divestitures
- Earnings announcements
- Executive changes
- Auditor changes
- Restructuring
- Impairments
- Bankruptcy-related events
- Material legal developments

Important 8-K item codes may include:

| Item | Meaning |
|---|---|
| 1.01 | Entry into a material definitive agreement |
| 1.02 | Termination of a material definitive agreement |
| 1.03 | Bankruptcy or receivership |
| 2.01 | Completion of acquisition or disposition |
| 2.02 | Results of operations and financial condition |
| 2.03 | Creation of a direct financial obligation |
| 2.04 | Triggering events that accelerate obligations |
| 2.05 | Costs associated with exit or disposal activities |
| 2.06 | Material impairments |
| 3.01 | Delisting or listing-rule issues |
| 4.01 | Changes in registrant's certifying accountant |
| 5.02 | Executive or director changes |
| 7.01 | Regulation FD disclosure |
| 8.01 | Other material events |

The system should preserve the filing URL and extracted item numbers so every derived conclusion remains traceable.

### 4.5 Retrieve Form 4 Insider Transactions

For recent Form 4 filings:

- Discover filings using SEC submissions data
- Download the actual Form 4 XML
- Parse real transaction-level details
- Store individual transactions
- Aggregate insider sentiment by ticker

Suggested fields:

| Field | Description |
|---|---|
| ticker | Security ticker |
| cik | Issuer CIK |
| accession_number | SEC accession |
| filing_date | Form 4 filing date |
| transaction_date | Actual trade or award date |
| insider_name | Reporting owner |
| officer_title | Officer title |
| is_director | Director flag |
| is_officer | Officer flag |
| is_ten_percent_owner | 10% owner flag |
| security_title | Security type |
| transaction_code | P, S, A, M, F, G, etc. |
| acquired_disposed | Acquired or disposed |
| shares | Shares transacted |
| price_per_share | Reported price |
| total_value | Shares multiplied by price |
| direct_or_indirect | Ownership type |
| shares_owned_after | Post-transaction ownership |
| filing_url | Official SEC source |

Transaction-code classification should distinguish open-market activity from compensation and administrative transactions.

- `P`: Open-market or private purchase
- `S`: Open-market or private sale
- `A`: Grant or award
- `M`: Exercise or conversion of derivative security
- `F`: Payment of exercise price or tax liability
- `G`: Gift

The insider sentiment score should give more weight to open-market purchases and sales than to grants, gifts, or tax-withholding transactions.

---

## 5. SEC Data Sources

Primary official SEC endpoints may include:

```text
https://www.sec.gov/files/company_tickers.json
https://data.sec.gov/submissions/CIK##########.json
https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json
https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/...
```

All SEC requests should:

- Use a valid identifying `User-Agent`
- Include a real contact email
- Reuse a persistent HTTP session
- Respect SEC rate limits
- Use retries and exponential backoff
- Cache downloaded responses
- Avoid repeatedly downloading unchanged filings
- Continue gracefully when one issuer or endpoint fails

Example header:

```python
SEC_HEADERS = {
    "User-Agent": "Equity Intelligence Platform/1.0 (your-email@example.com)",
    "Accept-Encoding": "gzip, deflate",
}
```

---

## 6. Raw Document Storage

Raw source documents should be cached outside normalized database tables.

Suggested structure:

```text
data/
├── sec/
│   ├── cik_map/
│   ├── submissions/
│   ├── companyfacts/
│   ├── filings/
│   │   ├── NVDA/
│   │   ├── WMT/
│   │   └── ...
│   └── form4/
├── market/
│   ├── prices/
│   └── dividends/
└── llm/
    ├── requests/
    └── responses/
```

Each stored document should retain:

- Ticker
- CIK
- Accession number
- Filing type
- Filing date
- Period end
- Source URL
- Download timestamp
- SHA-256 hash
- Parser version
- Processing status

---

## 7. XBRL Fundamental Data

The fundamental engine should normalize SEC facts into a consistent internal schema.

### 7.1 Income Statement Metrics

- Revenue
- Cost of revenue
- Gross profit
- Research and development
- Selling, general, and administrative expense
- Operating expenses
- Operating income
- Interest expense
- Pretax income
- Income tax expense
- Net income
- Basic EPS
- Diluted EPS
- Weighted-average shares

### 7.2 Balance Sheet Metrics

- Cash and cash equivalents
- Short-term investments
- Accounts receivable
- Inventory
- Current assets
- Property, plant, and equipment
- Goodwill
- Intangible assets
- Total assets
- Accounts payable
- Current liabilities
- Short-term debt
- Long-term debt
- Total liabilities
- Stockholders' equity

### 7.3 Cash Flow Metrics

- Operating cash flow
- Capital expenditures
- Free cash flow
- Acquisitions
- Share repurchases
- Dividends paid
- Debt issued
- Debt repaid
- Financing cash flow
- Investing cash flow

### 7.4 Normalization Challenges

The SEC taxonomy is not perfectly uniform across companies.

The platform should handle:

- Multiple possible US-GAAP concepts for the same business metric
- Company-specific extension concepts
- Fiscal years that do not align with calendar years
- Year-to-date versus standalone-quarter values
- Restated facts
- Duplicate facts
- Multiple units
- Amended filings
- Segment-level versus consolidated data
- Instant versus duration facts

Every normalized value should retain provenance.

Suggested provenance fields:

- SEC concept
- Unit
- Period start
- Period end
- Fiscal year
- Fiscal period
- Filed date
- Form
- Accession number
- Frame
- Extraction method
- Confidence score

---

## 8. Narrative Extraction

The filing parser should extract sections using filing structure, anchors, headings, and semantic matching.

Target sections:

- Item 1: Business
- Item 1A: Risk Factors
- Item 1B: Unresolved Staff Comments
- Item 1C: Cybersecurity
- Item 3: Legal Proceedings
- Item 7: MD&A
- Item 7A: Market Risk
- Item 8: Financial Statements and Notes
- Liquidity and Capital Resources
- Critical Accounting Policies and Estimates
- Debt and Financing
- Share Repurchase Activity
- Commitments and Contingencies

Each extracted section should store:

| Field | Description |
|---|---|
| ticker | Security |
| accession_number | Filing identifier |
| filing_type | 10-K or 10-Q |
| section_name | Standardized section |
| source_heading | Original heading |
| raw_text | Extracted text |
| cleaned_text | Normalized text |
| character_count | Section size |
| extraction_method | Anchor, heading, semantic fallback |
| extraction_confidence | Confidence score |
| parser_version | Parser version |

---

## 9. LLM Summarization Layer

The LLM should summarize narrative content, not calculate financial metrics.

### 9.1 LLM Responsibilities

The LLM may:

- Summarize risk factors
- Identify newly added risks
- Compare current and prior risk language
- Summarize MD&A
- Explain changes in revenue, margins, costs, or cash flow
- Summarize liquidity and capital resources
- Identify legal and regulatory developments
- Summarize buyback strategy
- Extract management's stated priorities
- Highlight debt-related concerns
- Produce an investor-readable filing brief

### 9.2 Deterministic Responsibilities

Python should calculate:

- Ratios
- Growth rates
- Margins
- Drawdowns
- Recovery times
- Debt trends
- Altman Z-score
- Piotroski F-score
- Beneish M-score
- Dividend metrics
- Valuation percentiles
- Composite scores

The LLM should not be trusted to perform arithmetic that can be calculated deterministically.

### 9.3 Suggested LLM Output Schema

```json
{
  "executive_summary": "",
  "material_changes": [],
  "risk_factors": {
    "new": [],
    "worsening": [],
    "improving": [],
    "unchanged": []
  },
  "mda_summary": "",
  "liquidity_summary": "",
  "debt_summary": "",
  "legal_summary": "",
  "buyback_summary": "",
  "management_outlook": "",
  "red_flags": [],
  "positive_signals": [],
  "source_citations": []
}
```

### 9.4 LLM Auditability

Store:

- Model name
- Model version
- Prompt version
- Temperature
- Token usage
- Input document references
- Timestamp
- Raw response
- Parsed response
- Validation status
- Retry count

---

## 10. Market and Price Analytics

In addition to SEC data, the platform may retrieve:

- Daily adjusted prices
- Daily unadjusted prices
- Dividends
- Stock splits
- Market capitalization
- ETF assets under management
- Shares outstanding
- Trading volume

Price data supports:

- Historical drawdowns
- Recovery analysis
- Current drawdown
- Volatility
- Total return
- Dividend-adjusted return
- Buy-the-dip analysis
- Valuation calculations

---

## 11. Drawdown and Recovery Analytics

For every security, calculate every meaningful historical drawdown.

Suggested minimum threshold:

```yaml
drawdowns:
  minimum_drawdown_pct: 5
```

For each drawdown event store:

- Peak date
- Peak price
- Bottom date
- Bottom price
- Drawdown percentage
- Recovery date
- Recovery price
- Days to bottom
- Days underwater
- Recovery duration
- Dividend yield at bottom
- Total return after bottom
- Additional decline after threshold entry

Summary metrics:

- Number of completed drawdowns
- Drawdowns above 10%, 20%, 30%, and 40%
- Average drawdown
- Median drawdown
- Worst drawdown
- Average recovery time
- Longest recovery time
- Current drawdown
- Total years underwater
- Recovery probability within 6 months, 1 year, 2 years, 3 years, and 5 years

---

## 12. Fundamental Analytics

Once annual and quarterly data are normalized, calculate:

### Growth

- Revenue CAGR
- EPS CAGR
- Net income CAGR
- Free cash flow CAGR
- Dividend CAGR
- Book value CAGR
- Operating cash flow CAGR

### Profitability

- Gross margin
- Operating margin
- Net margin
- Free cash flow margin
- Return on assets
- Return on equity
- Return on invested capital

### Liquidity

- Current ratio
- Quick ratio
- Cash ratio
- Operating cash flow to current liabilities

### Leverage and Solvency

- Debt-to-equity
- Debt-to-assets
- Net debt
- Net debt to EBITDA
- Interest coverage
- Fixed-charge coverage
- Debt maturity concentration
- Cash to debt
- Free cash flow to debt

### Efficiency

- Asset turnover
- Inventory turnover
- Receivables turnover
- Cash conversion cycle

### Shareholder Allocation

- Buybacks
- Net share issuance
- Dividend payout ratio
- Free cash flow payout ratio
- Debt-funded buybacks
- Acquisition spending

---

## 13. Bankruptcy and Financial Distress Analysis

The system should not claim with certainty that a company will or will not go bankrupt.

Instead, estimate financial-distress risk using multiple indicators.

Potential models:

- Altman Z-score
- Ohlson O-score
- Zmijewski score
- Piotroski F-score
- Beneish M-score
- Interest coverage trend
- Liquidity trend
- Debt maturity schedule
- Free cash flow consistency
- Operating loss persistence
- Auditor going-concern language
- Covenant breach disclosures
- 8-K Item 1.03 bankruptcy events
- 8-K Item 2.04 obligation-trigger events
- Credit facility reductions
- Equity dilution dependence

Suggested output:

```json
{
  "distress_risk_score": 22,
  "risk_level": "LOW",
  "confidence": 0.84,
  "primary_drivers": [
    "Strong interest coverage",
    "Positive free cash flow",
    "Low near-term debt concentration"
  ],
  "warning_signals": [],
  "model_results": {
    "altman_z": 4.1,
    "ohlson_o": -3.2,
    "piotroski_f": 7
  }
}
```

Use probabilistic wording such as:

- Low financial-distress risk
- Elevated financial-distress risk
- Material solvency concerns
- Insufficient data

Avoid absolute predictions.

---

## 14. Risk Scoring Framework

A composite risk score may combine:

| Component | Example Weight |
|---|---:|
| Balance-sheet risk | 20% |
| Liquidity risk | 15% |
| Earnings stability | 10% |
| Cash-flow risk | 15% |
| Filing risk factors | 15% |
| Legal and regulatory risk | 10% |
| Drawdown severity | 10% |
| Insider selling | 5% |

Example interpretation:

| Score | Risk Level |
|---:|---|
| 0-20 | Very low |
| 21-40 | Low |
| 41-60 | Moderate |
| 61-80 | High |
| 81-100 | Very high |

The score should include component-level explanations, not just a final number.

---

## 15. Quality Scoring Framework

A quality score may combine:

| Component | Example Weight |
|---|---:|
| Revenue growth and stability | 15% |
| EPS and net-income quality | 15% |
| Free cash flow quality | 20% |
| Profitability and margins | 15% |
| Balance-sheet strength | 15% |
| ROIC and capital efficiency | 10% |
| Dividend and buyback quality | 10% |

Example output:

```json
{
  "quality_score": 87,
  "quality_level": "HIGH",
  "strengths": [
    "Strong free cash flow conversion",
    "High return on invested capital",
    "Conservative leverage"
  ],
  "weaknesses": [
    "Slowing revenue growth"
  ]
}
```

---

## 16. Valuation Analytics

Potential metrics:

- Price-to-earnings
- Forward P/E
- Price-to-sales
- Price-to-book
- Enterprise value to EBITDA
- Enterprise value to sales
- Price to free cash flow
- Free cash flow yield
- Earnings yield
- Dividend yield
- Historical yield percentile
- Historical valuation percentile
- Sector-relative valuation
- Growth-adjusted valuation

The valuation layer should distinguish operating companies from ETFs.

ETF valuation may instead focus on:

- AUM
- Expense ratio
- Premium or discount
- Tracking difference
- Holdings concentration
- Weighted portfolio valuation
- Distribution yield
- Liquidity and bid-ask spread

---

## 17. Investment Score

A composite investment score may combine:

| Component | Example Weight |
|---|---:|
| Fundamental quality | 30% |
| Valuation | 20% |
| Financial risk | 15% |
| Filing-derived risk | 10% |
| Drawdown opportunity | 10% |
| Dividend quality | 10% |
| Insider activity | 5% |

Example output:

```json
{
  "investment_score": 82,
  "rating": "ATTRACTIVE",
  "quality_score": 88,
  "valuation_score": 74,
  "risk_score": 24,
  "drawdown_opportunity_score": 79,
  "dividend_score": 76,
  "insider_score": 55
}
```

Scores should be accompanied by transparent formulas, component values, and source dates.

---

## 18. SQLite Database Design

Use normalized tables instead of one large table.

Suggested core tables:

```text
universe
issuers
filings
filing_documents
filing_sections
llm_summaries
xbrl_facts_raw
fundamentals_annual
fundamentals_quarterly
financial_ratios
eight_k_events
insider_filings
insider_transactions
prices_daily
dividends
drawdown_events
drawdown_summary
valuation_snapshots
risk_scores
quality_scores
investment_scores
pipeline_runs
pipeline_errors
```

### Example: `filings`

```sql
CREATE TABLE filings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    cik TEXT,
    accession_number TEXT NOT NULL UNIQUE,
    form_type TEXT NOT NULL,
    filing_date TEXT,
    report_date TEXT,
    fiscal_year INTEGER,
    fiscal_period TEXT,
    primary_document TEXT,
    filing_url TEXT,
    source TEXT DEFAULT 'SEC',
    downloaded_at TEXT,
    parsed_at TEXT,
    parser_version TEXT,
    status TEXT,
    error_message TEXT
);
```

### Example: `fundamentals_quarterly`

```sql
CREATE TABLE fundamentals_quarterly (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    accession_number TEXT,
    fiscal_year INTEGER,
    fiscal_period TEXT,
    period_start TEXT,
    period_end TEXT,
    revenue REAL,
    gross_profit REAL,
    operating_income REAL,
    net_income REAL,
    diluted_eps REAL,
    operating_cash_flow REAL,
    capex REAL,
    free_cash_flow REAL,
    cash REAL,
    total_assets REAL,
    current_assets REAL,
    current_liabilities REAL,
    short_term_debt REAL,
    long_term_debt REAL,
    total_debt REAL,
    stockholders_equity REAL,
    shares_outstanding REAL,
    filed_date TEXT,
    extraction_confidence REAL,
    UNIQUE(ticker, accession_number, fiscal_period)
);
```

### Example: `filing_sections`

```sql
CREATE TABLE filing_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    accession_number TEXT NOT NULL,
    ticker TEXT NOT NULL,
    section_name TEXT NOT NULL,
    source_heading TEXT,
    raw_text TEXT,
    cleaned_text TEXT,
    extraction_method TEXT,
    extraction_confidence REAL,
    parser_version TEXT,
    created_at TEXT,
    UNIQUE(accession_number, section_name)
);
```

### Example: `llm_summaries`

```sql
CREATE TABLE llm_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    accession_number TEXT NOT NULL,
    ticker TEXT NOT NULL,
    summary_type TEXT NOT NULL,
    model_name TEXT,
    model_version TEXT,
    prompt_version TEXT,
    input_hash TEXT,
    summary_json TEXT,
    raw_response TEXT,
    token_input INTEGER,
    token_output INTEGER,
    validation_status TEXT,
    created_at TEXT,
    UNIQUE(accession_number, summary_type, model_name, prompt_version)
);
```

---

## 19. Pipeline Orchestration

Suggested execution flow:

```text
1. Start pipeline run
2. Refresh active universe if required
3. Load cached ticker-to-CIK map
4. Create task per ticker
5. For each ticker:
   a. Validate issuer identity
   b. Fetch submissions metadata
   c. Find latest 10-K
   d. Find 10-Q filings from prior 365 days
   e. Find relevant 8-K filings
   f. Find recent Form 4 filings
   g. Download only missing documents
   h. Parse narrative sections
   i. Parse XBRL facts
   j. Normalize annual and quarterly fundamentals
   k. Parse insider transactions
   l. Run LLM summarization when input changed
   m. Calculate deterministic analytics
   n. Save results in a transaction
6. Record errors without stopping other tickers
7. Generate run summary
8. Update dashboards and reports
```

---

## 20. Parallel Processing and Rate Limits

The pipeline can process securities in parallel, but SEC requests must remain conservative.

Recommended pattern:

- Use a small number of SEC download workers
- Use a larger number of local parsing workers
- Separate network I/O from CPU-heavy parsing
- Rate-limit by domain
- Add randomized delay
- Use exponential backoff
- Cache every successful response

Example configuration:

```yaml
pipeline:
  ticker_workers: 4
  sec_download_workers: 2
  parser_workers: 6
  llm_workers: 3
  sec_requests_per_second: 5
  max_retries: 5
  retry_backoff_seconds: 2
```

A single ticker failure should not terminate the overall run.

---

## 21. Incremental Update Strategy

The pipeline should avoid rebuilding all data every run.

### Every Run

- Check for new filings
- Update recent prices
- Update current drawdown
- Process new insider transactions
- Process new 8-K events
- Recompute affected scores

### Daily

- Prices
- Dividends
- Form 4 filings
- 8-K filings
- Current valuation
- Current drawdown
- Watchlist signals

### Weekly

- Universe membership
- Market capitalization
- ETF AUM
- Full data-quality checks

### Quarterly

- 10-Q ingestion
- Fundamental trend updates
- Quarterly narrative summaries
- Quality and solvency recalculation

### Annually

- Latest 10-K ingestion
- Full narrative baseline
- Long-term fundamental recalculation
- Annual risk-factor comparison

---

## 22. Data Validation

Before accepting a metric:

- Verify units
- Verify period
- Verify filing type
- Verify fiscal quarter
- Reject duplicates
- Detect impossible values
- Compare against prior periods
- Flag large unexpected changes
- Preserve raw SEC concept
- Store confidence

Potential validation rules:

- Revenue should not be confused with segment revenue
- Quarterly facts should not accidentally use nine-month YTD values
- Total debt should not double-count current and long-term debt
- Free cash flow should be computed consistently
- EPS should account for stock splits
- Restated filings should supersede older values
- Amended filings should not create duplicate periods

---

## 23. Error Handling

Store errors in `pipeline_errors`.

Suggested fields:

- Run ID
- Ticker
- Module
- Stage
- URL
- Error type
- Error message
- Retry count
- Recoverable flag
- Timestamp

Failure behavior:

- Log the error
- Roll back only the affected ticker transaction
- Continue processing other securities
- Mark the ticker as partially processed
- Retry recoverable failures
- Preserve previous valid data

---

## 24. Observability

Every pipeline run should report:

- Securities attempted
- Securities completed
- Securities partially completed
- Securities failed
- New filings discovered
- Filings downloaded
- Filings parsed
- LLM summaries generated
- Cached results reused
- SEC requests made
- Retry count
- Runtime by module
- Database rows inserted or updated
- Estimated LLM cost

Example summary:

```text
Pipeline Run: 2026-08-01

Universe:                    186
Completed:                   179
Partial:                       5
Failed:                        2
New 10-K filings:              3
New 10-Q filings:             41
New 8-K filings:             118
New Form 4 filings:          642
LLM summaries generated:      52
Cached summaries reused:     497
Runtime:                 24m 18s
```

---

## 25. Suggested Repository Structure

```text
equity-intelligence-platform/
├── analyze.py
├── update_pipeline.py
├── update_universe.py
├── pyproject.toml
├── requirements.txt
├── config.yaml
├── README.md
│
├── equity_intelligence/
│   ├── universe/
│   │   ├── manager.py
│   │   └── filters.py
│   ├── providers/
│   │   ├── sec_client.py
│   │   ├── market_data.py
│   │   └── etf_data.py
│   ├── sec/
│   │   ├── submissions.py
│   │   ├── companyfacts.py
│   │   ├── filing_downloader.py
│   │   ├── filing_parser.py
│   │   ├── narrative_sections.py
│   │   ├── eight_k.py
│   │   └── form4.py
│   ├── normalization/
│   │   ├── concept_mapper.py
│   │   ├── annual.py
│   │   └── quarterly.py
│   ├── analytics/
│   │   ├── fundamentals.py
│   │   ├── ratios.py
│   │   ├── solvency.py
│   │   ├── drawdowns.py
│   │   ├── dividends.py
│   │   ├── valuation.py
│   │   ├── risk_score.py
│   │   ├── quality_score.py
│   │   └── investment_score.py
│   ├── llm/
│   │   ├── prompts.py
│   │   ├── summarizer.py
│   │   ├── validator.py
│   │   └── schemas.py
│   ├── database/
│   │   ├── connection.py
│   │   ├── schema.py
│   │   ├── repositories.py
│   │   └── migrations.py
│   ├── reporting/
│   │   ├── dashboard.py
│   │   ├── excel.py
│   │   └── json_export.py
│   └── utils/
│       ├── logging.py
│       ├── retry.py
│       ├── hashing.py
│       └── dates.py
│
├── tests/
├── data/
├── cache/
├── output/
└── docs/
```

---

## 26. Example CLI

```bash
# Run full pipeline for the active universe
python update_pipeline.py

# Run selected tickers
python update_pipeline.py --tickers NVDA WMT MSFT

# Only fetch SEC filings
python update_pipeline.py --modules sec

# Only recompute analytics
python update_pipeline.py --modules analytics --offline

# Force refresh one ticker
python update_pipeline.py --tickers NVDA --force

# Reprocess LLM summaries with a new prompt version
python update_pipeline.py --modules llm --prompt-version v3

# Generate reports without downloading new data
python analyze.py --from-db
```

---

## 27. Key Design Principles

### Official Sources First

Use SEC filings as the authoritative source for filing data and reported financial facts.

### Reproducibility

Every score and summary must be traceable to source documents, extraction versions, and formulas.

### Deterministic Calculations

Use Python for arithmetic, ratios, scoring, and backtesting.

### LLM for Synthesis

Use the LLM for summarization, comparison, classification, and narrative explanation.

### Incremental Processing

Download and process only new or changed data.

### Graceful Degradation

A failure in SEC, market data, Form 4 parsing, or LLM summarization should not destroy the rest of the ticker analysis.

### Asset-Type Awareness

Do not process ETFs as though they are conventional operating companies.

### Transparent Uncertainty

Store extraction confidence and clearly label incomplete or uncertain results.

### No Absolute Bankruptcy Prediction

Estimate financial-distress risk rather than claiming certainty.

---

## 28. Future Extensions

Potential future modules:

- Congressional trading activity
- Institutional 13F holdings
- 13D and 13G ownership changes
- Credit-rating changes
- Earnings-call transcript analysis
- Analyst estimate revisions
- Options-market signals
- Short interest
- Macro sensitivity
- Peer comparison
- Portfolio-level risk
- Correlation and diversification
- Scenario stress testing
- Monte Carlo analysis
- REST API
- Web dashboard
- Alerting and scheduled watchlists
- AI-generated investment memos
- Filing-difference engine
- Evidence-linked research assistant

---

## 29. Project Vision

The long-term goal is to build a reliable equity intelligence system that can analyze a focused universe of globally significant stocks and ETFs using official filings, structured financial data, market history, and transparent analytics.

The system should answer questions such as:

- Is the company's financial position strengthening or weakening?
- Are revenue, margins, cash flow, and debt improving?
- What changed in the latest 10-Q or 10-K?
- Did management add or remove meaningful risks?
- Is liquidity sufficient for upcoming obligations?
- Are insiders buying or selling in the open market?
- Is the current drawdown historically unusual?
- Does the company exhibit signs of financial distress?
- Is the stock fundamentally strong but temporarily mispriced?
- How does the security compare with peers and its own history?

The final platform should not replace investment judgment. It should provide a structured, evidence-based research foundation that makes large-scale company analysis faster, more consistent, and more auditable.
