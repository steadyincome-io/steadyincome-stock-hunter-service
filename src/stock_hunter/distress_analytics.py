"""Financial-distress estimation (architecture doc section 13).

Computes an Altman Z-score and a best-effort Piotroski F-score from the
XBRL-derived fields already captured in `sec_financials`, then blends them
into a 0-100 distress risk score with probabilistic, non-absolute wording.
Never claims certainty about bankruptcy -- degrades to "Insufficient data"
when the required inputs are not available for a given ticker.
"""
import json

from .logger import info


def _safe_div(numerator, denominator):
    try:
        if numerator is None or denominator in (None, 0):
            return None
        return float(numerator) / float(denominator)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _latest_two_annual_filings(cursor, ticker):
    cursor.execute("""
        SELECT filing_date, revenue_usd, net_income_usd, operating_income_usd,
               total_assets_usd, total_liabilities_usd, stockholders_equity_usd,
               total_debt_usd, current_assets_usd, current_liabilities_usd,
               retained_earnings_usd, operating_cash_flow_usd, shares_outstanding
        FROM sec_financials
        WHERE ticker = ? AND form_type = '10-K'
        ORDER BY filing_date DESC
        LIMIT 2
    """, (ticker,))
    rows = cursor.fetchall()
    cols = [
        "filing_date", "revenue_usd", "net_income_usd", "operating_income_usd",
        "total_assets_usd", "total_liabilities_usd", "stockholders_equity_usd",
        "total_debt_usd", "current_assets_usd", "current_liabilities_usd",
        "retained_earnings_usd", "operating_cash_flow_usd", "shares_outstanding",
    ]
    return [dict(zip(cols, row)) for row in rows]


def compute_altman_z(current, market_cap_usd):
    total_assets = current.get("total_assets_usd")
    total_liabilities = current.get("total_liabilities_usd")
    if not total_assets or not total_liabilities:
        return None

    working_capital = None
    if current.get("current_assets_usd") is not None and current.get("current_liabilities_usd") is not None:
        working_capital = current["current_assets_usd"] - current["current_liabilities_usd"]

    x1 = _safe_div(working_capital, total_assets)
    x2 = _safe_div(current.get("retained_earnings_usd"), total_assets)
    x3 = _safe_div(current.get("operating_income_usd"), total_assets)
    x4 = _safe_div(market_cap_usd, total_liabilities)
    x5 = _safe_div(current.get("revenue_usd"), total_assets)

    components = [c for c in (x1, x2, x3, x4, x5) if c is not None]
    if len(components) < 3:
        return None

    z = (
        1.2 * (x1 or 0) + 1.4 * (x2 or 0) + 3.3 * (x3 or 0)
        + 0.6 * (x4 or 0) + 1.0 * (x5 or 0)
    )
    return round(z, 2)


def compute_piotroski_f(current, prior):
    """Best-effort 9-point Piotroski F-score. Criteria with missing inputs are
    skipped (neither awarded nor penalized); data_completeness reports how
    many of the 9 criteria could actually be evaluated."""
    points = 0
    evaluated = 0

    roa_cur = _safe_div(current.get("net_income_usd"), current.get("total_assets_usd"))
    roa_prior = _safe_div(prior.get("net_income_usd"), prior.get("total_assets_usd")) if prior else None

    checks = []

    # 1. Positive ROA
    if roa_cur is not None:
        checks.append(roa_cur > 0)

    # 2. Positive operating cash flow
    cfo_cur = current.get("operating_cash_flow_usd")
    if cfo_cur is not None:
        checks.append(cfo_cur > 0)

    # 3. Improving ROA
    if roa_cur is not None and roa_prior is not None:
        checks.append(roa_cur > roa_prior)

    # 4. Earnings quality: operating cash flow exceeds net income
    if cfo_cur is not None and current.get("net_income_usd") is not None:
        checks.append(cfo_cur > current["net_income_usd"])

    if prior:
        # 5. Decreasing leverage (total debt / total assets)
        lev_cur = _safe_div(current.get("total_debt_usd"), current.get("total_assets_usd"))
        lev_prior = _safe_div(prior.get("total_debt_usd"), prior.get("total_assets_usd"))
        if lev_cur is not None and lev_prior is not None:
            checks.append(lev_cur <= lev_prior)

        # 6. Improving current ratio
        cr_cur = _safe_div(current.get("current_assets_usd"), current.get("current_liabilities_usd"))
        cr_prior = _safe_div(prior.get("current_assets_usd"), prior.get("current_liabilities_usd"))
        if cr_cur is not None and cr_prior is not None:
            checks.append(cr_cur > cr_prior)

        # 7. No new share issuance
        shares_cur = current.get("shares_outstanding")
        shares_prior = prior.get("shares_outstanding")
        if shares_cur is not None and shares_prior is not None:
            checks.append(shares_cur <= shares_prior * 1.01)

        # 8. Improving operating margin (proxy for gross margin -- gross profit not captured)
        margin_cur = _safe_div(current.get("operating_income_usd"), current.get("revenue_usd"))
        margin_prior = _safe_div(prior.get("operating_income_usd"), prior.get("revenue_usd"))
        if margin_cur is not None and margin_prior is not None:
            checks.append(margin_cur > margin_prior)

        # 9. Improving asset turnover
        turn_cur = _safe_div(current.get("revenue_usd"), current.get("total_assets_usd"))
        turn_prior = _safe_div(prior.get("revenue_usd"), prior.get("total_assets_usd"))
        if turn_cur is not None and turn_prior is not None:
            checks.append(turn_cur > turn_prior)

    evaluated = len(checks)
    points = sum(1 for c in checks if c)
    if evaluated == 0:
        return None, 0.0
    # Scale to a 0-9 score even when fewer than 9 criteria were evaluable.
    scaled = round((points / evaluated) * 9)
    return scaled, round(evaluated / 9, 2)


