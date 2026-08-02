"""Read-only Streamlit dashboard for Drawdown Analyzer."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

try:
    from streamlit_autorefresh import st_autorefresh
except Exception:  # pragma: no cover - optional dependency
    st_autorefresh = None

# Make the repo root importable when Streamlit runs this file as a script.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from ui.db import (
    DEFAULT_DB_PATH,
    get_distress_score,
    get_drawdown_events,
    get_drawdown_summary,
    get_eightk_events,
    get_filing_detail,
    get_etf_holdings,
    get_pipeline_runs,
    get_pipeline_run_detail,
    get_price_history,
    get_insider_trades,
    get_summary_counts,
    get_ticker_fundamentals_history,
    get_ticker_filings,
    get_ticker_overview,
    list_active_tickers,
    parse_json_blob,
)


st.set_page_config(
    page_title="Drawdown Analyzer",
    page_icon="DA",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
        .stApp {
            background: radial-gradient(circle at top left, rgba(21, 128, 61, 0.10), transparent 30%),
                        radial-gradient(circle at top right, rgba(15, 23, 42, 0.10), transparent 28%),
                        linear-gradient(180deg, #f8fafc 0%, #f1f5f9 100%);
        }
        .block-container { padding-top: 1.2rem; padding-bottom: 1.8rem; }
        .metric-card {
            border: 1px solid rgba(15, 23, 42, 0.10);
            padding: 0.9rem 1rem;
            border-radius: 0.9rem;
            background: rgba(255, 255, 255, 0.82);
            box-shadow: 0 10px 30px rgba(15, 23, 42, 0.04);
        }
        .subtle { color: #64748b; font-size: 0.9rem; }
        .section-title { margin-top: 0.5rem; }
        .badge {
            display: inline-block;
            padding: 0.15rem 0.6rem;
            border-radius: 999px;
            font-size: 0.82rem;
            font-weight: 600;
        }
        .badge-low { background: rgba(21, 128, 61, 0.14); color: #166534; }
        .badge-moderate { background: rgba(202, 138, 4, 0.16); color: #854d0e; }
        .badge-high { background: rgba(220, 38, 38, 0.14); color: #991b1b; }
        .badge-neutral { background: rgba(100, 116, 139, 0.14); color: #334155; }
        .ticker-header {
            display: flex;
            align-items: baseline;
            gap: 0.6rem;
            flex-wrap: wrap;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


def _format_dt(value):
    if value in (None, "") or pd.isna(value):
        return "n/a"
    try:
        return datetime.fromisoformat(str(value)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(value)


def _format_pct(value):
    if value in (None, "") or pd.isna(value):
        return "n/a"
    try:
        return f"{float(value):.2f}%"
    except Exception:
        return str(value)


def _format_number(value, decimals=2):
    if value in (None, "") or pd.isna(value):
        return "n/a"
    try:
        return f"{float(value):,.{decimals}f}"
    except Exception:
        return str(value)


def _format_money_auto(value, decimals=1):
    if value in (None, "") or pd.isna(value):
        return "n/a"
    try:
        amount = float(value)
    except Exception:
        return str(value)

    abs_amount = abs(amount)
    if abs_amount >= 1_000_000_000_000:
        return f"${amount / 1_000_000_000_000:,.{decimals}f}T"
    if abs_amount >= 1_000_000_000:
        return f"${amount / 1_000_000_000:,.{decimals}f}B"
    if abs_amount >= 1_000_000:
        return f"${amount / 1_000_000:,.{decimals}f}M"
    if abs_amount >= 1_000:
        return f"${amount / 1_000:,.{decimals}f}K"
    return f"${amount:,.{decimals}f}"


def _format_ratio(value, decimals=2):
    if value in (None, "") or pd.isna(value):
        return "n/a"
    try:
        return f"{float(value):,.{decimals}f}x"
    except Exception:
        return str(value)


def _metric_card(label, value, delta=None):
    st.markdown("<div class='metric-card'>", unsafe_allow_html=True)
    st.metric(label=label, value=value, delta=delta)
    st.markdown("</div>", unsafe_allow_html=True)


def _format_money_frame(df: pd.DataFrame, columns: list[str], decimals: int = 1) -> pd.io.formats.style.Styler:
    if df.empty:
        return df.style

    def formatter(value):
        return _format_money_auto(value, decimals=decimals)

    fmt = {column: formatter for column in columns if column in df.columns}
    return df.style.format(fmt, na_rep="n/a")


def _section_header(title: str, subtitle: str | None = None):
    st.subheader(title)
    if subtitle:
        st.caption(subtitle)


def _badge_class_for_score(score, invert=False):
    """0-100 score -> badge tier. invert=True means higher is worse (e.g. risk)."""
    if score is None or pd.isna(score):
        return "badge-neutral"
    score = float(score)
    if invert:
        if score <= 35:
            return "badge-low"
        if score <= 65:
            return "badge-moderate"
        return "badge-high"
    if score >= 65:
        return "badge-low"
    if score >= 35:
        return "badge-moderate"
    return "badge-high"


def _badge_class_for_risk_level(level: str | None):
    if not level:
        return "badge-neutral"
    level = level.lower()
    if "low" in level:
        return "badge-low"
    if "elevated" in level or "moderate" in level:
        return "badge-moderate"
    if "material" in level or "high" in level:
        return "badge-high"
    return "badge-neutral"


def _badge(text, css_class):
    st.markdown(f"<span class='badge {css_class}'>{text}</span>", unsafe_allow_html=True)


def _normalize_db_path() -> str:
    sidebar_default = os.getenv("DRAW_DOWN_DB_PATH", DEFAULT_DB_PATH)
    return st.sidebar.text_input("SQLite DB path", value=sidebar_default)


def _configure_auto_refresh():
    st.sidebar.subheader("Auto refresh")
    enabled = st.sidebar.toggle("Enable auto refresh", value=False)
    interval_seconds = st.sidebar.slider("Refresh every (seconds)", min_value=5, max_value=120, value=15, step=5)
    if enabled and st_autorefresh is not None:
        st_autorefresh(interval=interval_seconds * 1000, key="drawdown_dashboard_autorefresh")
    elif enabled:
        st.sidebar.info("Install streamlit-autorefresh to enable automatic polling.")
    st.sidebar.caption("Use this while the backend pipeline is running.")


def _render_summary(summary: dict):
    cols = st.columns(8)
    metrics = [
        ("Active tickers", summary.get("active_tickers", 0)),
        ("Snapshot rows", summary.get("snapshot_rows", 0)),
        ("Financial rows", summary.get("financial_rows", 0)),
        ("Rows with LLM", summary.get("financial_rows_with_llm", 0)),
        ("ETF rows", summary.get("etf_rows", 0)),
        ("Drawdown-scored", summary.get("drawdown_tickers", 0)),
        ("Distress-scored", summary.get("distress_tickers", 0)),
        ("8-K debt events", summary.get("eight_k_events", 0)),
    ]
    for col, (label, value) in zip(cols, metrics):
        with col:
            _metric_card(label, f"{int(value):,}")

    latest = summary.get("latest_run") or {}
    if latest:
        st.caption(
            "Latest run: "
            f"{latest.get('run_id', 'n/a')} | "
            f"{_format_dt(latest.get('run_timestamp'))} | "
            f"status={latest.get('status', 'n/a')} | "
            f"tickers={latest.get('tickers_processed', 'n/a')} | "
            f"duration={_format_number(latest.get('duration_seconds'), 1)}s"
        )


def _render_header(row: dict):
    """Compact identity + verdict strip shown above every ticker's tabs."""
    left, right = st.columns([3, 2])
    with left:
        st.markdown(
            f"<div class='ticker-header'><h2>{row.get('ticker')}</h2>"
            f"<span class='subtle'>{row.get('name') or ''}</span></div>",
            unsafe_allow_html=True,
        )
        st.caption(
            f"{row.get('asset_type') or 'n/a'} | {row.get('sector') or 'n/a'} | "
            f"{row.get('industry') or 'n/a'} | Market cap: {_format_money_auto((row.get('market_cap') or 0) * 1e9)}"
        )
    with right:
        verdict = row.get("investment_verdict") or "n/a"
        tier = row.get("valuation_tier") or "n/a"
        st.markdown(f"**Verdict:** {verdict}  |  **Valuation:** {tier}")
        st.caption(f"Last updated: {_format_dt(row.get('updated_at'))}")

    has_snapshot = pd.notna(row.get("price")) or pd.notna(row.get("investment_score"))
    if not has_snapshot:
        st.info(
            "No daily_snapshot row exists for this ticker yet. "
            "Run the pipeline to populate live price, score, and valuation fields."
        )
        return

    price_cols = st.columns(4)
    price_cols[0].metric("Price", f"${_format_number(row.get('price'))}", delta=_format_pct(row.get("price_change_1d")))
    price_cols[1].metric("52W high", f"${_format_number(row.get('high_52w'))}")
    price_cols[2].metric("52W low", f"${_format_number(row.get('low_52w'))}")
    price_cols[3].metric("Current drawdown", _format_pct(row.get("current_drawdown_pct")))

    st.markdown("**Composite scores**")
    score_cols = st.columns(5)
    score_defs = [
        ("Investment", row.get("investment_score"), False),
        ("Quality", row.get("quality_score"), False),
        ("Risk", row.get("risk_score"), True),
        ("Drawdown opportunity", row.get("drawdown_opportunity_score"), False),
    ]
    if row.get("asset_type") == "Stock":
        score_defs.append(("Insider sentiment", row.get("insider_sentiment_score"), False))
    for col, (label, value, invert) in zip(score_cols, score_defs):
        with col:
            st.metric(label, _format_number(value, 0))
            _badge(
                "n/a" if value is None or pd.isna(value) else ("Strong" if not invert and value >= 65 else "Weak" if not invert and value < 35 else "Elevated" if invert and value >= 65 else "Contained" if invert and value < 35 else "Moderate"),
                _badge_class_for_score(value, invert=invert),
            )

    if row.get("asset_type") == "Stock" and row.get("distress_risk_level"):
        st.markdown("**Financial-distress read:**")
        _badge(row.get("distress_risk_level"), _badge_class_for_risk_level(row.get("distress_risk_level")))


