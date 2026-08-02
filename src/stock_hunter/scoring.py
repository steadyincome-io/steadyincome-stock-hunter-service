"""Composite risk, quality, and investment scoring (architecture doc sections 14, 15, 17).

Blends live valuation inputs (already fetched in pipeline.py from yfinance)
with the SEC-derived signals collected elsewhere in the pipeline: LLM filing
risk scores, financial-distress models, drawdown history, 8-K debt/bankruptcy
events, and Form 4 insider sentiment.
"""
from datetime import datetime, timedelta

# Transaction-code weights: open-market purchases/sales carry far more signal
# than grants, option exercises, tax withholding, or gifts.
_CODE_WEIGHTS = {"P": 3.0, "S": 3.0, "A": 0.3, "M": 0.3, "F": 0.1, "G": 0.1}
_DEFAULT_CODE_WEIGHT = 0.5


def _clamp(value, lo=0, hi=100):
    return max(lo, min(hi, value))


def compute_insider_sentiment_score(cursor, ticker, days_back=180):
    """0-100 insider sentiment score. 50 = neutral/no signal, >50 = net open-market
    buying, <50 = net open-market selling. Weighted so P/S dominate over A/M/F/G."""
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    cursor.execute("""
        SELECT code, transaction_type, total_value
        FROM insider_trades
        WHERE ticker = ? AND filing_date >= ?
    """, (ticker, cutoff))
    rows = cursor.fetchall()
    if not rows:
        return 50, {"trade_count": 0}

    signed_total = 0.0
    weighted_abs_total = 0.0
    open_market_count = 0
    for code, transaction_type, total_value in rows:
        value = float(total_value or 0)
        weight = _CODE_WEIGHTS.get((code or "").upper(), _DEFAULT_CODE_WEIGHT)
        direction = 1.0 if transaction_type == "Purchase" else -1.0
        signed_total += value * weight * direction
        weighted_abs_total += value * weight
        if (code or "").upper() in ("P", "S"):
            open_market_count += 1

    if weighted_abs_total <= 0:
        return 50, {"trade_count": len(rows), "open_market_count": open_market_count}

    ratio = signed_total / weighted_abs_total  # -1..1
    score = _clamp(int(round(50 + 50 * ratio)))
    return score, {"trade_count": len(rows), "open_market_count": open_market_count}


def get_recent_eightk_flags(cursor, ticker, days_back=180):
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    cursor.execute("""
        SELECT is_debt_related, is_bankruptcy_related
        FROM eight_k_events
        WHERE ticker = ? AND filing_date >= ?
    """, (ticker, cutoff))
    rows = cursor.fetchall()
    return {
        "debt_event_count": sum(1 for r in rows if r[0]),
        "bankruptcy_event_count": sum(1 for r in rows if r[1]),
    }


def compute_risk_score(*, distress=None, current_ratio=None, revenue_volatility_pct=None,
                        operating_cash_flow=None, narrative_risk_score=None,
                        legal_sentiment=None, drawdown_summary=None,
                        insider_sentiment_score=None, eightk_flags=None):
    """Doc section 14: weighted composite, 0-100, higher = riskier."""
    balance_sheet_risk = distress.get("distress_risk_score") if distress else None
    if balance_sheet_risk is None:
        balance_sheet_risk = 50

    if current_ratio is not None:
        liquidity_risk = 20 if current_ratio >= 1.5 else (50 if current_ratio >= 1.0 else 80)
    else:
        liquidity_risk = 50

    if revenue_volatility_pct is not None:
        earnings_stability_risk = _clamp(int(revenue_volatility_pct * 3))
    else:
        earnings_stability_risk = 50

    if operating_cash_flow is not None:
        cash_flow_risk = 20 if operating_cash_flow > 0 else 80
    else:
        cash_flow_risk = 50

    filing_risk = narrative_risk_score if narrative_risk_score is not None else 50

    if legal_sentiment == "negative":
        legal_risk = 70
    elif legal_sentiment == "positive":
        legal_risk = 30
    else:
        legal_risk = 50

    current_dd = abs((drawdown_summary or {}).get("current_drawdown_pct") or 0)
    drawdown_severity_risk = _clamp(int(current_dd * 2))

    if insider_sentiment_score is not None:
        insider_selling_risk = _clamp(100 - insider_sentiment_score)
    else:
        insider_selling_risk = 50

    score = (
        0.20 * balance_sheet_risk
        + 0.15 * liquidity_risk
        + 0.10 * earnings_stability_risk
        + 0.15 * cash_flow_risk
        + 0.15 * filing_risk
        + 0.10 * legal_risk
        + 0.10 * drawdown_severity_risk
        + 0.05 * insider_selling_risk
    )

    # 8-K bankruptcy/debt-covenant events are hard override signals, not just a weighted input.
    flags = eightk_flags or {}
    if flags.get("bankruptcy_event_count"):
        score = max(score, 85)
    elif flags.get("debt_event_count"):
        score = max(score, score + 8)

    return _clamp(int(round(score)))


def compute_quality_score(*, revenue_growth_pct=None, net_margin_pct=None,
                           ocf_margin_pct=None, operating_margin_pct=None,
                           debt_to_equity=None, roa_pct=None,
                           dividend_yield_pct=None):
    """Doc section 15: weighted composite, 0-100, higher = higher quality."""
    def scale(value, low, high, lo_score=20, hi_score=90):
        if value is None:
            return 50
        if value <= low:
            return lo_score
        if value >= high:
            return hi_score
        return lo_score + (value - low) / (high - low) * (hi_score - lo_score)

    revenue_growth_stability = scale(revenue_growth_pct, -5, 20)
    eps_quality = scale(net_margin_pct, 0, 25)
    fcf_quality = scale(ocf_margin_pct, 0, 25)
    profitability = scale(operating_margin_pct, 0, 30)
    balance_sheet_strength = 90 if debt_to_equity is not None and debt_to_equity < 0.5 else (
        20 if debt_to_equity is not None and debt_to_equity > 2.5 else 55
    )
    roic_efficiency = scale(roa_pct, 0, 20)
    dividend_buyback_quality = scale(dividend_yield_pct, 0, 4)

    score = (
        0.15 * revenue_growth_stability
        + 0.15 * eps_quality
        + 0.20 * fcf_quality
        + 0.15 * profitability
        + 0.15 * balance_sheet_strength
        + 0.10 * roic_efficiency
        + 0.10 * dividend_buyback_quality
    )
    return _clamp(int(round(score)))


def compute_investment_score(*, quality_score=50, valuation_score=50, risk_score=50,
                              filing_risk_score=None, drawdown_opportunity_score=50,
                              dividend_score=50, insider_sentiment_score=50):
    """Doc section 17: weighted composite blending quality, valuation, and risk."""
    filing_risk = filing_risk_score if filing_risk_score is not None else risk_score
    score = (
        0.30 * quality_score
        + 0.20 * valuation_score
        + 0.15 * (100 - risk_score)
        + 0.10 * (100 - filing_risk)
        + 0.10 * drawdown_opportunity_score
        + 0.10 * dividend_score
        + 0.05 * insider_sentiment_score
    )
    return _clamp(int(round(score)))
