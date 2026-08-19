import sqlite3
import time
import json
import pandas as pd
from datetime import datetime
from .schema import init_db, migrate_db, sync_universe_from_csv, DB_NAME, UNIVERSE_CSV_PATH
from .logger import banner, step, info, success, warning, error, ticker_start, ticker_done, progress
from .sec_edgar_worker import sync_sec_insider_data
from .sec_etf_worker import sync_etf_reports
from .sec_financials_worker import sync_10k_10q_financials, get_annual_revenue_history
from .sec_eightk_worker import sync_8k_events
from .drawdown_analytics import compute_and_store_drawdowns, drawdown_opportunity_score
from .distress_analytics import compute_distress, store_distress_score
from .dcf_valuation import compute_dcf_fair_value, compute_base_growth_rate
from .scoring import (
    compute_insider_sentiment_score,
    get_recent_eightk_flags,
    compute_risk_score,
    compute_quality_score as compute_composite_quality_score,
    compute_investment_score as compute_composite_investment_score,
)

try:
    import yfinance as yf
except Exception:
    yf = None

# Minimum rows of price_history before we consider a ticker's series "backfilled"
# enough for meaningful multi-year drawdown analysis.
PRICE_HISTORY_BACKFILL_THRESHOLD = 200

def calculate_quality_score(pe, current_dd, asset_type, etf_metrics=None):
    """Compute composite factor quality score (0-100)."""
    if asset_type == 'ETF':
        if etf_metrics:
            score = 70
            exp_ratio = etf_metrics.get('expense_ratio', 0)
            if exp_ratio > 0 and exp_ratio < 0.05:
                score += 10
            elif exp_ratio > 0.1:
                score -= 10
            
            turnover = etf_metrics.get('turnover_rate', 0)
            if turnover > 0 and turnover < 0.3:
                score += 5
            elif turnover > 1.0:
                score -= 5
            
            if current_dd < -20:
                score += 10
            elif current_dd < -10:
                score += 5
            return max(10, min(99, score))
        return 88
    score = 70
    if pe > 0 and pe < 25:
        score += 15
    elif pe >= 25 and pe < 40:
        score += 5
    elif pe >= 40:
        score -= 10
        
    if current_dd < -20:
        score += 10
    elif current_dd < -10:
        score += 5
    return max(10, min(99, score))

def calculate_investment_score(pe, current_dd, asset_type,
                               etf_metrics=None,
                               etf_risk=None,
                               narrative=None):
    """
    Returns a 0-100 score that blends:
        - traditional fundamentals (PE, FCF-yield, dividend-yield, EV/EBITDA)
        - ETF-specific cost/turnover
        - narrative-derived risk (inverted: higher risk → lower score)
        - sentiment boost (small)
    """
    # ---- start from a neutral base --------------------------------
    base = 50   # we'll move away from 50 based on the factors
    score = base

    if asset_type == 'ETF':
        # ---- ETF-specific cost & turnover -------------------------
        if etf_metrics:
            er = etf_metrics.get('expense_ratio', 0)
            if 0 < er < 0.05:
                score += 12
            elif er > 0.10:
                score -= 12

            to = etf_metrics.get('turnover_rate', 0)
            if 0 < to < 0.3:
                score += 6
            elif to > 1.0:
                score -= 6

        # ---- narrative-derived risk (invert: high risk = low score) ----
        risk_bundle = etf_risk or narrative
        if risk_bundle:
            risk = risk_bundle.get('risk_score', 50)   # 0-100, higher = riskier
            # map risk → contribution: 0 risk → +20, 100 risk → -20
            risk_adj = (50 - risk) * 0.4   # scales to roughly ±20
            score += risk_adj

            # sentiment bonus (small)
            sent = risk_bundle.get('sentiment', risk_bundle.get('full_sentiment', 'neutral'))
            if sent == 'positive':
                score += 4
            elif sent == 'negative':
                score -= 4

        # ---- ETF dividend yield (if you have it) -----------------
        dy = etf_metrics.get('dividend_yield', 0) if etf_metrics else 0
        if dy > 0.03:        # >3%
            score += 6
        elif dy < 0.01:
            score -= 4

    else:   # ----- STOCKS -----------------------------------------
        # ---- classic fundamentals -------------------------------------
        if pe > 0:
            if pe < 15:
                score += 20
            elif pe < 25:
                score += 10
            elif pe < 35:
                score += 5
            elif pe >= 50:
                score -= 15
            elif pe >= 40:
                score -= 10

        # ---- FCF yield (higher is better) -----------------------------
        fcf_yield = etf_metrics.get('fcf_yield_pct', 0) if etf_metrics else 0
        if fcf_yield > 0.08:      # >8%
            score += 12
        elif fcf_yield < 0.02:
            score -= 8

        # ---- dividend yield ------------------------------------------
        div_yield = etf_metrics.get('dividend_yield', 0) if etf_metrics else 0
        if div_yield > 0.04:
            score += 8
        elif div_yield < 0.005:
            score -= 4

        # ---- EV/EBITDA (lower is better) -----------------------------
        ev_ebitda = etf_metrics.get('ev_ebitda', 0) if etf_metrics else 0
        if 0 < ev_ebitda < 8:
            score += 10
        elif ev_ebitda > 20:
            score -= 10

        # ---- risk from narrative (if we have it for stocks) --------
        risk_bundle = narrative or etf_risk
        if risk_bundle:
            risk = risk_bundle.get('risk_score', 50)
            risk_adj = (50 - risk) * 0.3   # a bit less weight for stocks
            score += risk_adj

            sent = risk_bundle.get('full_sentiment', risk_bundle.get('sentiment', 'neutral'))
            if sent == 'positive':
                score += 3
            elif sent == 'negative':
                score -= 3

            md_a_summary = (risk_bundle.get('md_a_summary') or "").lower()
            if md_a_summary:
                if any(word in md_a_summary for word in ["growth", "expand", "margin", "strong", "improve"]):
                    score += 3
                if any(word in md_a_summary for word in ["decline", "pressure", "weak", "loss", "risk"]):
                    score -= 3

    # ---- draw‑down adjustment (same for both) ----------------------
    if current_dd < -20:
        score += 12
    elif current_dd < -10:
        score += 6
    elif current_dd > 0:   # trading above 52‑wk high
        score -= 6

    # ---- final clamp --------------------------------------------------
    return max(0, min(100, int(round(score))))

