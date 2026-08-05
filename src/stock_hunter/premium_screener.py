"""Weekly options premium-selling screener.

Reads the analytics already computed by the pipeline (risk/quality/distress/
insider/drawdown scores) to build an "avoid list" of names too dangerous to
sell short-dated options against, then narrows survivors by:

1. Upcoming-earnings exclusion (an earnings print inside the option's life is
   the single most common way a short-premium trade blows up).
2. A live yfinance options-chain pull for the nearest weekly expiration,
   picking a near-the-money contract and comparing its implied volatility to
   trailing realized volatility (from stored price_history) as a rough
   "is premium rich enough to bother selling" signal.
3. A correlation check across survivors (using stored price_history) so the
   final candidate list isn't 10 names that all move together.

This is a screening aid, not a trade-execution or profit-guarantee tool: it
has no view on macro risk, broad-market direction, or true options-market
liquidity (bid/ask spread and open interest are reported but not scored).

Usage:
    PYTHONPATH=src python -m stock_hunter.premium_screener
    PYTHONPATH=src python -m stock_hunter.premium_screener --strategy cash_secured_put
    PYTHONPATH=src python -m stock_hunter.premium_screener --strategy covered_call --max-picks 8
"""
import argparse
import math
import sqlite3
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .schema import DB_NAME
from .logger import banner, step, info, success, warning, error

try:
    import yfinance as yf
except Exception:
    yf = None

# ---- avoid-list thresholds -------------------------------------------------
RISK_SCORE_AVOID_THRESHOLD = 65
# Insider selling is deliberately NOT a hard avoid-list criterion (see
# get_insider_selling_summary) -- it's shown informationally on the report
# instead, since it correlates weakly with near-term price direction for
# this mega-cap-only universe.
EIGHT_K_LOOKBACK_DAYS = 180
EARNINGS_LOOKAHEAD_DAYS = 7
CORRELATION_LOOKBACK_DAYS = 90
CORRELATION_MAX_THRESHOLD = 0.70

# ---- option-leg selection ---------------------------------------------------
# How far out-of-the-money to target the short leg: below current price for
# puts (cash_secured_put, put_credit_spread), above current price for calls
# (covered_call). 5% is a common starting point for short-dated premium
# selling; adjust via --short-otm-pct for a more/less aggressive strike.
SHORT_LEG_OTM_PCT = 0.05
# put_credit_spread only: how many strikes below the short leg to place the
# long (protective) leg. Counted in strikes, not dollars/percent, so it
# adapts automatically to each underlying's actual strike spacing.
SPREAD_WIDTH_STRIKES = 2

# ---- trend filter -------------------------------------------------------
# Require price above its trailing SMA for bullish/neutral strategies (you
# want the stock to stay flat-to-up), sourced from stored price_history.
SMA_TREND_FILTER_PERIOD = 50

# ---- probability-of-profit model -----------------------------------------
# Approximate short-term risk-free rate used in the Black-Scholes probability
# estimate below. Not fetched live; adjust via --risk-free-rate if it drifts.
RISK_FREE_RATE = 0.045

# ---- macro/regime filters ---------------------------------------------------
# These gate the ENTIRE run for bullish/neutral strategies (require_uptrend),
# not individual tickers -- no amount of stock-picking protects a single-name
# short-premium position from a genuine systemic shock, so instead of trying
# to score "recession risk" per ticker, we just refuse to open new bullish/
# neutral trades when the broad market itself is unhealthy.
MARKET_INDEX_TICKER = "SPY"
MARKET_SMA_PERIOD = 200  # standard "bull vs. bear market" threshold
VIX_TICKER = "^VIX"
VIX_PAUSE_THRESHOLD = 30  # elevated/crisis-level volatility

# ---- liquidity filter ---------------------------------------------------
# Below ~100 open interest, bid/ask spreads commonly blow out past 10-20% of
# the option's value, and closing a position can be difficult at a fair
# price -- a widely-cited practitioner threshold for options sellers. Applies
# to every leg (both legs of a spread must individually clear the bar).
MIN_OPEN_INTEREST = 100

STRATEGIES = {
    "cash_secured_put": {
        "description": "Sell a put on a name you'd be happy to own at a lower price.",
        "option_side": "puts",
        "min_quality_score": 55,
        "prefer_drawdown_opportunity": True,
        "require_uptrend": True,
    },
    "put_credit_spread": {
        "description": "Defined-risk put spread; slightly lower quality bar since you don't want assignment.",
        "option_side": "puts",
        "min_quality_score": 40,
        "prefer_drawdown_opportunity": True,
        "require_uptrend": True,
    },
    "covered_call": {
        "description": "Sell a call against a name you hold or want moderate, capped upside on.",
        "option_side": "calls",
        "min_quality_score": 55,
        "prefer_drawdown_opportunity": False,
        # Not trend-gated: a covered call is often written specifically to
        # generate income on a name that's lagging while you wait it out.
        "require_uptrend": False,
    },
}