def _render_drawdown_tab(ticker: str, db_path: str):
    _section_header("Drawdown & recovery history", "Every peak-to-trough-to-recovery episode found in stored price history.")

    price_history = get_price_history(ticker, db_path=db_path, limit=1500)
    if price_history.empty:
        st.info("No price history found for this ticker yet.")
        return

    chart_df = price_history.copy()
    chart_df["trade_date"] = pd.to_datetime(chart_df["trade_date"])
    chart_df = chart_df.set_index("trade_date")[["close_price"]]
    st.line_chart(chart_df, height=280)

    summary = get_drawdown_summary(ticker, db_path=db_path)
    if not summary:
        st.info("Drawdown summary has not been computed for this ticker yet.")
        return

    st.markdown("#### Summary")
    top_cols = st.columns(5)
    top_cols[0].metric("Current drawdown", _format_pct(summary.get("current_drawdown_pct")))
    top_cols[1].metric("Worst drawdown", _format_pct(summary.get("worst_drawdown_pct")))
    top_cols[2].metric("Average drawdown", _format_pct(summary.get("avg_drawdown_pct")))
    top_cols[3].metric("Median drawdown", _format_pct(summary.get("median_drawdown_pct")))
    top_cols[4].metric("Years of history", _format_number(summary.get("years_of_history"), 1))

    count_cols = st.columns(5)
    count_cols[0].metric("Completed drawdowns", summary.get("completed_drawdowns") or 0)
    count_cols[1].metric("> 10%", summary.get("drawdowns_over_10pct") or 0)
    count_cols[2].metric("> 20%", summary.get("drawdowns_over_20pct") or 0)
    count_cols[3].metric("> 30%", summary.get("drawdowns_over_30pct") or 0)
    count_cols[4].metric("> 40%", summary.get("drawdowns_over_40pct") or 0)

    recovery_cols = st.columns(2)
    recovery_cols[0].metric("Average recovery time", f"{_format_number(summary.get('avg_recovery_days'), 0)} days" if summary.get("avg_recovery_days") else "n/a")
    recovery_cols[1].metric("Longest recovery time", f"{summary.get('longest_recovery_days')} days" if summary.get("longest_recovery_days") else "n/a")

    recovery_probability = summary.get("recovery_probability") or {}
    if recovery_probability:
        st.markdown("#### Recovery probability by holding period")
        st.caption("Share of past completed drawdowns that had fully recovered within each horizon.")
        prob_df = pd.DataFrame(
            [{"Horizon": k.replace("_", " "), "Recovery probability": v} for k, v in recovery_probability.items()]
        )
        prob_df["Recovery probability"] = prob_df["Recovery probability"].map(lambda v: f"{v * 100:.0f}%")
        st.dataframe(prob_df, use_container_width=True, hide_index=True)

    st.markdown("#### Largest drawdown episodes")
    events = get_drawdown_events(ticker, db_path=db_path, limit=25)
    if events.empty:
        st.info("No individual drawdown episodes stored yet.")
        return
    events_display = events.rename(
        columns={
            "peak_date": "Peak date",
            "peak_price": "Peak price",
            "bottom_date": "Bottom date",
            "bottom_price": "Bottom price",
            "drawdown_pct": "Drawdown %",
            "recovery_date": "Recovery date",
            "recovery_price": "Recovery price",
            "days_to_bottom": "Days to bottom",
            "days_underwater": "Days underwater",
            "recovery_duration_days": "Recovery days",
            "is_ongoing": "Ongoing",
        }
    ).copy()
    events_display["Ongoing"] = events_display["Ongoing"].map({1: "Yes", 0: "No"})
    events_display["Recovery date"] = events_display["Recovery date"].fillna("Not yet recovered")
    st.dataframe(events_display, use_container_width=True, hide_index=True)