def compute_distress(cursor, ticker, market_cap_usd=None, sector=None):
    """sector, when passed, is used only to decide whether Altman Z counts toward
    the risk score (see the is_financial_sector block below) -- it does not
    change which filings/fields are read."""
    filings = _latest_two_annual_filings(cursor, ticker)
    if not filings:
        return {
            "ticker": ticker,
            "altman_z": None,
            "piotroski_f": None,
            "distress_risk_score": None,
            "risk_level": "Insufficient data",
            "confidence": 0.0,
            "primary_drivers": [],
            "warning_signals": ["No 10-K financial data available"],
            "data_completeness": "0/9",
        }

    current = filings[0]
    prior = filings[1] if len(filings) > 1 else None

    altman_z = compute_altman_z(current, market_cap_usd)
    piotroski_f, f_completeness = compute_piotroski_f(current, prior)

    if altman_z is None and piotroski_f is None:
        return {
            "ticker": ticker,
            "altman_z": None,
            "piotroski_f": None,
            "distress_risk_score": None,
            "risk_level": "Insufficient data",
            "confidence": 0.0,
            "primary_drivers": [],
            "warning_signals": ["Insufficient balance-sheet detail to estimate distress risk"],
            "data_completeness": "0/9",
        }

    # Altman Z's x4 component (market_cap / total_liabilities) assumes a
    # non-financial capital structure -- heavy liabilities relative to market
    # cap is normal, healthy leverage for a bank/insurer (deposits, policy
    # obligations), not a solvency warning the way it would be for a
    # manufacturer. So for Financial-sector tickers, Altman Z is still
    # computed and reported for reference below, but excluded from the score
    # itself; the score leans on Piotroski F alone instead (base_risk stays
    # at the neutral 50 used when Altman Z can't be computed at all).
    is_financial_sector = (sector or "").strip().lower() == "financial"
    z_for_scoring = None if is_financial_sector else altman_z

    # Base risk from Altman Z zones (safe / grey / distress).
    if z_for_scoring is not None:
        if z_for_scoring > 2.99:
            base_risk = 15
        elif z_for_scoring >= 1.81:
            base_risk = 45
        else:
            base_risk = 80
    else:
        base_risk = 50

    # Piotroski F (0-9, higher = fundamentally stronger) shifts the score.
    if piotroski_f is not None:
        base_risk += (4.5 - piotroski_f) * 4

    risk_score = max(0, min(100, int(round(base_risk))))

    if risk_score <= 30:
        risk_level = "Low financial-distress risk"
    elif risk_score <= 60:
        risk_level = "Elevated financial-distress risk"
    else:
        risk_level = "Material solvency concerns"

    drivers = []
    warnings = []
    debt_to_assets = _safe_div(current.get("total_debt_usd"), current.get("total_assets_usd"))
    cfo = current.get("operating_cash_flow_usd")
    if debt_to_assets is not None:
        (drivers if debt_to_assets < 0.4 else warnings).append(
            f"Debt-to-assets ratio of {debt_to_assets:.2f}"
        )
    if cfo is not None:
        (drivers if cfo > 0 else warnings).append(
            "Positive operating cash flow" if cfo > 0 else "Negative operating cash flow"
        )
    if altman_z is not None:
        if is_financial_sector:
            warnings.append(
                f"Altman Z-score of {altman_z} (excluded from score -- unreliable for Financial-sector companies)"
            )
        else:
            (drivers if altman_z > 2.99 else warnings).append(f"Altman Z-score of {altman_z}")
    if piotroski_f is not None:
        (drivers if piotroski_f >= 6 else warnings).append(f"Piotroski F-score of {piotroski_f}/9")

    confidence = round(
        (0.5 if z_for_scoring is not None else 0.0) + (0.5 * f_completeness), 2
    )

    return {
        "ticker": ticker,
        "altman_z": altman_z,
        "piotroski_f": piotroski_f,
        "distress_risk_score": risk_score,
        "risk_level": risk_level,
        "confidence": confidence,
        "primary_drivers": drivers,
        "warning_signals": warnings,
        "data_completeness": f"{int(round(f_completeness * 9))}/9",
    }


def store_distress_score(cursor, result):
    cursor.execute("""
        INSERT INTO distress_scores (
            ticker, altman_z, piotroski_f, distress_risk_score, risk_level,
            confidence, primary_drivers, warning_signals, data_completeness, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(ticker) DO UPDATE SET
            altman_z=excluded.altman_z,
            piotroski_f=excluded.piotroski_f,
            distress_risk_score=excluded.distress_risk_score,
            risk_level=excluded.risk_level,
            confidence=excluded.confidence,
            primary_drivers=excluded.primary_drivers,
            warning_signals=excluded.warning_signals,
            data_completeness=excluded.data_completeness,
            updated_at=datetime('now')
    """, (
        result["ticker"], result["altman_z"], result["piotroski_f"],
        result["distress_risk_score"], result["risk_level"], result["confidence"],
        json.dumps(result["primary_drivers"]), json.dumps(result["warning_signals"]),
        result["data_completeness"],
    ))