def _connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def get_insider_selling_summary(conn, ticker, days_back=EIGHT_K_LOOKBACK_DAYS):
    """Informational only -- NOT used to exclude candidates. Insider-selling
    volume/breadth turned out to correlate weakly with near-term price
    direction for this mega-cap-only universe (executives are paid largely
    in equity, so routine diversification sales are common regardless of
    outlook), so it's surfaced for the trader to eyeball rather than used
    as a filter. Returns distinct open-market sellers, total dollar value
    sold, and the single largest sale in the trailing window."""
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(DISTINCT insider_name), COALESCE(SUM(total_value), 0), COALESCE(MAX(total_value), 0)
        FROM insider_trades
        WHERE ticker = ? AND code = 'S' AND filing_date >= ?
    """, (ticker, cutoff))
    sellers, total_value, max_sale = cursor.fetchone()
    return {
        "insider_sellers": sellers or 0,
        "insider_sale_value": total_value or 0.0,
        "insider_largest_sale": max_sale or 0.0,
    }


def build_avoid_list(conn):
    """Hard excludes: elevated financial distress, any bankruptcy-related 8-K,
    or top-quartile risk score. Insider selling is intentionally NOT a hard
    exclude here -- see get_insider_selling_summary for why -- but it still
    contributes a small weight inside daily_snapshot.risk_score itself, so
    it isn't entirely ignored, just not treated as disqualifying on its own."""
    cutoff = (datetime.now() - timedelta(days=EIGHT_K_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    cursor = conn.cursor()

    avoid = {}

    cursor.execute("""
        SELECT ticker, risk_level FROM distress_scores
        WHERE risk_level = 'Material solvency concerns'
    """)
    for row in cursor.fetchall():
        avoid[row["ticker"]] = "Material solvency concerns (distress model)"

    cursor.execute("""
        SELECT DISTINCT ticker FROM eight_k_events
        WHERE is_bankruptcy_related = 1 AND filing_date >= ?
    """, (cutoff,))
    for row in cursor.fetchall():
        avoid[row["ticker"]] = "Bankruptcy-related 8-K in trailing window"

    cursor.execute("""
        SELECT ticker, risk_score FROM daily_snapshot
    """)
    for row in cursor.fetchall():
        ticker = row["ticker"]
        if ticker in avoid:
            continue
        if row["risk_score"] is not None and row["risk_score"] >= RISK_SCORE_AVOID_THRESHOLD:
            avoid[ticker] = f"Risk score {row['risk_score']} >= {RISK_SCORE_AVOID_THRESHOLD}"

    return avoid


def load_all_active_candidates(conn):
    """Every active stock and ETF, regardless of whether it's eligible for
    options screening -- a LEFT JOIN so tickers with no daily_snapshot/
    drawdown_summary row yet still show up (with those fields as None) rather
    than silently disappearing, which matters for reporting a complete
    rejection list.

    Also pulls structural concentration risk (product/customer/supplier/
    geographic) from the latest 10-K -- stocks only, since ETFs don't file
    10-Ks; will be None for every ETF row, which callers should render as
    "N/A" rather than a numeric 0 (0 would misleadingly imply "verified no
    concentration" rather than "not applicable/not scored")."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            u.ticker, u.name, u.asset_type, u.sector,
            ds.price, ds.quality_score, ds.investment_score, ds.risk_score,
            ds.drawdown_opportunity_score, ds.insider_sentiment_score,
            ds.current_drawdown_pct, ds.dividend_yield_pct, ds.low_52w, ds.high_52w,
            dsum.avg_drawdown_pct, dsum.worst_drawdown_pct,
            conc.concentration_risk_score, conc.concentration_risk_summary, conc.concentration_risk_type
        FROM universe u
        LEFT JOIN daily_snapshot ds ON ds.ticker = u.ticker
        LEFT JOIN drawdown_summary dsum ON dsum.ticker = u.ticker
        LEFT JOIN (
            SELECT sf1.ticker, sf1.concentration_risk_score, sf1.concentration_risk_summary, sf1.concentration_risk_type
            FROM sec_financials sf1
            WHERE sf1.form_type = '10-K'
              AND sf1.filing_date = (
                  SELECT MAX(sf2.filing_date) FROM sec_financials sf2
                  WHERE sf2.ticker = sf1.ticker AND sf2.form_type = '10-K'
              )
        ) conc ON conc.ticker = u.ticker
        WHERE u.status = 'active' AND u.asset_type IN ('Stock', 'ETF')
    """)
    return [dict(row) for row in cursor.fetchall()]


# Leveraged/inverse ETFs have structural volatility decay (well documented:
# compounding daily rebalancing means NAV erodes over multi-day holds even if
# the underlying index round-trips to its starting price) that makes them
# generally unsuitable for premium-selling strategies. None are in this
# project's default universe today, but this guards against future additions
# rather than assuming the universe never changes. Detected by name pattern
# since we don't store an explicit leverage-factor field.
LEVERAGED_INVERSE_KEYWORDS = [
    "2x", "3x", "-1x", "ultrapro", "ultra ", "ultrashort", "inverse", "bear ",
    "leveraged", "daily target", "triple", "double short", "short etf",
]


def is_leveraged_or_inverse_etf(name):
    if not name:
        return False
    lower = f" {name.lower()} "
    return any(kw in lower for kw in LEVERAGED_INVERSE_KEYWORDS)


def compute_sma(conn, ticker, period=SMA_TREND_FILTER_PERIOD):
    """Simple moving average of the last `period` daily closes from stored
    price_history. Returns None if there isn't enough history yet."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT close_price FROM price_history
        WHERE ticker = ? ORDER BY trade_date DESC LIMIT ?
    """, (ticker, period))
    closes = [row["close_price"] for row in cursor.fetchall()]
    if len(closes) < period:
        return None
    return round(sum(closes) / len(closes), 2)


def check_market_regime(conn, index_ticker=MARKET_INDEX_TICKER, sma_period=MARKET_SMA_PERIOD):
    """Gate the whole run, not a single ticker: is the broad market itself
    healthy enough to sell bullish/neutral premium into? Compares a live
    price for `index_ticker` against its trailing SMA computed from stored
    price_history. No amount of per-ticker screening protects against a
    genuine bear market, so this refuses new entries outright rather than
    trying to score "recession risk" per candidate.

    Returns (is_healthy, detail_dict). Fails open (treats the market as
    healthy) if data is unavailable, since blocking every run on a data
    hiccup would defeat the screener's purpose -- the detail dict still
    reports what happened so this isn't a silent assumption.
    """
    live_price = fetch_live_price(index_ticker)
    sma = compute_sma(conn, index_ticker, sma_period)
    if live_price is None or sma is None:
        return True, {
            "index": index_ticker, "price": live_price, "sma": sma,
            "note": "Insufficient data to evaluate market regime; failing open (treating as healthy)",
        }
    is_healthy = live_price >= sma
    return is_healthy, {"index": index_ticker, "price": live_price, "sma": sma, "sma_period": sma_period}