def _render_scores_and_risk_tab(ticker: str, db_path: str, row: dict):
    _section_header("Financial-distress model", "Altman Z-score and a best-effort Piotroski F-score, blended into a probabilistic risk read.")
    distress = get_distress_score(ticker, db_path=db_path)
    if not distress:
        st.info("No distress score computed for this ticker yet.")
    else:
        d_cols = st.columns(4)
        d_cols[0].metric("Altman Z-score", _format_number(distress.get("altman_z"), 2) if distress.get("altman_z") is not None else "n/a")
        d_cols[1].metric("Piotroski F-score", f"{distress.get('piotroski_f')}/9" if distress.get("piotroski_f") is not None else "n/a")
        d_cols[2].metric("Distress risk score", distress.get("distress_risk_score") if distress.get("distress_risk_score") is not None else "n/a")
        d_cols[3].metric("Confidence", _format_number(distress.get("confidence"), 2))
        _badge(distress.get("risk_level") or "Insufficient data", _badge_class_for_risk_level(distress.get("risk_level")))
        st.caption(f"Data completeness: {distress.get('data_completeness') or 'n/a'} Piotroski criteria evaluable")
        if distress.get("altman_z") is not None and (row.get("sector") or "").lower() in ("financial",):
            st.warning(
                "Altman Z-score is designed for non-financial firms and reads structurally low for "
                "banks/insurers due to their balance-sheet structure. Treat it as one signal, not a verdict."
            )

        driver_cols = st.columns(2)
        with driver_cols[0]:
            st.markdown("**Supporting factors**")
            drivers = distress.get("primary_drivers") or []
            if drivers:
                for d in drivers:
                    st.write(f"- {d}")
            else:
                st.caption("None recorded.")
        with driver_cols[1]:
            st.markdown("**Warning signals**")
            warnings_list = distress.get("warning_signals") or []
            if warnings_list:
                for w in warnings_list:
                    st.write(f"- {w}")
            else:
                st.caption("None recorded.")

    st.divider()
    _section_header("Composite score inputs", "How the risk/quality/investment scores on the overview strip are weighted.")
    st.caption(
        "Risk score blends: balance-sheet risk (distress model), liquidity, earnings stability, cash-flow risk, "
        "filing risk factors, legal/regulatory sentiment, drawdown severity, and insider selling. "
        "Quality score blends: revenue growth, margins, FCF quality, balance-sheet strength, ROA, and dividend quality. "
        "Investment score blends quality, valuation, inverted risk, drawdown opportunity, dividend quality, and insider sentiment."
    )


