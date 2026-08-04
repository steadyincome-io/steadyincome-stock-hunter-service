"""SQLite read helpers for the Streamlit dashboard."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator, Optional

import pandas as pd
import struct


DEFAULT_DB_PATH = os.getenv("DRAW_DOWN_DB_PATH", "drawdown_analyzer.db")


@contextmanager
def connect(db_path: str = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def fetch_dataframe(query: str, params: tuple = (), db_path: str = DEFAULT_DB_PATH) -> pd.DataFrame:
    with connect(db_path) as conn:
        return pd.read_sql_query(query, conn, params=params)


def fetch_one(query: str, params: tuple = (), db_path: str = DEFAULT_DB_PATH) -> Optional[sqlite3.Row]:
    with connect(db_path) as conn:
        cursor = conn.execute(query, params)
        return cursor.fetchone()


def parse_json_blob(blob):
    if not blob:
        return None
    if isinstance(blob, (list, dict)):
        return blob
    try:
        return json.loads(blob)
    except Exception:
        return None


def _coerce_sqlite_numeric(value):
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if isinstance(value, bytes):
        if len(value) == 8:
            try:
                return int.from_bytes(value, "little", signed=True)
            except Exception:
                try:
                    return struct.unpack("<d", value)[0]
                except Exception:
                    return None
        try:
            return value.decode("utf-8")
        except Exception:
            return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass
    return value


def _normalize_numeric_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if df.empty:
        return df
    cleaned = df.copy()
    for column in columns:
        if column in cleaned.columns:
            cleaned[column] = cleaned[column].map(_coerce_sqlite_numeric)
            cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")
    return cleaned


def get_summary_counts(db_path: str = DEFAULT_DB_PATH) -> dict:
    queries = {
        "active_tickers": "SELECT COUNT(*) FROM universe WHERE status = 'active'",
        "snapshot_rows": "SELECT COUNT(*) FROM daily_snapshot",
        "financial_rows": "SELECT COUNT(*) FROM sec_financials",
        "financial_rows_with_llm": """
            SELECT COUNT(*)
            FROM sec_financials
            WHERE risk_score IS NOT NULL
               OR risk_summary IS NOT NULL
               OR md_a_summary IS NOT NULL
               OR full_sentiment IS NOT NULL
               OR comprehensive_summary IS NOT NULL
        """,
        "etf_rows": "SELECT COUNT(*) FROM sec_etf_reports",
        "drawdown_tickers": "SELECT COUNT(*) FROM drawdown_summary",
        "distress_tickers": "SELECT COUNT(*) FROM distress_scores",
        "eight_k_events": "SELECT COUNT(*) FROM eight_k_events",
        "pipeline_runs": "SELECT COUNT(*) FROM pipeline_runs",
    }
    result = {}
    with connect(db_path) as conn:
        for key, query in queries.items():
            result[key] = conn.execute(query).fetchone()[0]
        latest_run = conn.execute(
            """
            SELECT run_id, run_timestamp, duration_seconds, tickers_processed, status
            FROM pipeline_runs
            ORDER BY run_timestamp DESC
            LIMIT 1
            """
        ).fetchone()
    result["latest_run"] = dict(latest_run) if latest_run else None
    return result


def list_active_tickers(db_path: str = DEFAULT_DB_PATH) -> pd.DataFrame:
    return fetch_dataframe(
        """
        SELECT ticker, name, asset_type, sector, industry, market_cap
        FROM universe
        WHERE status = 'active'
        ORDER BY asset_type, market_cap DESC, ticker
        """,
        db_path=db_path,
    )


def get_universe_table(db_path: str = DEFAULT_DB_PATH) -> pd.DataFrame:
    """One row per active ticker, joining live snapshot scores/valuation with
    drawdown-history summary stats -- built for sorting/screening across the
    whole universe rather than drilling into a single ticker."""
    df = fetch_dataframe(
        """
        SELECT
            u.ticker,
            u.name,
            u.asset_type,
            u.sector,
            u.market_cap,
            ds.price,
            ds.price_change_1d,
            ds.high_52w,
            ds.low_52w,
            ds.current_drawdown_pct,
            ds.max_drawdown_1y_pct,
            ds.pe_ratio,
            ds.dividend_yield_pct,
            ds.quality_score,
            ds.investment_score,
            ds.risk_score,
            ds.insider_sentiment_score,
            ds.drawdown_opportunity_score,
            ds.distress_risk_level,
            ds.valuation_tier,
            ds.investment_verdict,
            dsum.completed_drawdowns,
            dsum.avg_drawdown_pct,
            dsum.worst_drawdown_pct,
            dsum.avg_recovery_days,
            dsum.longest_recovery_days,
            ds.updated_at
        FROM universe u
        LEFT JOIN daily_snapshot ds ON ds.ticker = u.ticker
        LEFT JOIN drawdown_summary dsum ON dsum.ticker = u.ticker
        WHERE u.status = 'active'
        ORDER BY u.ticker
        """,
        db_path=db_path,
    )
    return _normalize_numeric_columns(
        df,
        [
            "market_cap", "price", "price_change_1d", "high_52w", "low_52w",
            "current_drawdown_pct", "max_drawdown_1y_pct", "pe_ratio", "dividend_yield_pct",
            "quality_score", "investment_score", "risk_score", "insider_sentiment_score",
            "drawdown_opportunity_score", "completed_drawdowns", "avg_drawdown_pct",
            "worst_drawdown_pct", "avg_recovery_days", "longest_recovery_days",
        ],
    )


def get_ticker_overview(ticker: str, db_path: str = DEFAULT_DB_PATH) -> pd.DataFrame:
    df = fetch_dataframe(
        """
        SELECT
            u.ticker,
            u.name,
            u.asset_type,
            u.sector,
            u.industry,
            u.market_cap,
            ds.price,
            ds.price_change_1d,
            ds.high_52w,
            ds.low_52w,
            ds.current_drawdown_pct,
            ds.max_drawdown_1y_pct,
            ds.pe_ratio,
            ds.forward_pe,
            ds.ev_ebitda,
            ds.fcf_yield_pct,
            ds.dividend_yield_pct,
            ds.quality_score,
            ds.investment_score,
            ds.risk_score,
            ds.distress_risk_level,
            ds.insider_sentiment_score,
            ds.drawdown_opportunity_score,
            ds.valuation_tier,
            ds.investment_verdict,
            ds.updated_at
        FROM universe u
        LEFT JOIN daily_snapshot ds ON ds.ticker = u.ticker
        WHERE u.ticker = ?
        """,
        (ticker,),
        db_path=db_path,
    )
    return _normalize_numeric_columns(
        df,
        [
            "market_cap",
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
            "investment_score",
            "risk_score",
            "insider_sentiment_score",
            "drawdown_opportunity_score",
        ],
    )


def get_drawdown_summary(ticker: str, db_path: str = DEFAULT_DB_PATH) -> Optional[dict]:
    row = fetch_one(
        """
        SELECT *
        FROM drawdown_summary
        WHERE ticker = ?
        """,
        (ticker,),
        db_path=db_path,
    )
    if not row:
        return None
    detail = dict(row)
    detail["recovery_probability"] = parse_json_blob(detail.get("recovery_probability_json")) or {}
    return detail


def get_drawdown_events(ticker: str, db_path: str = DEFAULT_DB_PATH, limit: int = 50) -> pd.DataFrame:
    return fetch_dataframe(
        """
        SELECT
            peak_date, peak_price, bottom_date, bottom_price, drawdown_pct,
            recovery_date, recovery_price, days_to_bottom, days_underwater,
            recovery_duration_days, is_ongoing
        FROM drawdown_events
        WHERE ticker = ?
        ORDER BY ABS(drawdown_pct) DESC
        LIMIT ?
        """,
        (ticker, limit),
        db_path=db_path,
    )


def get_distress_score(ticker: str, db_path: str = DEFAULT_DB_PATH) -> Optional[dict]:
    row = fetch_one(
        """
        SELECT *
        FROM distress_scores
        WHERE ticker = ?
        """,
        (ticker,),
        db_path=db_path,
    )
    if not row:
        return None
    detail = dict(row)
    detail["primary_drivers"] = parse_json_blob(detail.get("primary_drivers")) or []
    detail["warning_signals"] = parse_json_blob(detail.get("warning_signals")) or []
    return detail


def get_eightk_events(ticker: str, db_path: str = DEFAULT_DB_PATH, limit: int = 25) -> pd.DataFrame:
    return fetch_dataframe(
        """
        SELECT filing_date, item_codes, is_debt_related, is_bankruptcy_related, description, filing_url
        FROM eight_k_events
        WHERE ticker = ?
        ORDER BY filing_date DESC
        LIMIT ?
        """,
        (ticker, limit),
        db_path=db_path,
    )


def get_ticker_filings(ticker: str, db_path: str = DEFAULT_DB_PATH) -> pd.DataFrame:
    df = fetch_dataframe(
        """
        SELECT
            filing_date,
            period_end_date,
            form_type,
            accession_number,
            primary_doc_description,
            revenue_usd,
            net_income_usd,
            operating_income_usd,
            total_assets_usd,
            total_liabilities_usd,
            stockholders_equity_usd,
            total_debt_usd,
            debt_to_equity_ratio,
            eps_diluted,
            risk_score,
            risk_sentiment,
            full_sentiment,
            md_a_summary,
            risk_summary,
            comprehensive_summary,
            narrative_mda,
            narrative_risk_factors,
            narrative_legal,
            narrative_commitments,
            narrative_buybacks,
            narrative_liquidity,
            narrative_subsequent,
            report_url
        FROM sec_financials
        WHERE ticker = ?
        ORDER BY filing_date DESC, accession_number DESC
        """,
        (ticker,),
        db_path=db_path,
    )
    return _normalize_numeric_columns(
        df,
        [
            "revenue_usd",
            "net_income_usd",
            "operating_income_usd",
            "total_assets_usd",
            "total_liabilities_usd",
            "stockholders_equity_usd",
            "total_debt_usd",
            "debt_to_equity_ratio",
            "eps_diluted",
            "risk_score",
        ],
    )


def get_ticker_fundamentals_history(ticker: str, db_path: str = DEFAULT_DB_PATH) -> pd.DataFrame:
    df = fetch_dataframe(
        """
        SELECT
            filing_date,
            form_type,
            revenue_usd,
            net_income_usd,
            operating_income_usd,
            total_assets_usd,
            total_liabilities_usd,
            stockholders_equity_usd,
            total_debt_usd,
            debt_to_equity_ratio,
            eps_diluted
        FROM sec_financials
        WHERE ticker = ?
          AND form_type = '10-Q'
        ORDER BY filing_date ASC
        """,
        (ticker,),
        db_path=db_path,
    )
    return _normalize_numeric_columns(
        df,
        [
            "revenue_usd",
            "net_income_usd",
            "operating_income_usd",
            "total_assets_usd",
            "total_liabilities_usd",
            "stockholders_equity_usd",
            "total_debt_usd",
            "debt_to_equity_ratio",
            "eps_diluted",
        ],
    )


def get_insider_trades(ticker: str, db_path: str = DEFAULT_DB_PATH, limit: int = 200) -> pd.DataFrame:
    return fetch_dataframe(
        """
        SELECT
            filing_date,
            trade_date,
            insider_name,
            title,
            shares,
            code,
            transaction_type,
            price_per_share,
            total_value,
            sentiment
        FROM insider_trades
        WHERE ticker = ?
        ORDER BY filing_date DESC, trade_date DESC, id DESC
        LIMIT ?
        """,
        (ticker, limit),
        db_path=db_path,
    )


def get_etf_holdings(ticker: str, db_path: str = DEFAULT_DB_PATH, filing_date: str | None = None, limit: int = 200) -> pd.DataFrame:
    if filing_date is None:
        latest_row = fetch_one(
            """
            SELECT MAX(filing_date) AS filing_date
            FROM etf_holdings
            WHERE ticker = ?
            """,
            (ticker,),
            db_path=db_path,
        )
        filing_date = latest_row["filing_date"] if latest_row and latest_row["filing_date"] else None

    if not filing_date:
        return pd.DataFrame()

    return fetch_dataframe(
        """
        SELECT
            filing_date,
            holding_name,
            holding_ticker,
            cusip,
            isin,
            shares,
            market_value,
            weight_pct,
            asset_category,
            country,
            currency
        FROM etf_holdings
        WHERE ticker = ?
          AND filing_date = ?
        ORDER BY weight_pct DESC, market_value DESC, holding_name ASC
        LIMIT ?
        """,
        (ticker, filing_date, limit),
        db_path=db_path,
    )


def get_price_history(ticker: str, db_path: str = DEFAULT_DB_PATH, limit: int = 180) -> pd.DataFrame:
    return fetch_dataframe(
        """
        SELECT trade_date, close_price, volume
        FROM price_history
        WHERE ticker = ?
        ORDER BY trade_date ASC
        LIMIT ?
        """,
        (ticker, limit),
        db_path=db_path,
    )


def get_filing_detail(ticker: str, filing_date: str, accession_number: str, db_path: str = DEFAULT_DB_PATH) -> Optional[dict]:
    row = fetch_one(
        """
        SELECT *
        FROM sec_financials
        WHERE ticker = ? AND filing_date = ? AND accession_number = ?
        """,
        (ticker, filing_date, accession_number),
        db_path=db_path,
    )
    if not row:
        return None
    detail = dict(row)
    for column in (
        "revenue_usd",
        "net_income_usd",
        "operating_income_usd",
        "total_assets_usd",
        "total_liabilities_usd",
        "stockholders_equity_usd",
        "total_debt_usd",
        "debt_to_equity_ratio",
        "eps_diluted",
        "risk_score",
    ):
        if column in detail:
            value = _coerce_sqlite_numeric(detail[column])
            try:
                detail[column] = pd.to_numeric([value], errors="coerce")[0]
            except Exception:
                detail[column] = value
    return detail


def get_pipeline_run_detail(run_id: str, db_path: str = DEFAULT_DB_PATH) -> Optional[dict]:
    row = fetch_one(
        """
        SELECT run_id, run_timestamp, duration_seconds, tickers_processed, status, summary_json
        FROM pipeline_runs
        WHERE run_id = ?
        """,
        (run_id,),
        db_path=db_path,
    )
    return dict(row) if row else None


def get_pipeline_runs(db_path: str = DEFAULT_DB_PATH, limit: int = 25) -> pd.DataFrame:
    return fetch_dataframe(
        """
        SELECT run_id, run_timestamp, duration_seconds, tickers_processed, status, summary_json
        FROM pipeline_runs
        ORDER BY run_timestamp DESC
        LIMIT ?
        """,
        (limit,),
        db_path=db_path,
    )