def check_vix_level(threshold=VIX_PAUSE_THRESHOLD, vix_ticker=VIX_TICKER):
    """Systemic-stress gate: is the VIX (market's own fear gauge) elevated
    enough to warrant pausing new bullish/neutral premium sales? Fails open
    (treats VIX as calm) if the fetch fails, for the same reason as
    check_market_regime -- reported in the detail dict, not silently assumed.
    """
    vix_level = fetch_live_price(vix_ticker)
    if vix_level is None:
        return True, {"vix": None, "threshold": threshold, "note": "VIX fetch failed; failing open (treating as calm)"}
    is_calm = vix_level < threshold
    return is_calm, {"vix": vix_level, "threshold": threshold}


def rank_and_filter_pool(conn, all_rows, avoid_list, strategy_key, pool_size, sma_period=SMA_TREND_FILTER_PERIOD):
    """Split all active stocks into an eligible, ranked pool (up to pool_size)
    and a rejections dict {ticker: reason} covering everyone else: avoid-list
    hits, missing snapshot data, below the strategy's quality bar, below its
    trend filter (for strategies that require one), or ranked outside the
    evaluated pool."""
    strategy = STRATEGIES[strategy_key]
    rejections = {}
    for ticker, reason in avoid_list.items():
        rejections[ticker] = f"Avoid list: {reason}"

    eligible = []
    for row in all_rows:
        ticker = row["ticker"]
        if ticker in rejections:
            continue
        if row["asset_type"] == "ETF" and is_leveraged_or_inverse_etf(row.get("name")):
            rejections[ticker] = "Leveraged/inverse ETF -- structural volatility decay makes these unsuitable for premium selling"
            continue
        if row["price"] is None or row["quality_score"] is None:
            rejections[ticker] = "No daily_snapshot data yet (pipeline hasn't scored this ticker)"
            continue
        if row["quality_score"] < strategy["min_quality_score"]:
            rejections[ticker] = f"Quality score {row['quality_score']} below strategy minimum {strategy['min_quality_score']}"
            continue
        if strategy["require_uptrend"]:
            sma = compute_sma(conn, ticker, sma_period)
            if sma is not None and row["price"] < sma:
                rejections[ticker] = (
                    f"Price ${row['price']:.2f} below {sma_period}-day SMA (${sma:.2f}) -- trend filter, "
                    "not a confirmed uptrend"
                )
                continue
            row["sma"] = sma
        eligible.append(row)

    if strategy["prefer_drawdown_opportunity"]:
        eligible.sort(key=lambda r: (r["drawdown_opportunity_score"] or 0), reverse=True)
    else:
        eligible.sort(key=lambda r: (r["investment_score"] or 0), reverse=True)

    pool = eligible[:pool_size]
    for row in eligible[pool_size:]:
        rejections[row["ticker"]] = (
            f"Passed quality/avoid filters but ranked outside the top {pool_size} candidates "
            "evaluated (raise --pool-size to consider more)"
        )
    return pool, rejections


def _yf_symbol(ticker):
    """Yahoo Finance uses a hyphen where our tickers use a period (e.g. our
    'BRK.B' is Yahoo's 'BRK-B'). Without this, yfinance 404s / reports the
    ticker as delisted -- not an actual data gap, just an unnormalized
    symbol. Matches the same normalization pipeline.py already applies."""
    return ticker.replace(".", "-")


def has_earnings_within(ticker, days=EARNINGS_LOOKAHEAD_DAYS):
    """Returns (has_upcoming_earnings, earnings_date_or_None). Fails open
    (assumes no upcoming earnings) if yfinance can't answer, since blocking
    every candidate on an API hiccup would defeat the screener's purpose."""
    if yf is None:
        return False, None
    try:
        calendar = yf.Ticker(_yf_symbol(ticker)).calendar
        earnings_dates = calendar.get("Earnings Date") if calendar else None
        if not earnings_dates:
            return False, None
        next_date = min(earnings_dates)
        days_out = (next_date - datetime.now().date()).days
        if 0 <= days_out <= days:
            return True, next_date
        return False, next_date
    except Exception as exc:
        warning(f"{ticker}: earnings calendar lookup failed, assuming no upcoming earnings: {exc}")
        return False, None


