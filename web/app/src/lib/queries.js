// SQL query strings. Ticker-scoped queries (tickerOverview, drawdownEvents,
// distressScore, tickerFilings, insiderTradesForTicker, eightKEvents,
// etfHoldings, priceHistory, pipelineRuns) are ported directly from the
// project's Streamlit dashboard (ui/db.py) -- same data, computed the same
// way, not reinvented. The aggregate/cross-ticker queries (marked below)
// are new, built for the Market Overview / Sector & Regulatory pages that
// didn't exist in the Streamlit app.

export const QUERIES = {
  // ---- Market Overview (new aggregate queries) -----------------------------
  universeCounts: `
    SELECT
      (SELECT COUNT(*) FROM universe) AS total,
      (SELECT COUNT(*) FROM universe WHERE status = 'active') AS active,
      (SELECT COUNT(*) FROM universe WHERE status = 'inactive') AS inactive
  `,
  totalMarketCapB: `
    SELECT SUM(market_cap) AS total_b
    FROM universe
    WHERE status = 'active'
  `,
  avgQualityScore: `
    SELECT AVG(quality_score) AS avg_score, COUNT(*) AS n
    FROM daily_snapshot ds
    JOIN universe u ON u.ticker = ds.ticker
    WHERE u.status = 'active' AND ds.quality_score IS NOT NULL
  `,
  avgMarketDrawdown: `
    SELECT AVG(current_drawdown_pct) AS avg_dd, COUNT(*) AS n
    FROM daily_snapshot ds
    JOIN universe u ON u.ticker = ds.ticker
    WHERE u.status = 'active' AND ds.current_drawdown_pct IS NOT NULL
  `,
  riskDistribution: `
    SELECT
      CASE
        WHEN ds.distress_risk_level LIKE '%solvency%' THEN 'distress'
        WHEN ds.distress_risk_level LIKE '%elevated%' THEN 'high'
        WHEN ds.distress_risk_level LIKE '%low%' THEN 'low'
        ELSE 'unscored'
      END AS bucket,
      COUNT(*) AS n
    FROM daily_snapshot ds
    JOIN universe u ON u.ticker = ds.ticker
    WHERE u.status = 'active'
    GROUP BY bucket
  `,
  universeTable: `
    SELECT
      u.ticker, u.name, u.asset_type, u.sector, u.market_cap,
      ds.price, ds.price_change_1d, ds.high_52w, ds.low_52w,
      ds.current_drawdown_pct, ds.max_drawdown_1y_pct, ds.pe_ratio, ds.dividend_yield_pct,
      ds.quality_score, ds.investment_score, ds.risk_score, ds.insider_sentiment_score,
      ds.drawdown_opportunity_score, ds.distress_risk_level, ds.valuation_tier, ds.investment_verdict,
      ds.updated_at
    FROM universe u
    LEFT JOIN daily_snapshot ds ON ds.ticker = u.ticker
    WHERE u.status = 'active'
    ORDER BY u.ticker
  `,

  // ---- Sector & Regulatory (new aggregate queries) -------------------------
  sectorBreakdown: `
    SELECT
      u.sector,
      COUNT(*) AS ticker_count,
      AVG(ds.dcf_margin_of_safety_pct) AS avg_margin_of_safety_pct,
      AVG(ds.price_change_1d) AS avg_1d_change_pct
    FROM universe u
    LEFT JOIN daily_snapshot ds ON ds.ticker = u.ticker
    WHERE u.status = 'active' AND u.sector IS NOT NULL AND u.sector != ''
    GROUP BY u.sector
    ORDER BY ticker_count DESC
  `,
  sectorValuationTierCounts: `
    SELECT u.sector, ds.valuation_tier, COUNT(*) AS n
    FROM universe u
    JOIN daily_snapshot ds ON ds.ticker = u.ticker
    WHERE u.status = 'active' AND u.sector IS NOT NULL AND ds.valuation_tier IS NOT NULL
    GROUP BY u.sector, ds.valuation_tier
  `,
  recentInsiderTrades: `
    SELECT ticker, filing_date, trade_date, insider_name, title, code,
           transaction_type, shares, price_per_share, total_value, sentiment
    FROM insider_trades
    ORDER BY filing_date DESC, trade_date DESC, id DESC
    LIMIT ?
  `,
  recentCongressTrades: `
    SELECT ticker, politician, chamber, party, transaction_type, amount_range,
           disclosure_date, trade_date
    FROM congress_trades
    ORDER BY disclosure_date DESC, trade_date DESC
    LIMIT ?
  `,

  // ---- Ticker Analysis (ported from ui/db.py, see comment above) ----------
  activeTickers: `
    SELECT ticker, name, asset_type, sector, industry, market_cap
    FROM universe
    WHERE status = 'active'
    ORDER BY asset_type, market_cap DESC, ticker
  `,
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
  drawdownEvents: `
    SELECT peak_date, peak_price, bottom_date, bottom_price, drawdown_pct,
           recovery_date, recovery_price, days_to_bottom, days_underwater,
           recovery_duration_days, is_ongoing
    FROM drawdown_events
    WHERE ticker = ?
    ORDER BY ABS(drawdown_pct) DESC
    LIMIT ?
  `,
  distressScore: `SELECT * FROM distress_scores WHERE ticker = ?`,
  eightKEvents: `
    SELECT filing_date, item_codes, is_debt_related, is_bankruptcy_related, description, filing_url
    FROM eight_k_events
    WHERE ticker = ?
    ORDER BY filing_date DESC
    LIMIT ?
  `,
  tickerFilings: `
    SELECT
      filing_date, period_end_date, form_type, accession_number, primary_doc_description,
      revenue_usd, net_income_usd, operating_income_usd, total_assets_usd, total_liabilities_usd,
      stockholders_equity_usd, total_debt_usd, debt_to_equity_ratio, eps_diluted,
      risk_score, risk_sentiment, full_sentiment, md_a_summary, risk_summary, comprehensive_summary,
      report_url
    FROM sec_financials
    WHERE ticker = ?
    ORDER BY filing_date DESC, accession_number DESC
  `,
  insiderTradesForTicker: `
    SELECT filing_date, trade_date, insider_name, title, shares, code,
           transaction_type, price_per_share, total_value, sentiment
    FROM insider_trades
    WHERE ticker = ?
    ORDER BY filing_date DESC, trade_date DESC, id DESC
    LIMIT ?
  `,
  etfLatestFilingDate: `SELECT MAX(filing_date) AS filing_date FROM etf_holdings WHERE ticker = ?`,
  etfHoldings: `
    SELECT filing_date, holding_name, holding_ticker, cusip, isin, shares,
           market_value, weight_pct, asset_category, country, currency
    FROM etf_holdings
    WHERE ticker = ? AND filing_date = ?
    ORDER BY weight_pct DESC, market_value DESC, holding_name ASC
    LIMIT ?
  `,
  priceHistory: `
    SELECT trade_date, close_price, volume
    FROM price_history
    WHERE ticker = ?
    ORDER BY trade_date ASC
    LIMIT ?
  `,

  // ---- System (pipeline run history) ---------------------------------------
  pipelineRuns: `
    SELECT run_id, run_timestamp, duration_seconds, tickers_processed, status, summary_json
    FROM pipeline_runs
    ORDER BY run_timestamp DESC
    LIMIT ?
  `,
};