def get_valuation_tier(pe, asset_type, etf_metrics=None):
    if asset_type == 'ETF':
        if etf_metrics and etf_metrics.get('expense_ratio', 0) < 0.05:
            return 'Fair Value'
        return 'Fair Value'
    if pe <= 0:
        return 'Speculative'
    if pe < 20:
        return 'Deep Value'
    if pe < 30:
        return 'Fair Value'
    return 'Premium / Growth'

def _table_columns(cursor, table_name):
    cursor.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in cursor.fetchall()}

def _fundamental_fcf_yield(info):
    market_cap = float(info.get('marketCap') or 0)
    free_cash_flow = info.get('freeCashflow') or info.get('freeCashFlow') or 0
    try:
        free_cash_flow = float(free_cash_flow or 0)
    except (TypeError, ValueError):
        free_cash_flow = 0.0
    if market_cap > 0 and free_cash_flow:
        return round((free_cash_flow / market_cap) * 100, 2)
    return 0.0

def _sec_derived_valuation(price, fin):
    """Compute valuation multiples from SEC fundamentals with yfinance supplying
    only the live price. Returns a dict of fields that could be computed; any
    field not present should fall back to the yfinance-sourced value. Forward
    PE is intentionally excluded -- forward EPS estimates are analyst
    consensus and are never disclosed in SEC filings, so there is no
    SEC-derived substitute for it.
    """
    result = {}
    if not fin or not price:
        return result

    shares = fin.get("shares_outstanding")
    market_cap_usd = price * shares if shares else None
    if market_cap_usd:
        result["market_cap_usd"] = market_cap_usd

    # Use full-fiscal-year (10-K) EPS only -- a 10-Q's diluted EPS is quarter-only
    # and would understate EPS (overstate PE) by roughly 4x if used directly.
    eps = fin.get("annual_eps_diluted")
    if eps:
        result["pe"] = round(price / eps, 2)

    if market_cap_usd:
        dividends_paid = fin.get("annual_dividends_paid_usd")
        if dividends_paid and shares:
            dividend_per_share = dividends_paid / shares
            result["div_yield_pct"] = round((dividend_per_share / price) * 100, 2)

        # Use the annual (10-K) figure on both sides of each ratio -- mixing an
        # annual value with a latest-any-filing (possibly single-quarter) value
        # for the other side understates/inflates these multiples by ~4x.
        capex = fin.get("annual_capex_usd")
        ocf = fin.get("annual_operating_cash_flow_usd")
        if ocf is not None and capex is not None:
            fcf = ocf - capex
            result["fcf_yield_pct"] = round((fcf / market_cap_usd) * 100, 2)

        d_and_a = fin.get("annual_depreciation_amortization_usd")
        operating_income = fin.get("annual_operating_income_usd")
        if d_and_a is not None and operating_income is not None:
            ebitda = operating_income + d_and_a
            total_debt = fin.get("total_debt_usd") or 0
            cash = fin.get("cash_usd") or 0
            enterprise_value = market_cap_usd + total_debt - cash
            if ebitda > 0:
                result["ev_ebitda"] = round(enterprise_value / ebitda, 2)

    return result


