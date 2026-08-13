"""Growth-adjusted DCF fair-value estimate (architecture-doc-style module,
same conventions as distress_analytics.py).

Built entirely from SEC-sourced fundamentals already ingested by
sec_financials_worker.py (revenue history, free cash flow, debt, cash,
shares outstanding) plus live beta from yfinance -- the one input with no
SEC substitute. See pipeline.py's _load_latest_financials_map() for where
these get assembled per ticker.

Deliberately outputs a low/base/high 3-point sensitivity range, never a
single confident price target. The prompt that motivated this module named
the exact failure mode being guarded against: Wall Street analysts can
quietly nudge the discount rate down or bake in optimistic, uninterrupted
growth to justify a predetermined target. A fixed, documented sensitivity
grid (WACC_SENSITIVITY_DELTA / GROWTH_SENSITIVITY_DELTA below) makes that
kind of after-the-fact tuning visible instead of hidden inside one number.

This is a screening aid, not a valuation guarantee -- see compute_wacc's and
compute_base_growth_rate's own docstrings for exactly which inputs can make
a given ticker return None (insufficient data) rather than a fabricated
estimate.
"""

# ---- CAPM / WACC assumptions ------------------------------------------------
# Approximate short-term risk-free rate, same status/value as
# premium_screener.py's RISK_FREE_RATE -- kept as its own constant here
# rather than imported, since these are two independently-documented uses
# (Black-Scholes there, CAPM here) of the same real-world figure, not a
# shared dependency between the two modules.
RISK_FREE_RATE = 0.045
# Long-run US equity risk premium -- a standard, widely-cited range is
# 4.5%-5.5%; 5.0% is the textbook midpoint, not fitted to any particular
# name. A macro constant, not something fetched per-ticker.
EQUITY_RISK_PREMIUM = 0.05
# Flat assumed pre-tax cost of debt (roughly a typical investment-grade-to-
# mid-BBB corporate bond yield). sec_financials has no interest-expense
# field to derive a genuine per-company cost of debt from, so this is a
# documented judgment call, not a computed figure.
PRETAX_COST_OF_DEBT = 0.055
# US federal statutory corporate rate -- used only to tax-shield the assumed
# cost of debt above, not each company's actual effective tax rate (which
# sec_financials also doesn't capture).
ASSUMED_TAX_RATE = 0.21

# ---- growth/projection assumptions -----------------------------------------
# Years of explicit cash-flow projection before switching to a terminal
# value -- long enough to let a fast-growing name fade toward a normal
# growth rate, short enough that the terminal value (the single most
# assumption-sensitive number in any DCF) doesn't dominate the result more
# than it already inherently does.
PROJECTION_YEARS = 5
# Long-run terminal growth rate, roughly matching long-term US GDP/inflation
# expectations -- deliberately the conservative, retail-investor-convention
# figure, used as the BASE case here on purpose (not nudged upward
# per-company the way an analyst justifying a higher target might).
TERMINAL_GROWTH_RATE = 0.025
# How far the low/high sensitivity scenarios push WACC and terminal growth
# away from the base case. Small, deliberately conservative deltas (this is
# a sensitivity check, not a wide bull/bear scenario spread) -- just enough
# to show how assumption-sensitive the base figure actually is.
WACC_SENSITIVITY_DELTA = 0.01
GROWTH_SENSITIVITY_DELTA = 0.005
# Below this many years of annual revenue history, a CAGR is more noise than
# signal -- falls back to the pipeline's simple 1-year revenue_growth_pct
# instead (still real data, just noisier).
MIN_REVENUE_YEARS_FOR_CAGR = 3


def compute_wacc(beta, market_cap_usd, total_debt_usd, risk_free_rate=RISK_FREE_RATE,
                  equity_risk_premium=EQUITY_RISK_PREMIUM,
                  pretax_cost_of_debt=PRETAX_COST_OF_DEBT, tax_rate=ASSUMED_TAX_RATE):
    """CAPM cost of equity blended with an after-tax assumed cost of debt,
    weighted by MARKET value of equity (not book) and total debt -- the
    standard textbook WACC formula. Returns None if beta or market cap is
    unavailable (can't compute a required return without them, and
    fabricating a default beta would hide that this ticker has thinner data
    than most)."""
    if beta is None or not market_cap_usd or market_cap_usd <= 0:
        return None
    cost_of_equity = risk_free_rate + beta * equity_risk_premium
    total_debt_usd = total_debt_usd or 0.0
    total_capital = market_cap_usd + total_debt_usd
    equity_weight = market_cap_usd / total_capital
    debt_weight = total_debt_usd / total_capital
    after_tax_cost_of_debt = pretax_cost_of_debt * (1 - tax_rate)
    return equity_weight * cost_of_equity + debt_weight * after_tax_cost_of_debt