def _render_fundamentals_tab(ticker: str, db_path: str, filings: pd.DataFrame):
    _section_header("Numeric fundamentals", "SEC companyfacts fields stored on each filing row.")
    if filings.empty:
        st.info("No filing rows found for this ticker.")
        return

    latest_numeric = filings.iloc[0].to_dict()
    numeric_cols = st.columns(4)
    numeric_cols[0].metric("Revenue", _format_money_auto(latest_numeric.get("revenue_usd")))
    numeric_cols[1].metric("Net income", _format_money_auto(latest_numeric.get("net_income_usd")))
    numeric_cols[2].metric("Operating income", _format_money_auto(latest_numeric.get("operating_income_usd")))
    numeric_cols[3].metric("EPS diluted", _format_number(latest_numeric.get("eps_diluted"), 3))

    numeric_cols_2 = st.columns(4)
    numeric_cols_2[0].metric("Assets", _format_money_auto(latest_numeric.get("total_assets_usd")))
    numeric_cols_2[1].metric("Liabilities", _format_money_auto(latest_numeric.get("total_liabilities_usd")))
    numeric_cols_2[2].metric("Equity", _format_money_auto(latest_numeric.get("stockholders_equity_usd")))
    numeric_cols_2[3].metric("Total debt", _format_money_auto(latest_numeric.get("total_debt_usd")))

    debt_ratio_cols = st.columns(2)
    debt_ratio_cols[0].metric("Debt / equity", _format_ratio(latest_numeric.get("debt_to_equity_ratio")))
    debt_ratio_cols[1].metric(
        "Debt risk",
        "Elevated" if (latest_numeric.get("debt_to_equity_ratio") or 0) > 2 else "Normal",
    )

    fundamentals_history = get_ticker_fundamentals_history(ticker, db_path=db_path)
    if fundamentals_history.empty:
        st.info("No 10-Q numeric fundamentals found for this ticker.")
        return

    trend_df = fundamentals_history.copy()
    trend_df["filing_date"] = pd.to_datetime(trend_df["filing_date"])
    trend_df = trend_df.set_index("filing_date")
    revenue_chart = trend_df[["revenue_usd", "net_income_usd", "operating_income_usd", "eps_diluted"]].copy()
    st.markdown("#### Revenue, income, and EPS trend")
    st.caption("All values come from SEC companyfacts and are shown in raw numeric form for charting.")
    st.line_chart(revenue_chart, height=260)

    debt_chart = trend_df[["total_debt_usd", "debt_to_equity_ratio"]].copy()
    st.markdown("#### Debt trend")
    st.line_chart(debt_chart, height=220)

    if len(fundamentals_history) >= 2:
        latest_debt = fundamentals_history.iloc[-1].get("total_debt_usd")
        prior_debt = fundamentals_history.iloc[-2].get("total_debt_usd")
        latest_ratio = fundamentals_history.iloc[-1].get("debt_to_equity_ratio")
        if latest_debt is not None and prior_debt is not None and latest_debt > prior_debt:
            st.warning(
                "Debt is increasing across the latest 10-Q filings. "
                f"Latest debt-to-equity ratio: {_format_ratio(latest_ratio)}"
            )

    with st.expander("10-Q numeric filings table", expanded=False):
        table_df = fundamentals_history[
            [
                "filing_date", "form_type", "revenue_usd", "net_income_usd", "operating_income_usd",
                "total_assets_usd", "total_liabilities_usd", "stockholders_equity_usd",
                "total_debt_usd", "debt_to_equity_ratio", "eps_diluted",
            ]
        ].rename(
            columns={
                "filing_date": "Filing date", "form_type": "Form", "revenue_usd": "Revenue",
                "net_income_usd": "Net income", "operating_income_usd": "Operating income",
                "total_assets_usd": "Assets", "total_liabilities_usd": "Liabilities",
                "stockholders_equity_usd": "Equity", "total_debt_usd": "Debt",
                "debt_to_equity_ratio": "Debt / equity", "eps_diluted": "EPS diluted",
            }
        )
        st.caption("10-Q rows are ordered oldest to newest so the trend direction is easier to read.")
        st.dataframe(
            _format_money_frame(
                table_df,
                ["Revenue", "Net income", "Operating income", "Assets", "Liabilities", "Equity", "Debt"],
            ).format({"Debt / equity": "{:.2f}x", "EPS diluted": "{:.3f}"}),
            use_container_width=True,
            hide_index=True,
        )


