// SQL query strings -- ported directly from ui/db.py (the Streamlit dashboard's
// existing, already-verified query layer) so this static dashboard shows the
// same data, computed the same way, not a reinvented/diverging set of queries.
// Every query here mirrors a specific function in ui/db.py; comments note which.

const QUERIES = {
  // ui/db.py: get_summary_counts
  summaryActiveTickers: "SELECT COUNT(*) AS n FROM universe WHERE status = 'active'",
  summarySnapshotRows: "SELECT COUNT(*) AS n FROM daily_snapshot",
  summaryFinancialRows: "SELECT COUNT(*) AS n FROM sec_financials",
  summaryFinancialRowsWithLlm: `
    SELECT COUNT(*) AS n
    FROM sec_financials
    WHERE risk_score IS NOT NULL
       OR risk_summary IS NOT NULL
       OR md_a_summary IS NOT NULL
       OR full_sentiment IS NOT NULL
       OR comprehensive_summary IS NOT NULL
  `,
  summaryEtfRows: "SELECT COUNT(*) AS n FROM sec_etf_reports",
  summaryDrawdownTickers: "SELECT COUNT(*) AS n FROM drawdown_summary",
  summaryDistressTickers: "SELECT COUNT(*) AS n FROM distress_scores",
  summaryEightKEvents: "SELECT COUNT(*) AS n FROM eight_k_events",
  summaryPipelineRuns: "SELECT COUNT(*) AS n FROM pipeline_runs",
  summaryLatestRun: `
    SELECT run_id, run_timestamp, duration_seconds, tickers_processed, status
    FROM pipeline_runs
    ORDER BY run_timestamp DESC
    LIMIT 1
  `,

  // ui/db.py: list_active_tickers
  activeTickers: `
    SELECT ticker, name, asset_type, sector, industry, market_cap
    FROM universe
    WHERE status = 'active'
    ORDER BY asset_type, market_cap DESC, ticker
  `,

  // ui/db.py: get_universe_table
  universeTable: `
    SELECT
      u.ticker, u.name, u.asset_type, u.sector, u.market_cap,
      ds.price, ds.price_change_1d, ds.high_52w, ds.low_52w,
      ds.current_drawdown_pct, ds.max_drawdown_1y_pct, ds.pe_ratio, ds.dividend_yield_pct,
      ds.quality_score, ds.investment_score, ds.risk_score, ds.insider_sentiment_score,
      ds.drawdown_opportunity_score, ds.distress_risk_level, ds.valuation_tier, ds.investment_verdict,
      dsum.completed_drawdowns, dsum.avg_drawdown_pct, dsum.worst_drawdown_pct,
      dsum.avg_recovery_days, dsum.longest_recovery_days, ds.updated_at
    FROM universe u
    LEFT JOIN daily_snapshot ds ON ds.ticker = u.ticker
    LEFT JOIN drawdown_summary dsum ON dsum.ticker = u.ticker
    WHERE u.status = 'active'
    ORDER BY u.ticker
  `,

  // ui/db.py: get_ticker_overview
  tickerOverview: `
    SELECT
      u.ticker, u.name, u.asset_type, u.sector, u.industry, u.market_cap,
      ds.price, ds.price_change_1d, ds.high_52w, ds.low_52w,
      ds.current_drawdown_pct, ds.max_drawdown_1y_pct, ds.pe_ratio, ds.forward_pe,
      ds.ev_ebitda, ds.fcf_yield_pct, ds.dividend_yield_pct,
      ds.quality_score, ds.investment_score, ds.risk_score, ds.distress_risk_level,
      ds.insider_sentiment_score, ds.drawdown_opportunity_score,
      ds.valuation_tier, ds.investment_verdict, ds.updated_at,
      ds.dcf_fair_value_low, ds.dcf_fair_value_base, ds.dcf_fair_value_high, ds.dcf_margin_of_safety_pct,
      ds.short_percent_of_float
    FROM universe u
    LEFT JOIN daily_snapshot ds ON ds.ticker = u.ticker
    WHERE u.ticker = ?
  `,

  // ui/db.py: get_drawdown_summary
  drawdownSummary: "SELECT * FROM drawdown_summary WHERE ticker = ?",

  // ui/db.py: get_drawdown_events
  drawdownEvents: `
    SELECT peak_date, peak_price, bottom_date, bottom_price, drawdown_pct,
           recovery_date, recovery_price, days_to_bottom, days_underwater,
           recovery_duration_days, is_ongoing
    FROM drawdown_events
    WHERE ticker = ?
    ORDER BY ABS(drawdown_pct) DESC
    LIMIT ?
  `,

  // ui/db.py: get_distress_score
  distressScore: "SELECT * FROM distress_scores WHERE ticker = ?",

  // ui/db.py: get_eightk_events
  eightKEvents: `
    SELECT filing_date, item_codes, is_debt_related, is_bankruptcy_related, description, filing_url
    FROM eight_k_events
    WHERE ticker = ?
    ORDER BY filing_date DESC
    LIMIT ?
  `,

  // ui/db.py: get_ticker_filings
  tickerFilings: `
    SELECT
      filing_date, period_end_date, form_type, accession_number, primary_doc_description,
      revenue_usd, net_income_usd, operating_income_usd, total_assets_usd, total_liabilities_usd,
      stockholders_equity_usd, total_debt_usd, debt_to_equity_ratio, eps_diluted,
      risk_score, risk_sentiment, full_sentiment, md_a_summary, risk_summary, comprehensive_summary,
      narrative_mda, narrative_risk_factors, narrative_legal, narrative_commitments,
      narrative_buybacks, narrative_liquidity, narrative_subsequent, report_url
    FROM sec_financials
    WHERE ticker = ?
    ORDER BY filing_date DESC, accession_number DESC
  `,

  // ui/db.py: get_insider_trades
  insiderTrades: `
    SELECT filing_date, trade_date, insider_name, title, shares, code,
           transaction_type, price_per_share, total_value, sentiment
    FROM insider_trades
    WHERE ticker = ?
    ORDER BY filing_date DESC, trade_date DESC, id DESC
    LIMIT ?
  `,

  // ui/db.py: get_etf_holdings (latest filing_date lookup, then holdings)
  etfLatestFilingDate: "SELECT MAX(filing_date) AS filing_date FROM etf_holdings WHERE ticker = ?",
  etfHoldings: `
    SELECT filing_date, holding_name, holding_ticker, cusip, isin, shares,
           market_value, weight_pct, asset_category, country, currency
    FROM etf_holdings
    WHERE ticker = ? AND filing_date = ?
    ORDER BY weight_pct DESC, market_value DESC, holding_name ASC
    LIMIT ?
  `,

  // ui/db.py: get_price_history
  priceHistory: `
    SELECT trade_date, close_price, volume
    FROM price_history
    WHERE ticker = ?
    ORDER BY trade_date ASC
    LIMIT ?
  `,

  // ui/db.py: get_pipeline_runs
  pipelineRuns: `
    SELECT run_id, run_timestamp, duration_seconds, tickers_processed, status, summary_json
    FROM pipeline_runs
    ORDER BY run_timestamp DESC
    LIMIT ?
  `,
};