def compute_base_growth_rate(annual_revenues, fallback_growth_pct=None):
    """annual_revenues: most-recent-first list of annual (10-K) revenue
    figures, however many years of history have been ingested so far.
    Returns a CAGR over the longest available window once at least
    MIN_REVENUE_YEARS_FOR_CAGR years exist (multi-year trend, smooths out a
    single unusual year), else falls back to the simple 1-year
    revenue_growth_pct already computed elsewhere in the pipeline. Returns
    None if neither is available -- no growth assumption to build a DCF on.
    """
    usable = [r for r in (annual_revenues or []) if r and r > 0]
    if len(usable) >= MIN_REVENUE_YEARS_FOR_CAGR:
        newest, oldest = usable[0], usable[-1]
        years = len(usable) - 1
        if oldest > 0 and years > 0:
            return (newest / oldest) ** (1 / years) - 1
    if fallback_growth_pct is not None:
        return fallback_growth_pct / 100
    return None


def _project_and_discount(base_fcf, growth_rate, wacc, terminal_growth, years=PROJECTION_YEARS):
    """2-stage DCF: fades growth_rate linearly toward terminal_growth over
    `years` (not held flat forever -- see PROJECTION_YEARS), discounts each
    projected year's FCF at wacc, then adds a Gordon Growth terminal value
    (also discounted back to present). Returns enterprise value in dollars,
    or None if wacc <= terminal_growth (the Gordon Growth formula is
    undefined/explosive there -- a real constraint, not an edge case to
    paper over)."""
    if wacc is None or wacc <= terminal_growth:
        return None
    pv_sum = 0.0
    fcf = base_fcf
    for year in range(1, years + 1):
        year_growth = growth_rate + (terminal_growth - growth_rate) * (year - 1) / max(years - 1, 1)
        fcf = fcf * (1 + year_growth)
        pv_sum += fcf / ((1 + wacc) ** year)
    terminal_value = fcf * (1 + terminal_growth) / (wacc - terminal_growth)
    pv_terminal = terminal_value / ((1 + wacc) ** years)
    return pv_sum + pv_terminal


def compute_dcf_fair_value(*, base_fcf, growth_rate, beta, market_cap_usd, total_debt_usd,
                            cash_usd, shares_outstanding, risk_free_rate=RISK_FREE_RATE,
                            wacc_delta=WACC_SENSITIVITY_DELTA, growth_delta=GROWTH_SENSITIVITY_DELTA,
                            terminal_growth=TERMINAL_GROWTH_RATE, projection_years=PROJECTION_YEARS):
    """Top-level orchestrator. Returns a dict with a low/base/high fair
    value per share -- 'low' = higher WACC + lower terminal growth (most
    conservative), 'high' = the opposite, 'base' = the documented central
    assumptions with no nudging in either direction -- plus every
    assumption used, so nothing is a black box. Returns None if required
    inputs (positive base_fcf, a growth rate, and a computable WACC) are
    missing, rather than fabricating a value from partial data.
    """
    if base_fcf is None or base_fcf <= 0 or growth_rate is None or not shares_outstanding:
        return None
    base_wacc = compute_wacc(beta, market_cap_usd, total_debt_usd, risk_free_rate)
    if base_wacc is None:
        return None

    total_debt_usd = total_debt_usd or 0.0
    cash_usd = cash_usd or 0.0

    def _fair_value_per_share(wacc, term_growth):
        ev = _project_and_discount(base_fcf, growth_rate, wacc, term_growth, projection_years)
        if ev is None:
            return None
        equity_value = ev - total_debt_usd + cash_usd
        return equity_value / shares_outstanding

    low = _fair_value_per_share(base_wacc + wacc_delta, terminal_growth - growth_delta)
    base = _fair_value_per_share(base_wacc, terminal_growth)
    high = _fair_value_per_share(base_wacc - wacc_delta, terminal_growth + growth_delta)
    if base is None:
        return None

    return {
        "fair_value_low": round(low, 2) if low is not None else None,
        "fair_value_base": round(base, 2),
        "fair_value_high": round(high, 2) if high is not None else None,
        "wacc_base_pct": round(base_wacc * 100, 2),
        "growth_rate_base_pct": round(growth_rate * 100, 2),
        "terminal_growth_pct": round(terminal_growth * 100, 2),
        "projection_years": projection_years,
    }