def _render_filings_narrative_tab(ticker: str, db_path: str, filings: pd.DataFrame):
    _section_header("Filing history", "Recent SEC filings stored for this ticker.")
    if filings.empty:
        st.info("No filing rows found for this ticker.")
        return

    display_df = filings[
        ["filing_date", "form_type", "accession_number", "risk_score", "risk_sentiment", "full_sentiment", "md_a_summary", "risk_summary"]
    ].rename(
        columns={
            "filing_date": "Filing date", "form_type": "Form", "accession_number": "Accession",
            "risk_score": "Risk score", "risk_sentiment": "Risk sentiment",
            "full_sentiment": "Overall sentiment", "md_a_summary": "MD&A summary", "risk_summary": "Risk summary",
        }
    )
    st.caption("Each row is one filing. Scores are LLM-derived and only computed for the latest 10-K per ticker.")
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    st.divider()
    filing_options = [f"{r.filing_date} | {r.form_type} | {r.accession_number}" for r in filings.itertuples(index=False)]
    selection = st.selectbox("Select filing", filing_options)
    selected = filings.iloc[filing_options.index(selection)].to_dict()
    detail = get_filing_detail(ticker, str(selected["filing_date"]), str(selected["accession_number"]), db_path=db_path)
    if not detail:
        st.warning("Selected filing row could not be loaded.")
        return

    st.subheader("Filing detail")
    left, right = st.columns(2)
    left.write(f"**Filing date:** {detail.get('filing_date')}")
    left.write(f"**Period end:** {detail.get('period_end_date') or 'n/a'}")
    left.write(f"**Form type:** {detail.get('form_type')}")
    left.write(f"**Accession:** {detail.get('accession_number')}")
    right.write(f"**Risk score:** {detail.get('risk_score') or 'n/a'}")
    right.write(f"**Risk sentiment:** {detail.get('risk_sentiment') or 'n/a'}")
    right.write(f"**Full sentiment:** {detail.get('full_sentiment') or 'n/a'}")
    right.write(f"**Report URL:** {detail.get('report_url') or 'n/a'}")

    narrative_keys = [
        ("MD&A", "narrative_mda"), ("Risk factors", "narrative_risk_factors"), ("Legal", "narrative_legal"),
        ("Commitments", "narrative_commitments"), ("Buybacks", "narrative_buybacks"),
        ("Liquidity", "narrative_liquidity"), ("Subsequent", "narrative_subsequent"),
    ]
    tabs = st.tabs([label for label, _ in narrative_keys])
    for tab, (label, key) in zip(tabs, narrative_keys):
        with tab:
            st.caption(f"Extracted section: {label}")
            blob = parse_json_blob(detail.get(key))
            if not blob:
                st.info("No extracted narrative for this section.")
                continue
            st.caption(f"chars: {blob.get('char_count', 0)}")
            st.text_area(label, blob.get("text", ""), height=220)

    with st.expander("LLM outputs", expanded=False):
        comp = parse_json_blob(detail.get("comprehensive_summary"))
        st.write(f"**Risk summary:** {detail.get('risk_summary') or 'n/a'}")
        st.write(f"**MD&A summary:** {detail.get('md_a_summary') or 'n/a'}")
        st.write(f"**Comprehensive summary:** {json.dumps(comp or [], indent=2)}")