def _load_latest_financials_map(cursor):
    """Latest known balance-sheet/cash-flow fields per stock ticker, plus a
    simple YoY revenue growth rate from the two most recent 10-K filings."""
    cursor.execute("""
        SELECT ticker, cik, form_type, filing_date, revenue_usd, net_income_usd, operating_income_usd,
               total_assets_usd, current_assets_usd, current_liabilities_usd,
               operating_cash_flow_usd, debt_to_equity_ratio, total_debt_usd, cash_usd,
               eps_diluted, shares_outstanding, capex_usd, depreciation_amortization_usd,
               dividends_paid_usd
        FROM sec_financials
        WHERE ticker IN (SELECT ticker FROM universe WHERE asset_type = 'Stock' AND status = 'active')
        ORDER BY filing_date DESC
    """)
    latest_map = {}
    annual_revenue_map = {}
    for row in cursor.fetchall():
        (ticker, cik, form_type, filing_date, revenue, net_income, operating_income,
         total_assets, current_assets, current_liabilities, ocf, d2e, total_debt, cash,
         eps_diluted, shares_outstanding, capex, d_and_a, dividends_paid) = row
        if ticker not in latest_map:
            latest_map[ticker] = {
                "cik": cik,
                "revenue_usd": revenue,
                "net_income_usd": net_income,
                "operating_income_usd": operating_income,
                "total_assets_usd": total_assets,
                "current_assets_usd": current_assets,
                "current_liabilities_usd": current_liabilities,
                "operating_cash_flow_usd": ocf,
                "debt_to_equity_ratio": d2e,
                "total_debt_usd": total_debt,
                "cash_usd": cash,
                "eps_diluted": eps_diluted,
                "shares_outstanding": shares_outstanding,
                "capex_usd": capex,
                "depreciation_amortization_usd": d_and_a,
                "dividends_paid_usd": dividends_paid,
            }
        if form_type == "10-K" and revenue is not None:
            annual_revenue_map.setdefault(ticker, []).append(revenue)

        # Capex/D&A/dividends are cash-flow-statement figures that 10-Qs often report
        # year-to-date rather than quarter-only (doc section 7.4). Prefer the latest
        # 10-K's clean full-fiscal-year figure for these; only fall back to whatever
        # the most recent filing reported if no 10-K value has been captured yet.
        if form_type == "10-K":
            entry = latest_map.setdefault(ticker, {})
            if "annual_capex_usd" not in entry and capex is not None:
                entry["annual_capex_usd"] = capex
            if "annual_depreciation_amortization_usd" not in entry and d_and_a is not None:
                entry["annual_depreciation_amortization_usd"] = d_and_a
            if "annual_dividends_paid_usd" not in entry and dividends_paid is not None:
                entry["annual_dividends_paid_usd"] = dividends_paid
            if "annual_operating_income_usd" not in entry and operating_income is not None:
                entry["annual_operating_income_usd"] = operating_income
            if "annual_operating_cash_flow_usd" not in entry and ocf is not None:
                entry["annual_operating_cash_flow_usd"] = ocf
            # EPS must come from a full fiscal year, not a single quarter -- a 10-Q's
            # diluted EPS is quarter-only, and dividing the current price by it would
            # produce a PE roughly 4x too high.
            if "annual_eps_diluted" not in entry and eps_diluted is not None:
                entry["annual_eps_diluted"] = eps_diluted

    for ticker, revenues in annual_revenue_map.items():
        if len(revenues) >= 2 and revenues[1]:
            growth_pct = round(((revenues[0] - revenues[1]) / abs(revenues[1])) * 100, 2)
            latest_map.setdefault(ticker, {})["revenue_growth_pct"] = growth_pct

    return latest_map