def _normal_cdf(x):
    """Standard normal CDF via math.erf (stdlib only, no scipy dependency)."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def probability_finishes_otm(option_side, spot, strike, days_to_expiration, iv_pct, risk_free_rate=RISK_FREE_RATE):
    """Black-Scholes estimate of the probability this specific contract
    expires out-of-the-money, using the contract's own implied volatility.
    For a put, that means the probability of achieving max profit on a short
    put / put credit spread (spot finishes above the strike). For a call, the
    probability of keeping the full covered-call premium (spot finishes below
    the strike).

    This is a model-based estimate, not an empirical/backtested figure -- it
    assumes lognormal returns (the Black-Scholes assumption), which is a
    simplification real markets don't perfectly obey, and it estimates the
    probability of finishing OTM at expiration specifically, not the
    probability that this strategy's actual exit rules end up profitable.
    Returns None if the inputs can't support the calculation (no time left,
    no IV, etc).
    """
    if not spot or not strike or not days_to_expiration or not iv_pct or iv_pct <= 0:
        return None
    T = days_to_expiration / 365.0
    sigma = iv_pct / 100.0
    if T <= 0 or sigma <= 0:
        return None
    try:
        d1 = (math.log(spot / strike) + (risk_free_rate + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
    except (ValueError, ZeroDivisionError):
        return None
    if option_side == "puts":
        prob_otm = _normal_cdf(d2)       # spot finishes above strike
    else:
        prob_otm = _normal_cdf(-d2)      # spot finishes below strike
    return round(prob_otm * 100, 1)


def fetch_live_price(ticker):
    """Latest live price via yfinance, used instead of daily_snapshot.price so
    OTM strike targeting matches the current market rather than whatever
    price the last full pipeline run happened to store (which can be a day
    or more stale for the option chain being pulled right now). Returns None
    on any failure so the caller can fall back to the stored snapshot price
    rather than aborting the whole ticker."""
    if yf is None:
        return None
    try:
        hist = yf.Ticker(_yf_symbol(ticker)).history(period="1d")
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception as exc:
        warning(f"{ticker}: live price fetch failed, falling back to stored snapshot price: {exc}")
        return None


def _find_weekly_expiration(stock):
    """Nearest expiration 4-10 days out. Returns (exp_str, days_out) or (None, None)."""
    expirations = stock.options
    if not expirations:
        return None, None
    today = datetime.now().date()
    for exp_str in expirations:
        exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
        days_out = (exp_date - today).days
        if 4 <= days_out <= 10:
            return exp_str, days_out
    return None, None


def _pick_strike_near_target(table, target_price):
    table = table.copy()
    table["distance"] = (table["strike"] - target_price).abs()
    return table.sort_values("distance").iloc[0]


def fetch_weekly_option_snapshot(ticker, current_price, strategy_key, realized_vol_pct,
                                  short_otm_pct=SHORT_LEG_OTM_PCT, spread_width_strikes=SPREAD_WIDTH_STRIKES,
                                  risk_free_rate=RISK_FREE_RATE, min_open_interest=MIN_OPEN_INTEREST):
    """Pick the nearest expiration (target ~4-10 days out for a 'weekly').

    For single-leg strategies (cash_secured_put, covered_call), targets a
    strike out-of-the-money by `short_otm_pct` (below current price for puts,
    above for calls) rather than pure at-the-money, matching how these are
    normally sold. For put_credit_spread, returns full two-leg economics
    priced off a conservative bid/ask fill (sell the short leg at its bid,
    buy the long leg at its ask) rather than the more optimistic mid-price --
    that's the realistic price a marketable limit order could expect to get
    filled at, not what you'd get in a best-case scenario. Also attaches a
    Black-Scholes probability-of-finishing-OTM estimate to every candidate.

    Returns (snapshot_dict, None) on success, or (None, reason_str) when no
    usable weekly expiration/contract(s)/viable spread can be found.
    """
    if yf is None:
        return None, "yfinance unavailable"
    strategy = STRATEGIES[strategy_key]
    option_side = strategy["option_side"]
    try:
        stock = yf.Ticker(_yf_symbol(ticker))
        target_exp, target_days = _find_weekly_expiration(stock)
        if target_exp is None:
            return None, "No weekly expiration (4-10 days out) available"

        chain = stock.option_chain(target_exp)
        table = chain.puts if option_side == "puts" else chain.calls
        if table.empty:
            return None, f"No {option_side} contracts available for the nearest weekly expiration"
        table = table.sort_values("strike").reset_index(drop=True)

        # OTM target: below current price for puts, above for calls.
        if option_side == "puts":
            short_target_price = current_price * (1 - short_otm_pct)
        else:
            short_target_price = current_price * (1 + short_otm_pct)

        if strategy_key == "put_credit_spread":
            short_row = _pick_strike_near_target(table, short_target_price)
            short_idx = table.index[table["strike"] == short_row["strike"]][0]
            long_idx = short_idx - spread_width_strikes
            if long_idx < 0:
                reason = f"Not enough strikes below the short leg to build a {spread_width_strikes}-strike-wide spread"
                warning(f"{ticker}: {reason}")
                return None, reason
            long_row = table.iloc[long_idx]

            short_oi = int(short_row["openInterest"]) if pd.notna(short_row["openInterest"]) else 0
            long_oi = int(long_row["openInterest"]) if pd.notna(long_row["openInterest"]) else 0
            if short_oi < min_open_interest or long_oi < min_open_interest:
                reason = (
                    f"Open interest too thin: short leg {short_oi}, long leg {long_oi} "
                    f"(minimum {min_open_interest} required on both legs)"
                )
                warning(f"{ticker}: {reason}")
                return None, reason

            short_bid = float(short_row["bid"]) if pd.notna(short_row["bid"]) else 0.0
            short_ask = float(short_row["ask"]) if pd.notna(short_row["ask"]) else 0.0
            long_bid = float(long_row["bid"]) if pd.notna(long_row["bid"]) else 0.0
            long_ask = float(long_row["ask"]) if pd.notna(long_row["ask"]) else 0.0
            short_strike = float(short_row["strike"])
            long_strike = float(long_row["strike"])

            # Primary: conservative bid/ask fill -- sell the short leg at its
            # bid, buy the long leg at its ask. This is what a marketable
            # limit order could realistically expect to fill at, not the
            # optimistic mid-to-mid assumption used previously.
            net_credit = round(short_bid - long_ask, 2)
            # Secondary reference only: what a mid-to-mid fill would look
            # like, i.e. the upside if you get a better-than-guaranteed fill.
            mid_credit = round(((short_bid + short_ask) / 2) - ((long_bid + long_ask) / 2), 2)

            if net_credit <= 0:
                # Not a viable credit spread at a realistic fill -- this
                # strike/width combination would cost money to open, not earn it.
                reason = f"{short_strike}/{long_strike} put spread has non-positive credit at a realistic bid/ask fill ({net_credit})"
                warning(f"{ticker}: {reason}, skipping as not viable")
                return None, reason

            spread_width = round(short_strike - long_strike, 2)
            max_loss = round(spread_width - net_credit, 2)
            max_profit = net_credit
            breakeven = round(short_strike - net_credit, 2)
            return_on_risk_pct = round((max_profit / max_loss) * 100, 1) if max_loss > 0 else None

            iv_pct = float(short_row["impliedVolatility"]) * 100 if pd.notna(short_row["impliedVolatility"]) else None
            iv_premium_pct = (iv_pct - realized_vol_pct) if (iv_pct is not None and realized_vol_pct is not None) else None
            confidence_pct = probability_finishes_otm(
                "puts", current_price, short_strike, target_days, iv_pct, risk_free_rate
            )

            return {
                "expiration": target_exp,
                "days_to_expiration": target_days,
                "short_strike": short_strike,
                "long_strike": long_strike,
                "spread_width": spread_width,
                "net_credit": net_credit,
                "mid_credit": mid_credit,
                "max_profit": max_profit,
                "max_loss": max_loss,
                "breakeven": breakeven,
                "return_on_risk_pct": return_on_risk_pct,
                "confidence_pct": confidence_pct,
                "implied_volatility_pct": round(iv_pct, 1) if iv_pct is not None else None,
                "iv_premium_vs_realized_pct": round(iv_premium_pct, 1) if iv_premium_pct is not None else None,
                "short_open_interest": short_oi,
                "long_open_interest": long_oi,
            }, None

        # Single-leg strategies (cash_secured_put, covered_call)
        contract = _pick_strike_near_target(table, short_target_price)
        contract_oi = int(contract["openInterest"]) if pd.notna(contract["openInterest"]) else 0
        if contract_oi < min_open_interest:
            reason = f"Open interest too thin: {contract_oi} (minimum {min_open_interest} required)"
            warning(f"{ticker}: {reason}")
            return None, reason

        iv_pct = float(contract["impliedVolatility"]) * 100 if pd.notna(contract["impliedVolatility"]) else None
        iv_premium_pct = (iv_pct - realized_vol_pct) if (iv_pct is not None and realized_vol_pct is not None) else None
        strike = float(contract["strike"])
        confidence_pct = probability_finishes_otm(option_side, current_price, strike, target_days, iv_pct, risk_free_rate)

        bid = float(contract["bid"]) if pd.notna(contract["bid"]) else 0.0
        ask = float(contract["ask"]) if pd.notna(contract["ask"]) else 0.0
        mid = (bid + ask) / 2 if (bid or ask) else 0.0
        spread_pct = ((ask - bid) / mid * 100) if mid else None

        return {
            "expiration": target_exp,
            "days_to_expiration": target_days,
            "strike": strike,
            "bid": bid,
            "ask": ask,
            "mid_price": round(mid, 2),
            "confidence_pct": confidence_pct,
            "implied_volatility_pct": round(iv_pct, 1) if iv_pct is not None else None,
            "iv_premium_vs_realized_pct": round(iv_premium_pct, 1) if iv_premium_pct is not None else None,
            "open_interest": contract_oi,
            "volume": int(contract["volume"]) if pd.notna(contract["volume"]) else 0,
            "bid_ask_spread_pct": round(spread_pct, 1) if spread_pct is not None else None,
        }, None
    except Exception as exc:
        warning(f"{ticker}: option chain lookup failed: {exc}")
        return None, f"Option chain lookup failed: {exc}"


def compute_realized_volatility_pct(conn, ticker, lookback_days=30):
    """Annualized realized volatility (%) from stored daily closes."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT close_price FROM price_history
        WHERE ticker = ? ORDER BY trade_date DESC LIMIT ?
    """, (ticker, lookback_days + 1))
    closes = [row["close_price"] for row in cursor.fetchall()]
    if len(closes) < 5:
        return None
    closes = np.array(list(reversed(closes)), dtype=float)
    log_returns = np.diff(np.log(closes))
    if len(log_returns) < 5:
        return None
    annualized_pct = log_returns.std() * math.sqrt(252) * 100
    return round(annualized_pct, 1)


def load_return_series(conn, tickers, lookback_days=CORRELATION_LOOKBACK_DAYS):
    cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    placeholders = ",".join("?" for _ in tickers)
    df = pd.read_sql_query(
        f"""
        SELECT ticker, trade_date, close_price FROM price_history
        WHERE ticker IN ({placeholders}) AND trade_date >= ?
        ORDER BY trade_date ASC
        """,
        conn,
        params=(*tickers, cutoff),
    )
    if df.empty:
        return pd.DataFrame()
    pivot = df.pivot(index="trade_date", columns="ticker", values="close_price")
    return pivot.pct_change().dropna(how="all")


def diversify_by_correlation(ranked_tickers, returns_df, max_picks, max_correlation=CORRELATION_MAX_THRESHOLD):
    """Greedy selection: walk the ranked list in order, only keep a candidate
    if its correlation with every already-picked name stays under the
    threshold. Falls back to keeping a candidate if correlation can't be
    computed (missing return history) rather than silently dropping it.

    Returns (picked_tickers, rejections) where rejections maps every
    not-picked ticker to a specific reason (too correlated with which peer,
    or simply not evaluated because --max-picks was already reached).
    """
    picked = []
    rejections = {}
    for ticker in ranked_tickers:
        if len(picked) >= max_picks:
            rejections[ticker] = f"Not evaluated for correlation -- --max-picks={max_picks} already reached"
            continue
        if ticker not in returns_df.columns:
            picked.append(ticker)
            continue
        too_correlated = False
        correlated_with, correlated_value = None, None
        for chosen in picked:
            if chosen not in returns_df.columns:
                continue
            pair = returns_df[[ticker, chosen]].dropna()
            if len(pair) < 20:
                continue
            corr = pair[ticker].corr(pair[chosen])
            if pd.notna(corr) and corr >= max_correlation:
                too_correlated = True
                correlated_with, correlated_value = chosen, corr
                break
        if too_correlated:
            rejections[ticker] = (
                f"Correlated {correlated_value:.2f} with already-picked {correlated_with} over trailing "
                f"{CORRELATION_LOOKBACK_DAYS} days (threshold {max_correlation:.2f})"
            )
        else:
            picked.append(ticker)
    return picked, rejections


def run_screener(db_path=DB_NAME, strategy_key="cash_secured_put", max_picks=10, pool_size=40,
                  short_otm_pct=SHORT_LEG_OTM_PCT, spread_width_strikes=SPREAD_WIDTH_STRIKES,
                  sma_period=SMA_TREND_FILTER_PERIOD, risk_free_rate=RISK_FREE_RATE,
                  market_index=MARKET_INDEX_TICKER, market_sma_period=MARKET_SMA_PERIOD,
                  vix_threshold=VIX_PAUSE_THRESHOLD, min_open_interest=MIN_OPEN_INTEREST):
    if strategy_key not in STRATEGIES:
        raise ValueError(f"Unknown strategy '{strategy_key}'. Choose from: {list(STRATEGIES)}")

    banner(f"Premium screener: strategy={strategy_key}")
    conn = _connect(db_path)
    strategy = STRATEGIES[strategy_key]

    if strategy["require_uptrend"]:
        step(f"Step 0: checking market regime ({market_index} trend) and VIX before screening individual names")
        market_healthy, market_detail = check_market_regime(conn, index_ticker=market_index, sma_period=market_sma_period)
        vix_calm, vix_detail = check_vix_level(threshold=vix_threshold)

        if not market_healthy:
            warning(
                f"Market regime check failed: {market_detail['index']} price ${market_detail['price']:.2f} "
                f"is below its {market_detail['sma_period']}-day SMA (${market_detail['sma']:.2f}) -- "
                "no new bullish/neutral trades this run regardless of individual ticker quality."
            )
            conn.close()
            return {
                "strategy": strategy_key, "avoid_list": {}, "candidates": [], "rejected": [],
                "blocked_reason": (
                    f"Market regime unhealthy: {market_detail['index']} (${market_detail['price']:.2f}) "
                    f"is below its {market_detail['sma_period']}-day SMA (${market_detail['sma']:.2f})"
                ),
            }
        if not vix_calm:
            warning(
                f"VIX check failed: {vix_detail['vix']:.1f} is at/above the {vix_detail['threshold']} pause "
                "threshold -- no new bullish/neutral trades this run regardless of individual ticker quality."
            )
            conn.close()
            return {
                "strategy": strategy_key, "avoid_list": {}, "candidates": [], "rejected": [],
                "blocked_reason": f"VIX elevated: {vix_detail['vix']:.1f} >= pause threshold {vix_detail['threshold']}",
            }
        success(
            f"Market regime OK ({market_detail['index']} ${market_detail['price']:.2f} above "
            f"{market_detail['sma_period']}-day SMA ${market_detail['sma']:.2f}); "
            f"VIX calm ({vix_detail['vix']:.1f} < {vix_detail['threshold']})"
        )

    step("Step 1: building avoid list from distress/8-K/risk-score signals")
    avoid_list = build_avoid_list(conn)
    success(f"Avoid list: {len(avoid_list)} tickers excluded")

    step("Step 2: ranking remaining candidates by strategy fit")
    all_rows = load_all_active_candidates(conn)
    by_ticker = {row["ticker"]: row for row in all_rows}
    pool, rejections = rank_and_filter_pool(conn, all_rows, avoid_list, strategy_key, pool_size, sma_period=sma_period)
    success(f"Candidate pool after quality/avoid/trend filters: {len(pool)} tickers")

    if yf is None:
        warning("yfinance unavailable; skipping earnings/options checks")

    step("Step 3: excluding names with earnings in the next week")
    survivors = []
    for row in pool:
        ticker = row["ticker"]
        has_earnings, earnings_date = has_earnings_within(ticker)
        if has_earnings:
            reason = f"Earnings on {earnings_date} within {EARNINGS_LOOKAHEAD_DAYS}-day lookahead"
            rejections[ticker] = reason
            info(f"{ticker}: skipped, {reason.lower()}")
            continue
        row["next_earnings_date"] = str(earnings_date) if earnings_date else None
        survivors.append(row)
    success(f"Survivors after earnings filter: {len(survivors)} tickers")

    step("Step 4: pulling live price, weekly option chain, and realized volatility")
    enriched = []
    stale_price_count = 0
    for row in survivors:
        ticker = row["ticker"]
        live_price = fetch_live_price(ticker)
        if live_price is not None:
            row["price"] = live_price
            row["price_source"] = "live"
        else:
            row["price_source"] = "stored (live fetch failed)"
            stale_price_count += 1

        # Drawdown/52-week-low context for the report: how far the (now
        # possibly live) price sits above its 52-week low. Smaller = closer
        # to the low, which for a bullish/neutral strategy like cash-secured
        # puts or put credit spreads can suggest more room to bounce.
        if row.get("low_52w"):
            row["pct_above_52w_low"] = round((row["price"] - row["low_52w"]) / row["low_52w"] * 100, 1)
        else:
            row["pct_above_52w_low"] = None

        row.update(get_insider_selling_summary(conn, ticker))

        realized_vol = compute_realized_volatility_pct(conn, ticker)
        option_snapshot, reason = fetch_weekly_option_snapshot(
            ticker, row["price"], strategy_key, realized_vol,
            short_otm_pct=short_otm_pct, spread_width_strikes=spread_width_strikes,
            risk_free_rate=risk_free_rate, min_open_interest=min_open_interest,
        )
        if option_snapshot is None:
            rejections[ticker] = reason or "No usable weekly option data"
            info(f"{ticker}: no usable weekly option chain found, skipping ({reason})")
            continue
        row["realized_volatility_pct"] = realized_vol
        row.update(option_snapshot)
        enriched.append(row)
    success(f"Survivors with usable option data: {len(enriched)} tickers")
    if stale_price_count:
        warning(
            f"{stale_price_count} ticker(s) fell back to the stored daily_snapshot price "
            "because a live yfinance quote could not be fetched; OTM strike targeting for "
            "those rows may be off from the current market."
        )

    if strategy_key == "put_credit_spread":
        step("Step 4b: sorting by return-on-risk (bid/ask realistic-fill basis)")
        enriched.sort(key=lambda r: (r.get("return_on_risk_pct") if r.get("return_on_risk_pct") is not None else -999), reverse=True)
    else:
        step("Step 4b: sorting by implied-volatility premium over realized volatility")
        enriched.sort(key=lambda r: (r.get("iv_premium_vs_realized_pct") or -999), reverse=True)

    step("Step 5: cross-position correlation check")
    ranked_tickers = [r["ticker"] for r in enriched]
    returns_df = load_return_series(conn, ranked_tickers) if ranked_tickers else pd.DataFrame()
    final_tickers, correlation_rejections = diversify_by_correlation(ranked_tickers, returns_df, max_picks)
    rejections.update(correlation_rejections)
    final_rows = [r for r in enriched if r["ticker"] in final_tickers]
    final_rows.sort(key=lambda r: final_tickers.index(r["ticker"]))
    success(f"Final candidates after correlation diversification: {len(final_rows)}")

    rejected_rows = []
    for ticker, reason in rejections.items():
        base = by_ticker.get(ticker, {})
        rejected_rows.append({"ticker": ticker, "name": base.get("name"), "sector": base.get("sector"), "reason": reason})
    rejected_rows.sort(key=lambda r: r["ticker"])

    conn.close()
    return {
        "strategy": strategy_key,
        "avoid_list": avoid_list,
        "candidates": final_rows,
        "rejected": rejected_rows,
        "blocked_reason": None,
    }


def _ticker_label(r):
    """Ticker with a trailing '*' when its price fell back to the stored
    daily_snapshot value instead of a live yfinance quote."""
    marker = "*" if r.get("price_source", "live") != "live" else ""
    return f"{r['ticker']}{marker}"


def _format_insider_value_m(r):
    value = r.get("insider_sale_value") or 0
    return round(value / 1_000_000, 1)


def _format_concentration_risk(r):
    """N/A for ETFs (no 10-K, so genuinely not scored) rather than a numeric
    0, which would misleadingly read as "verified no concentration"."""
    if r.get("asset_type") == "ETF":
        return "N/A"
    score = r.get("concentration_risk_score")
    return str(score) if score is not None else "N/A"


def _print_concentration_detail(candidates):
    """Supplementary list (not a table column, too much width) of the actual
    concentration disclosure text for any candidate with a meaningful score."""
    flagged = [r for r in candidates if (r.get("concentration_risk_score") or 0) >= 30 and r.get("concentration_risk_summary")]
    if not flagged:
        return
    print()
    print("Concentration risk detail (score >= 30):")
    for r in flagged:
        ctype = r.get("concentration_risk_type") or "none"
        print(f"  {r['ticker']} [{ctype}, score {r['concentration_risk_score']}]: {r['concentration_risk_summary']}")


def _print_single_leg_report(candidates):
    header = (
        f"{'Ticker':<8}{'Sector':<16}{'Price':>9}{'Qual':>6}{'Risk':>6}"
        f"{'CurrDD%':>8}{'AvgDD%':>7}{'Lo52wGap%':>10}{'#Sellers':>9}{'InsSel$M':>9}{'ConcRisk':>9}"
        f"{'Exp':>12}{'Strike':>8}{'Mid':>7}{'IV%':>7}{'RV%':>7}{'IVprem':>8}{'ProbOTM%':>9}{'OI':>7}"
    )
    print(header)
    print("-" * len(header))
    for r in candidates:
        print(
            f"{_ticker_label(r):<8}{(r['sector'] or '')[:14]:<16}{r['price']:>9.2f}"
            f"{r['quality_score']:>6}{r['risk_score']:>6}"
            f"{(r.get('current_drawdown_pct') if r.get('current_drawdown_pct') is not None else 0):>8.1f}"
            f"{(r.get('avg_drawdown_pct') if r.get('avg_drawdown_pct') is not None else 0):>7.1f}"
            f"{(r.get('pct_above_52w_low') if r.get('pct_above_52w_low') is not None else 0):>10.1f}"
            f"{(r.get('insider_sellers') or 0):>9}{_format_insider_value_m(r):>9.1f}"
            f"{_format_concentration_risk(r):>9}"
            f"{r['expiration']:>12}{r['strike']:>8.1f}{r['mid_price']:>7.2f}"
            f"{(r.get('implied_volatility_pct') or 0):>7.1f}{(r.get('realized_volatility_pct') or 0):>7.1f}"
            f"{(r.get('iv_premium_vs_realized_pct') or 0):>8.1f}"
            f"{(r.get('confidence_pct') if r.get('confidence_pct') is not None else 0):>9.1f}{(r.get('open_interest') or 0):>7}"
        )
    print()
    print("CurrDD% = current drawdown from 52w high (negative), AvgDD% = this ticker's historical average")
    print("drawdown magnitude, Lo52wGap% = how far the price sits above its 52-week low (smaller = closer to the low).")
    print("#Sellers/InsSel$M = distinct insiders who sold on the open market / total $ sold, trailing 180 days --")
    print("informational only, not used to filter candidates (see README for why).")
    print("ConcRisk = LLM-derived 0-100 estimate of structural product/customer/supplier/geographic")
    print("concentration from the latest 10-K -- informational only, not used to filter candidates.")
    print("ProbOTM% = Black-Scholes model estimate of the probability this strike finishes out-of-the-money at")
    print("expiration (using the contract's own implied volatility) -- a model estimate, not a guarantee.")
    if any(r.get("price_source", "live") != "live" for r in candidates):
        print("* price is the stored daily_snapshot value, not a live quote (yfinance fetch failed for this ticker).")
    _print_concentration_detail(candidates)


def _print_spread_report(candidates):
    header = (
        f"{'Ticker':<8}{'Sector':<11}{'Price':>9}{'Qual':>6}{'Risk':>6}"
        f"{'CurrDD%':>8}{'AvgDD%':>7}{'Lo52wGap%':>10}{'#Sellers':>9}{'InsSel$M':>9}{'ConcRisk':>9}"
        f"{'Exp':>12}{'Short':>7}{'Long':>7}{'Width':>7}{'Credit':>8}{'MidCr':>7}"
        f"{'MaxLoss':>9}{'RoR%':>7}{'BrkEven':>9}{'IV%':>7}{'ProbOTM%':>9}"
    )
    print(header)
    print("-" * len(header))
    for r in candidates:
        print(
            f"{_ticker_label(r):<8}{(r['sector'] or '')[:9]:<11}{r['price']:>9.2f}"
            f"{r['quality_score']:>6}{r['risk_score']:>6}"
            f"{(r.get('current_drawdown_pct') if r.get('current_drawdown_pct') is not None else 0):>8.1f}"
            f"{(r.get('avg_drawdown_pct') if r.get('avg_drawdown_pct') is not None else 0):>7.1f}"
            f"{(r.get('pct_above_52w_low') if r.get('pct_above_52w_low') is not None else 0):>10.1f}"
            f"{(r.get('insider_sellers') or 0):>9}{_format_insider_value_m(r):>9.1f}"
            f"{_format_concentration_risk(r):>9}"
            f"{r['expiration']:>12}{r['short_strike']:>7.1f}{r['long_strike']:>7.1f}{r['spread_width']:>7.1f}"
            f"{r['net_credit']:>8.2f}{r['mid_credit']:>7.2f}{r['max_loss']:>9.2f}"
            f"{(r.get('return_on_risk_pct') or 0):>7.1f}{r['breakeven']:>9.2f}"
            f"{(r.get('implied_volatility_pct') or 0):>7.1f}"
            f"{(r.get('confidence_pct') if r.get('confidence_pct') is not None else 0):>9.1f}"
        )
    print()
    print("CurrDD% = current drawdown from 52w high (negative), AvgDD% = this ticker's historical average")
    print("drawdown magnitude, Lo52wGap% = how far the price sits above its 52-week low (smaller = closer to the low).")
    print("#Sellers/InsSel$M = distinct insiders who sold on the open market / total $ sold, trailing 180 days --")
    print("informational only, not used to filter candidates (see README for why).")
    print("ConcRisk = LLM-derived 0-100 estimate of structural product/customer/supplier/geographic")
    print("concentration from the latest 10-K -- informational only, not used to filter candidates.")
    print("Short = short put strike (sold), Long = protective put strike (bought), Width = strike distance,")
    print("Credit = net credit at a realistic bid/ask fill (sell short at bid, buy long at ask) -- the primary,")
    print("conservative figure everything else here is computed from. MidCr = what a mid-to-mid fill would give,")
    print("shown only as an upside reference, not what you should plan around. MaxLoss = width - Credit,")
    print("RoR% = max profit / max loss (both on the Credit basis), BrkEven = short strike - Credit.")
    print("ProbOTM% = Black-Scholes model estimate of the probability the short strike finishes out-of-the-money")
    print("at expiration (using the contract's own implied volatility) -- a model estimate, not a guarantee.")
    if any(r.get("price_source", "live") != "live" for r in candidates):
        print("* price is the stored daily_snapshot value, not a live quote (yfinance fetch failed for this ticker).")
    _print_concentration_detail(candidates)


def _print_rejections_table(rejected_rows):
    if not rejected_rows:
        return
    header = f"{'Ticker':<8}{'Sector':<20}{'Reason':<95}"
    print(header)
    print("-" * len(header))
    for r in rejected_rows:
        print(f"{r['ticker']:<8}{(r['sector'] or '')[:18]:<20}{r['reason']:<95}")


def print_report(result, show_rejected=True):
    strategy_key = result["strategy"]
    strategy = STRATEGIES[strategy_key]
    banner(f"Premium screener report: {strategy_key}")
    info(strategy["description"])
    print()

    if result.get("blocked_reason"):
        warning(f"No screening was run this cycle -- {result['blocked_reason']}.")
        info("This is a whole-run gate, not a per-ticker rejection: no individual stock's quality can "
             "override a market-wide regime/volatility signal for a bullish/neutral strategy.")
        return

    candidates = result["candidates"]
    if not candidates:
        warning("No candidates survived all filters. Consider loosening thresholds or checking the log for skip reasons.")
    elif strategy_key == "put_credit_spread":
        _print_spread_report(candidates)
    else:
        _print_single_leg_report(candidates)

    print()
    info(f"{len(result['avoid_list'])} tickers were excluded by the avoid list (distress/8-K/risk-score signals).")
    info("This is a screening aid, not a trade recommendation: it has no view on broad-market risk, "
         "true options liquidity beyond open interest, or position sizing.")

    if show_rejected:
        rejected = result.get("rejected") or []
        print()
        banner(f"Filtered out: {len(rejected)} ticker(s) and why")
        _print_rejections_table(rejected)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Weekly options premium-selling screener")
    parser.add_argument("--db-path", default=DB_NAME, help="Path to drawdown_analyzer.db")
    parser.add_argument("--strategy", choices=list(STRATEGIES), default="cash_secured_put")
    parser.add_argument("--max-picks", type=int, default=10, help="Max final candidates after diversification")
    parser.add_argument("--pool-size", type=int, default=40, help="How many top-ranked names to run option/earnings checks on")
    parser.add_argument("--short-otm-pct", type=float, default=SHORT_LEG_OTM_PCT,
                         help="How far OTM to target the short leg, e.g. 0.05 = 5%% below price for puts, above for calls")
    parser.add_argument("--spread-width-strikes", type=int, default=SPREAD_WIDTH_STRIKES,
                         help="put_credit_spread only: number of strikes below the short leg for the protective long leg")
    parser.add_argument("--sma-period", type=int, default=SMA_TREND_FILTER_PERIOD,
                         help="Trend filter: require price above this N-day SMA for bullish/neutral strategies")
    parser.add_argument("--risk-free-rate", type=float, default=RISK_FREE_RATE,
                         help="Approximate risk-free rate used in the Black-Scholes probability-of-profit estimate")
    parser.add_argument("--market-index", default=MARKET_INDEX_TICKER,
                         help="Market-regime gate: which broad-market ticker to check the trend of")
    parser.add_argument("--market-sma-period", type=int, default=MARKET_SMA_PERIOD,
                         help="Market-regime gate: block new bullish/neutral trades if --market-index is below this N-day SMA")
    parser.add_argument("--vix-threshold", type=float, default=VIX_PAUSE_THRESHOLD,
                         help="Block new bullish/neutral trades if VIX is at/above this level")
    parser.add_argument("--min-open-interest", type=int, default=MIN_OPEN_INTEREST,
                         help="Minimum open interest required on every leg (liquidity floor)")
    parser.add_argument("--show-rejected", action=argparse.BooleanOptionalAction, default=True,
                         help="Print a table of every filtered-out ticker and why (default: on)")
    args = parser.parse_args()

    result = run_screener(
        db_path=args.db_path,
        strategy_key=args.strategy,
        max_picks=args.max_picks,
        pool_size=args.pool_size,
        short_otm_pct=args.short_otm_pct,
        spread_width_strikes=args.spread_width_strikes,
        sma_period=args.sma_period,
        risk_free_rate=args.risk_free_rate,
        market_index=args.market_index,
        market_sma_period=args.market_sma_period,
        vix_threshold=args.vix_threshold,
        min_open_interest=args.min_open_interest,
    )
    print_report(result, show_rejected=args.show_rejected)