def _render_insider_and_8k_tab(ticker: str, db_path: str, row: dict):
    _section_header("Insider sentiment", "Form 4 transactions, weighted so open-market buys/sells dominate over grants, exercises, tax withholding, and gifts.")
    insider_score = row.get("insider_sentiment_score")
    if insider_score is not None and not pd.isna(insider_score):
        cols = st.columns(2)
        cols[0].metric("Insider sentiment score (0-100)", _format_number(insider_score, 0))
        label = "Net buying" if insider_score > 55 else ("Net selling" if insider_score < 45 else "Neutral")
        with cols[1]:
            _badge(label, "badge-low" if insider_score > 55 else ("badge-high" if insider_score < 45 else "badge-neutral"))
        st.caption("Trailing 180-day window. 50 = no meaningful open-market signal.")

    insider_trades = get_insider_trades(ticker, db_path=db_path)
    if insider_trades.empty:
        st.info("No insider trades found for this ticker.")
    else:
        display_trades = insider_trades[
            ["filing_date", "trade_date", "insider_name", "title", "transaction_type", "shares", "price_per_share", "total_value", "sentiment", "code"]
        ].rename(
            columns={
                "filing_date": "Filing date", "trade_date": "Trade date", "insider_name": "Insider",
                "title": "Title", "transaction_type": "Transaction", "shares": "Shares",
                "price_per_share": "Price / share", "total_value": "Total value", "sentiment": "Sentiment", "code": "Code",
            }
        )
        st.caption("Form 4 rows are sorted newest first. Codes P/S (open-market) carry the most sentiment weight.")
        st.dataframe(display_trades, use_container_width=True, hide_index=True)

    st.divider()
    _section_header("8-K debt & bankruptcy events", "Material events with item codes indicating new debt, covenant triggers, refinancing, or bankruptcy.")
    eightk = get_eightk_events(ticker, db_path=db_path)
    if eightk.empty:
        st.info("No debt/bankruptcy-relevant 8-K events found in the trailing window.")
        return
    eightk_display = eightk.rename(
        columns={
            "filing_date": "Filing date", "item_codes": "Item codes", "is_debt_related": "Debt-related",
            "is_bankruptcy_related": "Bankruptcy-related", "description": "Description", "filing_url": "Filing URL",
        }
    ).copy()
    eightk_display["Debt-related"] = eightk_display["Debt-related"].map({1: "Yes", 0: "No"})
    eightk_display["Bankruptcy-related"] = eightk_display["Bankruptcy-related"].map({1: "Yes", 0: "No"})
    if eightk_display["Bankruptcy-related"].eq("Yes").any():
        st.error("At least one bankruptcy-related 8-K (Item 1.03) was filed in the trailing window.")
    st.dataframe(eightk_display, use_container_width=True, hide_index=True)