def _build_snapshot_record(
    ticker,
    current_price,
    price_change_1d,
    high_52w,
    low_52w,
    current_dd,
    max_dd_1y,
    pe,
    fwd_pe,
    ev_ebitda,
    fcf_yield_pct,
    div_yield,
    quality_score,
    investment_score,
    val_tier,
    verdict,
    updated_at,
    include_investment_score,
    extra_scores=None,
    available_columns=None,
):
    columns = [
        "ticker",
        "price",
        "price_change_1d",
        "high_52w",
        "low_52w",
        "current_drawdown_pct",
        "max_drawdown_1y_pct",
        "pe_ratio",
        "forward_pe",
        "ev_ebitda",
        "fcf_yield_pct",
        "dividend_yield_pct",
        "quality_score",
    ]
    values = [
        ticker,
        round(current_price, 2),
        price_change_1d,
        round(high_52w, 2),
        round(low_52w, 2),
        current_dd,
        max_dd_1y,
        pe,
        fwd_pe,
        ev_ebitda,
        fcf_yield_pct,
        div_yield,
        quality_score,
    ]
    if include_investment_score:
        columns.append("investment_score")
        values.append(investment_score)
    for column, value in (extra_scores or {}).items():
        if available_columns is None or column in available_columns:
            columns.append(column)
            values.append(value)
    columns.extend(["valuation_tier", "investment_verdict", "updated_at"])
    values.extend([val_tier, verdict, updated_at])
    return columns, values