def _render_etf_holdings_tab(ticker: str, db_path: str):
    _section_header("ETF holdings", "Latest parsed N-PORT holdings for the selected ETF.")
    holdings = get_etf_holdings(ticker, db_path=db_path)
    if holdings.empty:
        st.info("No ETF holdings were stored for this ticker yet.")
        return
    latest_filing_date = holdings.iloc[0].get("filing_date")
    st.caption(f"Latest holdings filing date: {latest_filing_date} | {len(holdings)} holdings shown")
    holdings_display = holdings.rename(
        columns={
            "filing_date": "Filing date", "holding_name": "Holding", "holding_ticker": "Ticker",
            "cusip": "CUSIP", "isin": "ISIN", "shares": "Shares", "market_value": "Market value",
            "weight_pct": "Weight %", "asset_category": "Asset category", "country": "Country", "currency": "Currency",
        }
    ).copy()
    holdings_display["Market value"] = holdings_display["Market value"].map(_format_money_auto)
    holdings_display["Weight %"] = holdings_display["Weight %"].map(_format_pct)
    st.dataframe(holdings_display, use_container_width=True, hide_index=True)


def _render_ticker_overview(db_path: str, ticker: str):
    overview = get_ticker_overview(ticker, db_path=db_path)
    if overview.empty:
        st.warning("No ticker metadata found.")
        return
    row = overview.iloc[0].to_dict()
    asset_type = row.get("asset_type")

    _render_header(row)
    st.divider()

    if asset_type == "Stock":
        tab_labels = ["Drawdown & Recovery", "Scores & Risk", "Fundamentals", "Filings & Narrative", "Insider & 8-K"]
    else:
        tab_labels = ["Drawdown & Recovery", "Holdings"]

    tabs = st.tabs(tab_labels)
    filings = get_ticker_filings(ticker, db_path=db_path) if asset_type == "Stock" else pd.DataFrame()

    with tabs[0]:
        _render_drawdown_tab(ticker, db_path)

    if asset_type == "Stock":
        with tabs[1]:
            _render_scores_and_risk_tab(ticker, db_path, row)
        with tabs[2]:
            _render_fundamentals_tab(ticker, db_path, filings)
        with tabs[3]:
            _render_filings_narrative_tab(ticker, db_path, filings)
        with tabs[4]:
            _render_insider_and_8k_tab(ticker, db_path, row)
    else:
        with tabs[1]:
            _render_etf_holdings_tab(ticker, db_path)


def _render_pipeline_runs(db_path: str):
    runs = get_pipeline_runs(db_path=db_path, limit=25)
    _section_header("Pipeline run history", "Latest executions recorded in SQLite.")
    if runs.empty:
        st.info("No pipeline run records found.")
        return
    summary_cols = ["run_timestamp", "duration_seconds", "tickers_processed", "status", "run_id"]
    available_cols = [column for column in summary_cols if column in runs.columns]
    st.caption("Each row is a pipeline execution. Open an expander for the stored JSON summary.")
    st.dataframe(
        runs[available_cols].rename(
            columns={
                "run_timestamp": "Run timestamp",
                "duration_seconds": "Duration (s)",
                "tickers_processed": "Tickers processed",
                "status": "Status",
                "run_id": "Run ID",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )
    st.caption("Recent run summaries")
    for run in runs.head(5).itertuples(index=False):
        title = f"{run.run_timestamp} | {run.status} | {run.run_id}"
        with st.expander(title, expanded=False):
            st.write(f"**Duration:** {_format_number(run.duration_seconds, 1)}s")
            st.write(f"**Tickers processed:** {run.tickers_processed}")
            try:
                summary = json.loads(run.summary_json) if run.summary_json else {}
            except Exception:
                summary = {"summary_json": run.summary_json}
            st.json(summary)

    st.divider()
    selected_run_id = st.selectbox(
        "Inspect a run",
        runs["run_id"].tolist(),
        index=0,
        help="Choose a pipeline execution to inspect the stored summary payload.",
    )
    detail = get_pipeline_run_detail(selected_run_id, db_path=db_path)
    if not detail:
        st.warning("Run details could not be loaded.")
        return

    detail_cols = st.columns(4)
    detail_cols[0].metric("Status", detail.get("status") or "n/a")
    detail_cols[1].metric("Tickers", detail.get("tickers_processed") or 0)
    detail_cols[2].metric("Duration", f"{_format_number(detail.get('duration_seconds'), 1)}s")
    detail_cols[3].metric("Run ID", detail.get("run_id") or "n/a")

    st.write(f"**Timestamp:** {_format_dt(detail.get('run_timestamp'))}")
    try:
        summary = json.loads(detail.get("summary_json")) if detail.get("summary_json") else {}
    except Exception:
        summary = {"summary_json": detail.get("summary_json")}
    st.json(summary)


def main():
    st.title("Drawdown Analyzer Dashboard")
    st.caption("Read-only local dashboard backed directly by SQLite.")

    db_path = _normalize_db_path()
    _configure_auto_refresh()
    if not os.path.exists(db_path):
        st.error(f"Database not found: {db_path}")
        st.stop()

    summary = get_summary_counts(db_path=db_path)
    _render_summary(summary)

    st.divider()
    active = list_active_tickers(db_path=db_path)
    if active.empty:
        st.warning("No active tickers available.")
        st.stop()

    search = st.sidebar.text_input("Search ticker or name", value="")
    asset_filter = st.sidebar.radio("Asset type", ["All", "Stock", "ETF"], horizontal=True)
    filtered = active
    if asset_filter != "All":
        filtered = filtered[filtered["asset_type"] == asset_filter]
    if search.strip():
        term = search.strip().lower()
        filtered = filtered[
            filtered["ticker"].str.lower().str.contains(term)
            | filtered["name"].str.lower().str.contains(term)
        ]

    sidebar_choices = filtered["ticker"].tolist()
    if not sidebar_choices:
        st.sidebar.warning("No tickers match your filters.")
        ticker = active.iloc[0]["ticker"]
    else:
        ticker = st.sidebar.selectbox(
            "Ticker",
            sidebar_choices,
            index=0,
        )

    with st.sidebar:
        st.divider()
        st.write("Read-only mode")
        st.write("Source: SQLite")
        st.write(f"DB: {db_path}")

    overview_tab, runs_tab = st.tabs(["Ticker overview", "Run history"])

    with overview_tab:
        _render_ticker_overview(db_path, ticker)

    with runs_tab:
        _render_pipeline_runs(db_path)


if __name__ == "__main__":
    main()