def run_pipeline(db_path=DB_NAME, skip_form4=False, reset_financials=False, resume_llm=False, skip_8k=False):
    start_time = time.time()
    run_id = f"PR-{int(time.time())}"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Ensure DB tables exist, including tables added by later migrations
    # (eight_k_events, drawdown_events, drawdown_summary, distress_scores).
    # This must run before ANY worker touches the database -- individual
    # workers also call migrate_db() defensively, but 8-K sync runs before
    # the 10-K/10-Q worker (the first place migrate_db() used to be called),
    # so relying on worker-level calls alone left a window where the new
    # tables didn't exist yet.
    init_db(db_path)
    migrate_db(db_path)

    # Reconcile the active universe against the weekly $50B-crossing scan
    # (see universe_scanner.py / .github/workflows/universe-scan.yml) --
    # data/universe_50b.csv is checked into git, so this is the sole source
    # of the active universe, no hardcoded fallback seed exists in init_db().
    sync_universe_from_csv(db_path, UNIVERSE_CSV_PATH)

    banner(f"Starting Drawdown Analyzer pipeline run [{run_id}]")
    info(f"Timestamp: {now_str}")

    # Step 1: Sync SEC-derived data first so the score can use the latest narratives
    if resume_llm:
        step("Step 1b/5: resuming LLM narrative scoring from existing SEC filings")
        financial_reports_count = sync_10k_10q_financials(
            db_path,
            days_back=365,
            reset_financials=False,
            resume_llm_only=True,
        )
        success(f"Financial narrative resume complete: {financial_reports_count} rows updated")
        progress(20, "Phase 1/5: SEC narrative scoring complete")
        eightk_events_count = 0
        sec_filings_count = 0
        etf_reports_count = 0
    else:
        step("Step 1/5: syncing SEC insider, financial, 8-K, and ETF filings")
        if skip_form4:
            info("Skipping SEC Form 4 insider sync (--skip-form4)")
            sec_filings_count = 0
        else:
            info("Starting SEC Form 4 insider sync")
            sec_filings_count = sync_sec_insider_data(db_path)
            success(f"SEC Form 4 sync complete: {sec_filings_count} filings stored")
        if skip_8k:
            info("Skipping SEC 8-K debt/material event sync (--skip-8k)")
            eightk_events_count = 0
        else:
            info("Starting SEC 8-K debt/material event sync")
            eightk_events_count = sync_8k_events(db_path, days_back=180)
            success(f"SEC 8-K sync complete: {eightk_events_count} events stored")
        info("Starting 10-K / 10-Q narrative sync")
        financial_reports_count = sync_10k_10q_financials(
            db_path,
            days_back=365,
            reset_financials=reset_financials,
        )
        success(f"Financial narrative sync complete: {financial_reports_count} rows updated")
        progress(20, "Phase 1/5: SEC filing sync complete")
        info("Starting ETF N-PORT / N-CEN sync")
        etf_reports_count = sync_etf_reports(db_path, years_back=5)
        success(f"ETF filing sync complete: {etf_reports_count} records touched")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    daily_snapshot_columns = _table_columns(cursor, "daily_snapshot")
    supports_investment_score = "investment_score" in daily_snapshot_columns

    # Step 2: Fetch active universe
    step("Step 2/5: loading active universe")
    cursor.execute("SELECT ticker, asset_type, market_cap, sector FROM universe WHERE status = 'active'")
    tickers = cursor.fetchall()
    success(f"Loaded {len(tickers)} active tickers")
    progress(35, "Phase 2/5: active universe loaded")

    # Pre-fetch latest ETF metrics from SEC filings
    step("Step 3/5: loading latest ETF and stock narrative signals")
    cursor.execute("""
        SELECT ticker, expense_ratio, turnover_rate, cash_percentage, nav_per_share
        FROM sec_etf_reports
        WHERE ticker IN (SELECT ticker FROM universe WHERE asset_type = 'ETF' AND status = 'active')
        AND form_type IN ('N-PORT', 'NPORT-P', 'NPORT-EX')
        ORDER BY filing_date DESC
    """)
    etf_metrics_map = {}
    for row in cursor.fetchall():
        if row[0] not in etf_metrics_map:
            etf_metrics_map[row[0]] = {
                'expense_ratio': row[1],
                'turnover_rate': row[2],
                'cash_percentage': row[3],
                'nav_per_share': row[4]
            }

    # -----------------------------------------------------------------
    # 0️⃣  Pull latest narrative‑derived metrics (risk, sentiment, MD&A)
    # -----------------------------------------------------------------
    cursor.execute("""
        SELECT ticker,
               risk_score,
               risk_summary,
               risk_sentiment,
               md_a_summary
        FROM sec_financials
        WHERE ticker IN (SELECT ticker FROM universe WHERE asset_type = 'ETF' AND status = 'active')
        AND form_type = 'N-PORT'
        ORDER BY filing_date DESC
    """)
    etf_risk_map = {}
    for row in cursor.fetchall():
        if row[0] not in etf_risk_map:
            etf_risk_map[row[0]] = {
                'risk_score': row[1],
                'risk_summary': row[2],
                'sentiment': row[3],
                'md_a_summary': row[4],
            }

    cursor.execute("""
        SELECT ticker,
               risk_score,
               risk_summary,
               risk_sentiment,
               md_a_summary,
               full_sentiment,
               comprehensive_summary
        FROM sec_financials
        WHERE ticker IN (SELECT ticker FROM universe WHERE asset_type = 'Stock' AND status = 'active')
          AND form_type IN ('10-K', '10-Q')
        ORDER BY filing_date DESC, id DESC
    """)
    stock_narrative_map = {}
    for row in cursor.fetchall():
        if row[0] not in stock_narrative_map:
            stock_narrative_map[row[0]] = {
                'risk_score': row[1],
                'risk_summary': row[2],
                'sentiment': row[3],
                'md_a_summary': row[4],
                'full_sentiment': row[5],
                'comprehensive_summary': row[6],
            }
    progress(50, "Phase 3/5: latest narrative signals loaded")

    # Latest balance-sheet/cash-flow fields per stock, for risk/quality scoring inputs
    stock_financials_map = _load_latest_financials_map(cursor)

    processed_count = 0

    # Step 3 & 4: Market Data & Drawdown Engine
    step("Step 4/5: fetching live market data and computing scores")
    total_tickers = len(tickers)

    if yf is None:
        warning("yfinance is unavailable; skipping live market refresh for this run")
    else:
        for index, (ticker, asset_type, cap, sector) in enumerate(tickers, start=1):
            yf_ticker = ticker.replace('.', '-')
            try:
                ticker_start(ticker, f"fetching market history and fundamentals ({asset_type})")

                # Backfill several years of history once, so drawdown analytics has
                # enough series to find real peak/trough/recovery episodes.
                cursor.execute("SELECT COUNT(*) FROM price_history WHERE ticker = ?", (ticker,))
                existing_price_rows = cursor.fetchone()[0]
                needs_backfill = existing_price_rows < PRICE_HISTORY_BACKFILL_THRESHOLD

                stock = yf.Ticker(yf_ticker)
                hist = stock.history(period="5y" if needs_backfill else "1y")

                if hist.empty:
                    warning(f"{ticker}: no market data found")
                    continue

                close_series = hist['Close']
                current_price = float(close_series.iloc[-1])
                prev_price = float(close_series.iloc[-2]) if len(close_series) > 1 else current_price
                price_change_1d = round(((current_price - prev_price) / prev_price) * 100, 2)

                high_52w_series = close_series.tail(252)
                high_52w = float(high_52w_series.max())
                low_52w = float(high_52w_series.min())

                current_dd = round(((current_price - high_52w) / high_52w) * 100, 2)
                max_dd_1y = round(((low_52w - high_52w) / high_52w) * 100, 2)

                # Store daily price history series: full backfill on first pass, else last 30 days.
                # yfinance occasionally emits a dividend-only pseudo-row with no real trading data
                # (OHLC all NaN, e.g. an ex-dividend date with no separate price bar) -- confirmed
                # live against CARR's real 5y history. Python's sqlite3 module silently binds
                # float('nan') as SQL NULL, which violates close_price's NOT NULL constraint --
                # and since this is a single executemany() batch, one bad row was failing the
                # ENTIRE ticker's insert (all ~1255 rows on a 5y backfill), not just that one row.
                # A 5y backfill (needs_backfill=True, i.e. every newly-added ticker) hits this far
                # more often than a 30-day incremental update, which is why 284/285 tickers failed
                # in one real run right after the $50B threshold change added a wave of brand-new
                # tickers needing their first full backfill.
                history_to_store = hist if needs_backfill else hist.tail(30)
                price_rows = [
                    (ticker, idx.strftime('%Y-%m-%d'), float(row['Close']), int(row['Volume']))
                    for idx, row in history_to_store.iterrows()
                    if pd.notna(row['Close']) and pd.notna(row['Volume'])
                ]
                skipped = len(history_to_store) - len(price_rows)
                if skipped:
                    warning(f"{ticker}: skipped {skipped} price_history row(s) with missing Close/Volume (e.g. a dividend-only pseudo-row)")
                cursor.executemany("""
                    INSERT OR REPLACE INTO price_history (ticker, trade_date, close_price, volume)
                    VALUES (?, ?, ?, ?)
                """, price_rows)

                # Fundamentals & Valuation. yfinance supplies the live price/quote-only
                # figures (forward PE has no SEC substitute); everything else is computed
                # from our own SEC fundamentals when available, falling back to yfinance's
                # own figures only when SEC data is missing (e.g. financials not synced yet).
                stock_info = stock.info if hasattr(stock, 'info') else {}
                fwd_pe = float(stock_info.get('forwardPE', 24.0) or 24.0)
                # FINRA settlement data via yfinance, e.g. 0.1354 = 13.54% of
                # float sold short -- stale by design (published twice monthly),
                # not something that gets fresher by running the pipeline more
                # often. None when yfinance doesn't have it for this ticker.
                short_pct_float = stock_info.get('shortPercentOfFloat')

                sec_valuation = (
                    _sec_derived_valuation(current_price, stock_financials_map.get(ticker))
                    if asset_type == 'Stock' else {}
                )

                pe = sec_valuation.get('pe') or float(stock_info.get('trailingPE', 28.5) or 28.5)
                ev_ebitda = sec_valuation.get('ev_ebitda') or float(stock_info.get('enterpriseToEbitda', 18.0) or 18.0)
                div_yield = sec_valuation.get('div_yield_pct')
                if div_yield is None:
                    div_yield = round(float(stock_info.get('dividendYield', 0.012) or 0.012) * 100, 2)
                fcf_yield_pct = sec_valuation.get('fcf_yield_pct')
                if fcf_yield_pct is None:
                    fcf_yield_pct = _fundamental_fcf_yield(stock_info)

                # Live Market Cap: prefer price x SEC shares_outstanding over yfinance's
                # own marketCap figure, which can diverge from our own book of record.
                market_cap_billion = cap
                market_cap_usd = sec_valuation.get('market_cap_usd')
                if market_cap_usd and market_cap_usd > 0:
                    market_cap_billion = round(market_cap_usd / 1e9, 1)
                else:
                    live_cap_b = stock_info.get('marketCap')
                    if live_cap_b and live_cap_b > 0:
                        market_cap_billion = round(live_cap_b / 1e9, 1)
                        market_cap_usd = live_cap_b
                cursor.execute("UPDATE universe SET market_cap = ?, last_updated = datetime('now') WHERE ticker = ?",
                                   (market_cap_billion, ticker))

                # Fetch ETF metrics if available
                etf_metrics = etf_metrics_map.get(ticker) if asset_type == 'ETF' else None

                # Pull risk bundle (if any) for this ticker
                risk_bundle = etf_risk_map.get(ticker) if asset_type == 'ETF' else None
                stock_narrative = stock_narrative_map.get(ticker) if asset_type == 'Stock' else None
                score_metrics = dict(etf_metrics or {})
                score_metrics.update({
                    'ev_ebitda': ev_ebitda,
                    'fcf_yield_pct': fcf_yield_pct / 100 if fcf_yield_pct else 0.0,
                    'dividend_yield': div_yield / 100 if div_yield else 0.0,
                })

                # ---- Drawdown/recovery analytics (doc section 11) --------------
                drawdown_summary = compute_and_store_drawdowns(cursor, ticker)
                drawdown_opp_score = drawdown_opportunity_score(drawdown_summary)

                # ---- Financial-distress estimate (doc section 13, stocks only) --
                distress_result = None
                if asset_type == 'Stock':
                    distress_result = compute_distress(
                        cursor, ticker,
                        market_cap_usd=market_cap_usd if market_cap_usd else market_cap_billion * 1e9,
                        sector=sector,
                    )
                    store_distress_score(cursor, distress_result)

                # ---- Insider sentiment + 8-K debt/bankruptcy flags (stocks only) --
                insider_score, insider_meta = (50, {})
                eightk_flags = {}
                if asset_type == 'Stock':
                    insider_score, insider_meta = compute_insider_sentiment_score(cursor, ticker)
                    eightk_flags = get_recent_eightk_flags(cursor, ticker)

                # ---- Growth-adjusted DCF fair value (sensitivity range, stocks only) --
                # See dcf_valuation.py module docstring for the full methodology and
                # why this is a low/base/high range rather than a single point target.
                dcf_result = None
                if asset_type == 'Stock':
                    fin_for_dcf = stock_financials_map.get(ticker, {})
                    ocf = fin_for_dcf.get('annual_operating_cash_flow_usd')
                    capex = fin_for_dcf.get('annual_capex_usd')
                    base_fcf = (ocf - capex) if (ocf is not None and capex is not None) else None
                    # Multi-year revenue history comes straight from the cached SEC
                    # companyfacts payload (get_annual_revenue_history), not from
                    # counting sec_financials rows -- that table may only ever hold
                    # the latest 10-K depending on how fetch_10k_10q_filings' days_back
                    # is configured, but companyfacts already has the full historical
                    # time series regardless of how many rows got stored locally.
                    revenue_history = get_annual_revenue_history(ticker, fin_for_dcf.get('cik'))
                    growth_rate = compute_base_growth_rate(
                        revenue_history,
                        fallback_growth_pct=fin_for_dcf.get('revenue_growth_pct'),
                    )
                    dcf_result = compute_dcf_fair_value(
                        base_fcf=base_fcf,
                        growth_rate=growth_rate,
                        beta=stock_info.get('beta'),
                        market_cap_usd=market_cap_usd if market_cap_usd else (market_cap_billion * 1e9 if market_cap_billion else None),
                        total_debt_usd=fin_for_dcf.get('total_debt_usd'),
                        cash_usd=fin_for_dcf.get('cash_usd'),
                        shares_outstanding=fin_for_dcf.get('shares_outstanding'),
                    )
                dcf_margin_of_safety_pct = None
                if dcf_result and current_price:
                    dcf_margin_of_safety_pct = round(
                        (dcf_result["fair_value_base"] - current_price) / current_price * 100, 2
                    )

                # ---- Legacy PE/ETF-driven valuation score (kept as the valuation input) --
                legacy_valuation_score = calculate_quality_score(pe, current_dd, asset_type, etf_metrics)

                fin = stock_financials_map.get(ticker, {}) if asset_type == 'Stock' else {}
                revenue = fin.get('revenue_usd')
                current_ratio = None
                if fin.get('current_assets_usd') and fin.get('current_liabilities_usd'):
                    current_ratio = fin['current_assets_usd'] / fin['current_liabilities_usd']

                if asset_type == 'Stock' and revenue:
                    net_margin_pct = (fin.get('net_income_usd') or 0) / revenue * 100
                    operating_margin_pct = (fin.get('operating_income_usd') or 0) / revenue * 100
                    ocf_margin_pct = (fin.get('operating_cash_flow_usd') or 0) / revenue * 100 if fin.get('operating_cash_flow_usd') is not None else None
                    roa_pct = (fin.get('net_income_usd') or 0) / fin['total_assets_usd'] * 100 if fin.get('total_assets_usd') else None
                    quality_score = compute_composite_quality_score(
                        revenue_growth_pct=fin.get('revenue_growth_pct'),
                        net_margin_pct=net_margin_pct,
                        ocf_margin_pct=ocf_margin_pct,
                        operating_margin_pct=operating_margin_pct,
                        debt_to_equity=fin.get('debt_to_equity_ratio'),
                        roa_pct=roa_pct,
                        dividend_yield_pct=div_yield,
                    )
                else:
                    quality_score = legacy_valuation_score

                risk_score = compute_risk_score(
                    distress=distress_result,
                    current_ratio=current_ratio,
                    operating_cash_flow=fin.get('operating_cash_flow_usd'),
                    narrative_risk_score=(stock_narrative or {}).get('risk_score'),
                    legal_sentiment=(stock_narrative or {}).get('full_sentiment'),
                    drawdown_summary=drawdown_summary,
                    insider_sentiment_score=insider_score if asset_type == 'Stock' else None,
                    eightk_flags=eightk_flags,
                )

                dividend_score = max(0, min(100, int(round(20 + div_yield * 15)))) if div_yield else 30

                investment_score = compute_composite_investment_score(
                    quality_score=quality_score,
                    valuation_score=legacy_valuation_score,
                    risk_score=risk_score,
                    filing_risk_score=(stock_narrative or {}).get('risk_score'),
                    drawdown_opportunity_score=drawdown_opp_score,
                    dividend_score=dividend_score,
                    insider_sentiment_score=insider_score if asset_type == 'Stock' else 50,
                )

                val_tier = get_valuation_tier(pe, asset_type, etf_metrics)
                verdict = "BUY / ACCUMULATE" if current_dd < -15 else ("HOLD" if current_dd < -5 else "MONITOR")
                updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # Write atomic daily snapshot
                snapshot_columns, snapshot_values = _build_snapshot_record(
                    ticker,
                    current_price,
                    price_change_1d,
                    high_52w,
                    low_52w,
                    current_dd,
                    max_dd_1y,
                    pe,
                    fwd_pe,
                    ev_ebitda,
                    fcf_yield_pct,
                    div_yield,
                    quality_score,
                    investment_score,
                    val_tier,
                    verdict,
                    updated_at,
                    supports_investment_score,
                    extra_scores={
                        "risk_score": risk_score,
                        "distress_risk_level": (distress_result or {}).get("risk_level"),
                        "insider_sentiment_score": insider_score if asset_type == 'Stock' else None,
                        "drawdown_opportunity_score": drawdown_opp_score,
                        "short_percent_of_float": short_pct_float,
                        "dcf_fair_value_low": (dcf_result or {}).get("fair_value_low"),
                        "dcf_fair_value_base": (dcf_result or {}).get("fair_value_base"),
                        "dcf_fair_value_high": (dcf_result or {}).get("fair_value_high"),
                        "dcf_margin_of_safety_pct": dcf_margin_of_safety_pct,
                    },
                    available_columns=daily_snapshot_columns,
                )
                placeholders = ", ".join(["?"] * len(snapshot_values))
                insert_sql = f"""
                    INSERT OR REPLACE INTO daily_snapshot (
                        {", ".join(snapshot_columns)}
                    ) VALUES ({placeholders})
                """
                cursor.execute(insert_sql, snapshot_values)
                
                processed_count += 1
                ticker_done(
                    ticker,
                    f"saved snapshot price=${current_price:,.2f} dd={current_dd:.2f}% high_52w=${high_52w:,.2f} score={investment_score}"
                )
                progress(
                    50 + (40 * index / max(total_tickers, 1)),
                    f"Phase 4/5: market data/scoring | Ticker {index}/{total_tickers}: {ticker} complete",
                )
                
            except Exception as e:
                error(f"{ticker}: market/scoring step failed: {e}")
                progress(
                    50 + (40 * index / max(total_tickers, 1)),
                    f"Phase 4/5: market data/scoring | Ticker {index}/{total_tickers}: {ticker} errored",
                )

    conn.commit()
    progress(95, "Phase 4/5: market data/scoring complete")

    # Step 5: Record Pipeline Execution
    step("Step 5/5: writing pipeline run record")
    duration = round(time.time() - start_time, 2)
    summary_data = {
        "processed_tickers": processed_count,
        "sec_filings_updated": sec_filings_count,
        "financial_reports_10k_10q": financial_reports_count,
        "etf_reports_nport_ncen": etf_reports_count,
        "eight_k_debt_events": eightk_events_count,
        "duration_seconds": duration,
        "completed_at": now_str
    }

    cursor.execute("""
        INSERT INTO pipeline_runs (run_id, run_timestamp, duration_seconds, tickers_processed, status, summary_json)
        VALUES (?, ?, ?, ?, 'SUCCESS', ?)
    """, (run_id, now_str, duration, processed_count, json.dumps(summary_data)))

    conn.commit()
    conn.close()
    progress(100, "Phase 5/5: pipeline run recorded")

    banner(f"Pipeline run [{run_id}] completed")
    success(f"Processed tickers: {processed_count}")
    success(f"SEC Form 4 filings: {sec_filings_count}")
    success(f"10-K/10-Q reports: {financial_reports_count}")
    success(f"ETF N-PORT/N-CEN: {etf_reports_count}")
    success(f"8-K debt/bankruptcy events: {eightk_events_count}")
    success(f"Total execution: {duration}s")

if __name__ == "__main__":
    run_pipeline()
