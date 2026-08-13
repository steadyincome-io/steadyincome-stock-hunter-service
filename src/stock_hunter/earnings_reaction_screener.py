"""Earnings-reaction screener: a magnitude/quality filter, NOT a directional
signal. Core principle (per the spec this was built from): fundamentals
don't predict earnings reactions, reaction history does.

This identifies whether a name's upcoming (or a past, for backtesting)
earnings report was/is a big-enough, well-enough-priced event to be worth
trading at all -- and whether options or shares give better risk/reward for
capturing the reaction. It does NOT tell you which direction to bet; that's
on you.

Data sources, and why each one is used where:
- Reaction history (rule 1: EPS surprise + past reactions) -- yfinance
  get_earnings_dates(), verified live against AAPL/JPM going back years.
- Realized price reaction (rules 1, 2, 3, 5) -- yfinance daily price history.
- Implied move + liquidity for an UPCOMING report (live screening, rules 4
  and 6) -- yfinance's live option_chain(), same mechanism premium_screener
  already uses; real current bid/ask/open interest.
- Implied move + liquidity for a PAST report (backtesting, rules 4 and 6) --
  Alpaca's historical options API (data.alpaca.markets), which provides
  REAL historical option prices, not a synthetic/modeled estimate. Requires
  APCA_API_KEY_ID / APCA_API_SECRET_KEY in .env (see .env.example).
  Historical coverage only goes back to ALPACA_OPTIONS_DATA_FLOOR below --
  earnings events before that can't get a real implied move, full stop, no
  estimate is fabricated to fill the gap.

Reaction metric -- a real judgment call, made explicit rather than left
implicit: "next-session % change" here means the OVERNIGHT GAP specifically
(prior/report-day close -> next session's OPEN), not the full close-to-close
day. Chosen because the strategy's own execution rule is "sell in the first
30-60 minutes of the reaction session" -- the P&L this strategy actually
captures is the gap, not a full day of whatever the stock does afterward.
Verified live against AAPL's 2025-01-30 earnings: the $237.5 ATM straddle
priced a 4.16% implied move; the actual open gap was +4.04% (ratio ~0.97,
fairly priced) while the full close-to-close move was only -0.67% (ratio
~0.16, would look like a huge options loser). These two definitions can
give OPPOSITE conclusions for the same event -- this has to be a deliberate
choice, not an accident of which column happened to be convenient.

Backtest scope, stated plainly: this backtests whether the RULE 2/5 FILTER
would have correctly flagged a name's past reactions as tradeable, using
100% real data (real EPS surprises, real price gaps, real historical option
prices/OI). It does NOT simulate realized dollar P&L, because that requires
a directional assumption (calls vs puts, long vs short) these rules
deliberately don't make -- inventing one would violate the "never invent
numbers, catalysts, or certainty" standard this was built to.
"""
import os
import statistics
from datetime import date, datetime, timedelta

import pandas as pd
import requests

from .logger import banner, step, info, success, warning, error
from .premium_screener import fetch_live_price
from .schema import DB_NAME

try:
    import yfinance as yf
except Exception:
    yf = None


def _ensure_env_loaded():
    """Same belt-and-suspenders .env loader ai_narrative.py uses -- a manual
    parser first (works even if python-dotenv isn't installed), then
    load_dotenv() as backup. Never overwrites an already-set env var, so
    CI-injected secrets (GitHub Actions) always win over a stray .env file.
    """
    curr = os.path.abspath(__file__)
    for _ in range(5):
        curr = os.path.dirname(curr)
        env_path = os.path.join(curr, ".env")
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as handle:
                    for raw_line in handle:
                        line = raw_line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        key, value = line.split("=", 1)
                        key = key.strip()
                        value = value.strip().strip("'").strip('"')
                        if key and key not in os.environ:
                            os.environ[key] = value
            except Exception:
                pass
            break


_ensure_env_loaded()
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


# ---- strategy thresholds (documented judgment calls, not derived) ---------
REACTION_LOOKBACK_QUARTERS = 6
MIN_POSITIVE_REACTIONS = 3          # rule 2: reject if fewer than 3/6 positive
MIN_AVG_ABS_MOVE_PCT = 5.0          # rule 2: reject if avg |move| under 5%
RUNUP_LOOKBACK_DAYS = 14            # rule 3: pre-report run-up window
RUNUP_THRESHOLD_PCT = 25.0          # rule 3: flag if up more than this in that window
IV_RICH_RATIO_THRESHOLD = 0.7       # rule 4: historical-avg-move / implied-move below this = use shares
MIN_OPTION_OPEN_INTEREST = 200      # rule 6
MAX_SPREAD_PCT_OF_MARK = 0.20       # rule 6 (live screening only -- see module docstring)
MAX_CANDIDATES = 5                  # "3-5 names max"
EARNINGS_LOOKAHEAD_DAYS = 7         # live screening: how far ahead counts as "reporting this week"

# Alpaca's historical options data starts here -- confirmed against their own
# docs (https://docs.alpaca.markets/us/docs/historical-option-data), not
# assumed. A backtest quarter reporting before this date cannot get a real
# implied move and is skipped with an explicit reason, never estimated.
ALPACA_OPTIONS_DATA_FLOOR = date(2024, 2, 1)

ALPACA_DATA_BASE = "https://data.alpaca.markets"
# The /v2/options/contracts endpoint lives on the TRADING api, separate from
# the data api used for bars above -- and a paper-account key (starts "PK")
# only authenticates against the paper base, not the live one. Defaults to
# paper since that's what a free/testing account has; override via
# APCA_API_TRADING_BASE_URL in .env if using a live-trading key instead.
ALPACA_TRADING_BASE = os.environ.get("APCA_API_TRADING_BASE_URL", "https://paper-api.alpaca.markets")


def _alpaca_headers():
    key_id = os.environ.get("APCA_API_KEY_ID")
    secret = os.environ.get("APCA_API_SECRET_KEY")
    if not key_id or not secret:
        return None
    return {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}


def _yf_symbol(ticker):
    return ticker.replace(".", "-")


# ---- rule 1: reaction history -----------------------------------------------

def _classify_timing(report_timestamp):
    """BMO (before market open) vs AMC (after market close), inferred from
    the report timestamp's hour -- verified live: JPM reports ~6-8am ET
    (BMO), AAPL reports 4pm ET (AMC). Hour < 12 -> BMO, else AMC; earnings
    reports cluster clearly at the open or the close, not in between, so
    this simple split is reliable in practice."""
    return "BMO" if report_timestamp.hour < 12 else "AMC"


def fetch_reaction_history(ticker, quarters=REACTION_LOOKBACK_QUARTERS):
    """Last `quarters` REPORTED earnings (skips the upcoming un-reported row,
    which has NaN actual EPS), most-recent-first. For each: EPS surprise%,
    BMO/AMC timing, and the realized overnight-gap reaction (see module
    docstring for why gap, not close-to-close).

    Returns a list of dicts, or [] if yfinance/data is unavailable -- never
    raises, since a single ticker's data gap shouldn't crash a multi-name
    screener run.
    """
    if yf is None:
        return []
    try:
        stock = yf.Ticker(_yf_symbol(ticker))
        raw = stock.get_earnings_dates(limit=quarters + 4)
        if raw is None or raw.empty:
            return []
        reported = raw[raw["Reported EPS"].notna()].head(quarters)
        if reported.empty:
            return []

        # One padded history fetch covering the whole window, rather than
        # quarters separate calls -- cheaper and avoids quarter-boundary
        # edge cases in per-call date ranges.
        earliest = reported.index.min().tz_localize(None) - timedelta(days=10)
        latest = reported.index.max().tz_localize(None) + timedelta(days=10)
        prices = stock.history(start=earliest, end=latest + timedelta(days=1))
        if prices.empty:
            return []
        prices.index = prices.index.tz_localize(None)
        trading_days = prices.index.sort_values()

        history = []
        for report_ts, row in reported.iterrows():
            report_ts_naive = report_ts.tz_localize(None) if report_ts.tzinfo else report_ts
            report_date = report_ts_naive.normalize()
            timing = _classify_timing(report_ts_naive)

            on_or_before = trading_days[trading_days <= report_date]
            after = trading_days[trading_days > report_date]
            if timing == "BMO":
                if len(on_or_before) < 2 or len(after) < 1:
                    continue
                pre_close = prices.loc[on_or_before[-2], "Close"]
                reaction_open = prices.loc[on_or_before[-1] if on_or_before[-1] == report_date else after[0], "Open"]
            else:  # AMC
                if len(on_or_before) < 1 or len(after) < 1:
                    continue
                pre_close = prices.loc[on_or_before[-1], "Close"]
                reaction_open = prices.loc[after[0], "Open"]

            if not pre_close or pd.isna(pre_close) or not reaction_open or pd.isna(reaction_open):
                continue
            gap_pct = round(float((reaction_open - pre_close) / pre_close * 100), 2)

            history.append({
                "report_date": report_date.date().isoformat(),
                "timing": timing,
                "eps_surprise_pct": round(float(row["Surprise(%)"]), 2) if pd.notna(row["Surprise(%)"]) else None,
                "gap_pct": gap_pct,
                "pre_close": round(float(pre_close), 2),  # last real price before the reaction -- the
                                                           # correct spot reference for that event's implied move,
                                                           # not a guessed/current price
            })

        return history
    except Exception as exc:
        warning(f"{ticker}: reaction history fetch failed: {exc}")
        return []


def evaluate_reaction_quality(history, min_positive=MIN_POSITIVE_REACTIONS,
                               min_avg_abs_move_pct=MIN_AVG_ABS_MOVE_PCT):
    """Rule 2. Returns (passes, detail_dict). detail_dict always includes
    both mean and median absolute move -- per the spec, show the median,
    not just the mean, since 5-6 observations are easily skewed by one
    outlier quarter."""
    if len(history) < 2:
        return False, {
            "reason": f"Only {len(history)} reported quarter(s) of history -- too thin to evaluate at all",
            "n": len(history),
        }
    positive_count = sum(1 for h in history if h["gap_pct"] > 0)
    abs_moves = [abs(h["gap_pct"]) for h in history]
    avg_abs_move = round(float(statistics.mean(abs_moves)), 2)
    median_abs_move = round(float(statistics.median(abs_moves)), 2)

    detail = {
        "n": len(history),
        "positive_count": positive_count,
        "avg_abs_move_pct": avg_abs_move,
        "median_abs_move_pct": median_abs_move,
    }
    if positive_count < min_positive:
        detail["reason"] = f"Only {positive_count}/{len(history)} reactions were positive (need >= {min_positive})"
        return False, detail
    if avg_abs_move < min_avg_abs_move_pct:
        detail["reason"] = f"Average |move| {avg_abs_move}% is below the {min_avg_abs_move_pct}% tradeable-event floor"
        return False, detail
    return True, detail


# ---- rule 3: pre-report run-up ---------------------------------------------

def check_preearnings_runup(ticker, report_date_str, lookback_days=RUNUP_LOOKBACK_DAYS,
                             threshold_pct=RUNUP_THRESHOLD_PCT):
    """Rule 3. Returns (is_flagged, runup_pct_or_None). Fails open (not
    flagged) if price history is unavailable, logged rather than silently
    assumed."""
    if yf is None:
        return False, None
    try:
        report_date = datetime.fromisoformat(report_date_str)
        start = report_date - timedelta(days=lookback_days + 5)
        stock = yf.Ticker(_yf_symbol(ticker))
        prices = stock.history(start=start, end=report_date + timedelta(days=1))
        if prices.empty:
            return False, None
        prices.index = prices.index.tz_localize(None)
        window = prices[prices.index <= report_date].tail(lookback_days + 1)
        if len(window) < 2:
            return False, None
        runup_pct = round(float((window["Close"].iloc[-1] - window["Close"].iloc[0]) / window["Close"].iloc[0] * 100), 2)
        return runup_pct >= threshold_pct, runup_pct
    except Exception as exc:
        warning(f"{ticker}: run-up check failed: {exc}")
        return False, None


# ---- rules 4/5/6: implied move, breakeven, liquidity -----------------------
# Two independent implementations -- LIVE (yfinance, current chain, for
# screening an upcoming report) and HISTORICAL (Alpaca, for backtesting a
# past report). They are NOT equivalent in what they can check: live gets
# real current bid/ask spread; historical gets real open interest (Alpaca's
# contracts endpoint reports it even for expired contracts) but NOT bid/ask
# spread (Alpaca's historical bars are OHLC+volume only, no quotes) -- rather
# than fabricate a spread estimate for the backtest path, it's reported as
# unavailable, explicitly, every time.

def _nearest_strike(strikes, target):
    return min(strikes, key=lambda s: abs(s - target))


def compute_implied_move_live(ticker, report_date_str, timing, spot_price):
    """LIVE path: current option chain via yfinance. Picks the nearest
    available expiration ON OR AFTER the reaction session (report day itself
    for BMO, next trading day for AMC) to minimize how much ordinary
    (non-event) time value the straddle also prices in -- see module
    docstring on why that matters. Returns a dict or None if no usable chain
    exists (e.g. report date already passed, or no chain data)."""
    if yf is None:
        return None
    try:
        stock = yf.Ticker(_yf_symbol(ticker))
        expirations = stock.options
        if not expirations:
            return None
        report_date = datetime.fromisoformat(report_date_str).date()
        reaction_date = report_date if timing == "BMO" else report_date + timedelta(days=1)
        usable = sorted(e for e in expirations if datetime.fromisoformat(e).date() >= reaction_date)
        if not usable:
            return None
        expiration = usable[0]

        chain = stock.option_chain(expiration)
        calls, puts = chain.calls, chain.puts
        if calls.empty or puts.empty:
            return None
        strike = _nearest_strike(calls["strike"].tolist(), spot_price)

        call_row = calls[calls["strike"] == strike]
        put_row = puts[puts["strike"] == strike]
        if call_row.empty or put_row.empty:
            return None
        call_row, put_row = call_row.iloc[0], put_row.iloc[0]

        call_bid, call_ask = float(call_row["bid"] or 0), float(call_row["ask"] or 0)
        put_bid, put_ask = float(put_row["bid"] or 0), float(put_row["ask"] or 0)
        straddle_mid = ((call_bid + call_ask) / 2) + ((put_bid + put_ask) / 2)
        if straddle_mid <= 0:
            return None
        implied_move_pct = round(straddle_mid / spot_price * 100, 2)

        call_spread_pct = (call_ask - call_bid) / straddle_mid if straddle_mid else None
        put_spread_pct = (put_ask - put_bid) / straddle_mid if straddle_mid else None

        return {
            "source": "live (yfinance)",
            "expiration": expiration,
            "strike": float(strike),
            "straddle_mid": round(straddle_mid, 2),
            "implied_move_pct": implied_move_pct,
            "call_open_interest": int(call_row.get("openInterest") or 0),
            "put_open_interest": int(put_row.get("openInterest") or 0),
            "spread_pct_of_mark": round(max(call_spread_pct or 0, put_spread_pct or 0) * 100, 1),
        }
    except Exception as exc:
        warning(f"{ticker}: live implied-move fetch failed: {exc}")
        return None


def compute_implied_move_historical(ticker, report_date_str, timing, spot_price):
    """HISTORICAL path: Alpaca. Returns None (with a logged reason) if the
    report predates ALPACA_OPTIONS_DATA_FLOOR, if auth isn't configured, or
    if no usable contract/bar data exists -- never fabricates a number to
    fill the gap."""
    headers = _alpaca_headers()
    if headers is None:
        warning(f"{ticker}: Alpaca credentials not configured (APCA_API_KEY_ID/APCA_API_SECRET_KEY) -- skipping historical implied move")
        return None

    report_date = datetime.fromisoformat(report_date_str).date()
    if report_date < ALPACA_OPTIONS_DATA_FLOOR:
        warning(f"{ticker}: {report_date} predates Alpaca's historical options data floor ({ALPACA_OPTIONS_DATA_FLOOR}) -- no real implied move available")
        return None

    reaction_date = report_date if timing == "BMO" else report_date + timedelta(days=1)
    asof_date = report_date  # last trading session before the reaction, priced going in

    try:
        # Find the nearest expiration on/after the reaction date, within a
        # 2-week window (weekly-ish), using Alpaca's contracts endpoint --
        # status=inactive since we're looking at a past, now-expired date.
        resp = requests.get(
            f"{ALPACA_TRADING_BASE}/v2/options/contracts",
            headers=headers,
            params={
                "underlying_symbols": ticker,
                "expiration_date_gte": reaction_date.isoformat(),
                "expiration_date_lte": (reaction_date + timedelta(days=14)).isoformat(),
                "strike_price_gte": spot_price * 0.9,
                "strike_price_lte": spot_price * 1.1,
                "status": "inactive",
                "limit": 200,
            },
            timeout=15,
        )
        resp.raise_for_status()
        contracts = resp.json().get("option_contracts", [])
        if not contracts:
            warning(f"{ticker}: no Alpaca contracts found for {report_date} reaction window")
            return None

        expirations = sorted({c["expiration_date"] for c in contracts})
        expiration = expirations[0]
        same_exp = [c for c in contracts if c["expiration_date"] == expiration]
        strikes = sorted({float(c["strike_price"]) for c in same_exp})
        strike = _nearest_strike(strikes, spot_price)

        call = next((c for c in same_exp if float(c["strike_price"]) == strike and c["type"] == "call"), None)
        put = next((c for c in same_exp if float(c["strike_price"]) == strike and c["type"] == "put"), None)
        if not call or not put:
            warning(f"{ticker}: no matching call/put pair at strike {strike} for {expiration}")
            return None

        bars_resp = requests.get(
            f"{ALPACA_DATA_BASE}/v1beta1/options/bars",
            headers=headers,
            params={
                "symbols": f"{call['symbol']},{put['symbol']}",
                "timeframe": "1Day",
                "start": (asof_date - timedelta(days=5)).isoformat(),
                "end": asof_date.isoformat(),
                "limit": 20,
            },
            timeout=15,
        )
        bars_resp.raise_for_status()
        bars = bars_resp.json().get("bars", {})
        call_bars, put_bars = bars.get(call["symbol"], []), bars.get(put["symbol"], [])
        if not call_bars or not put_bars:
            warning(f"{ticker}: no historical bars found on/before {asof_date} for the ATM straddle")
            return None

        call_close = call_bars[-1]["c"]
        put_close = put_bars[-1]["c"]
        straddle_price = call_close + put_close
        if straddle_price <= 0:
            return None
        implied_move_pct = round(straddle_price / spot_price * 100, 2)

        return {
            "source": "historical (Alpaca)",
            "expiration": expiration,
            "strike": strike,
            "straddle_mid": round(straddle_price, 2),
            "implied_move_pct": implied_move_pct,
            "call_open_interest": int(float(call.get("open_interest") or 0)),
            "put_open_interest": int(float(put.get("open_interest") or 0)),
            "spread_pct_of_mark": None,  # not available from historical bars, see docstring
        }
    except requests.HTTPError as exc:
        warning(f"{ticker}: Alpaca API error: {exc}")
        return None
    except Exception as exc:
        warning(f"{ticker}: historical implied-move fetch failed: {exc}")
        return None


def evaluate_options_economics(history, implied_move_info, min_open_interest=MIN_OPTION_OPEN_INTEREST,
                                max_spread_pct=MAX_SPREAD_PCT_OF_MARK, rich_ratio_threshold=IV_RICH_RATIO_THRESHOLD):
    """Rules 4, 5, 6 combined. Returns a detail dict -- never a hard
    pass/fail, since "use shares instead" is a valid, useful verdict, not a
    rejection. `history` must already be evaluate_reaction_quality-passing."""
    if not implied_move_info:
        return {"verdict": "no options data", "reason": "Could not compute an implied move"}

    abs_moves = [abs(h["gap_pct"]) for h in history]
    avg_abs_move = float(statistics.mean(abs_moves))
    implied_move_pct = implied_move_info["implied_move_pct"]
    ratio = round(avg_abs_move / implied_move_pct, 2) if implied_move_pct else None

    # Rule 5: breakeven as a % move, and how many of the last N would have cleared it.
    breakeven_pct = implied_move_pct
    hit_count = sum(1 for h in history if abs(h["gap_pct"]) >= breakeven_pct)

    # Rule 6: liquidity gate.
    min_oi = min(implied_move_info["call_open_interest"], implied_move_info["put_open_interest"])
    oi_ok = min_oi >= min_open_interest
    spread_pct = implied_move_info.get("spread_pct_of_mark")
    spread_ok = spread_pct is None or spread_pct <= (max_spread_pct * 100)

    if not oi_ok:
        verdict = "no trade -- illiquid"
    elif not spread_ok:
        verdict = "no trade -- spread too wide to exit after a gap"
    elif ratio is not None and ratio < rich_ratio_threshold:
        verdict = "use shares -- options pricing more move than history supports"
    else:
        verdict = "options viable"

    return {
        "verdict": verdict,
        "implied_move_pct": implied_move_pct,
        "avg_historical_move_pct": round(avg_abs_move, 2),
        "ratio": ratio,
        "breakeven_pct": breakeven_pct,
        "breakeven_hit_count": hit_count,
        "breakeven_hit_n": len(history),
        "min_open_interest": min_oi,
        "spread_pct_of_mark": spread_pct,
        "expiration": implied_move_info["expiration"],
        "strike": implied_move_info["strike"],
        "source": implied_move_info["source"],
    }


# ---- live multi-name weekly screener ---------------------------------------
# Applies all 6 rules to every active universe ticker reporting within
# EARNINGS_LOOKAHEAD_DAYS, using LIVE data for rules 4/6 (real current
# option chain via yfinance, not Alpaca -- the report hasn't happened yet,
# there's nothing historical to look up). Caps the final list at
# MAX_CANDIDATES per "3-5 names max."

def get_upcoming_earnings_date(ticker):
    """Returns (date, timing) for the next UN-reported earnings (the row
    with NaN actual EPS), or (None, None) if unavailable."""
    if yf is None:
        return None, None
    try:
        stock = yf.Ticker(_yf_symbol(ticker))
        raw = stock.get_earnings_dates(limit=4)
        if raw is None or raw.empty:
            return None, None
        upcoming = raw[raw["Reported EPS"].isna()]
        if upcoming.empty:
            return None, None
        ts = upcoming.index[0]
        ts_naive = ts.tz_localize(None) if ts.tzinfo else ts
        return ts_naive.date(), _classify_timing(ts_naive)
    except Exception:
        return None, None


def _load_active_tickers(db_path=DB_NAME):
    import sqlite3
    conn = sqlite3.connect(db_path)
    tickers = [r[0] for r in conn.execute(
        "SELECT ticker FROM universe WHERE status = 'active' AND asset_type = 'Stock'"
    ).fetchall()]
    conn.close()
    return tickers


def evaluate_upcoming_candidate(ticker, report_date, timing, quarters=REACTION_LOOKBACK_QUARTERS):
    """Runs rules 1-6 for one upcoming report. Returns a detail dict with a
    `status` field: 'rejected' (rule 2 or 3 failed / no data), or
    'evaluated' (rules 4-6 computed, verdict in econ['verdict'])."""
    history = fetch_reaction_history(ticker, quarters)
    if not history:
        return {"ticker": ticker, "report_date": report_date.isoformat(), "timing": timing,
                "status": "rejected", "reason": "No reaction history available"}

    quality_pass, quality_detail = evaluate_reaction_quality(history)
    if not quality_pass:
        return {"ticker": ticker, "report_date": report_date.isoformat(), "timing": timing,
                "status": "rejected", "reason": quality_detail.get("reason", "Failed rule 2"),
                "history": history, "quality_detail": quality_detail}

    runup_flagged, runup_pct = check_preearnings_runup(ticker, report_date.isoformat())

    spot = fetch_live_price(ticker)
    if not spot:
        return {"ticker": ticker, "report_date": report_date.isoformat(), "timing": timing,
                "status": "rejected", "reason": "Could not fetch live price",
                "history": history, "quality_detail": quality_detail}

    implied_info = compute_implied_move_live(ticker, report_date.isoformat(), timing, spot)
    econ = evaluate_options_economics(history, implied_info)

    return {
        "ticker": ticker, "report_date": report_date.isoformat(), "timing": timing,
        "status": "evaluated", "history": history, "quality_detail": quality_detail,
        "runup_flagged": runup_flagged, "runup_pct": runup_pct, "econ": econ,
    }


def run_live_earnings_screener(tickers=None, lookahead_days=EARNINGS_LOOKAHEAD_DAYS,
                                quarters=REACTION_LOOKBACK_QUARTERS, max_candidates=MAX_CANDIDATES):
    """Full pipeline: find active-universe tickers reporting within
    `lookahead_days`, run all 6 rules on each, rank survivors, cap at
    `max_candidates`. Returns {"upcoming": [...], "candidates": [...],
    "rejected": [...]}."""
    if tickers is None:
        tickers = _load_active_tickers()

    today = date.today()
    window_end = today + timedelta(days=lookahead_days)

    step(f"Scanning {len(tickers)} active tickers for earnings within the next {lookahead_days} days")
    upcoming = []
    for i, ticker in enumerate(tickers, start=1):
        report_date, timing = get_upcoming_earnings_date(ticker)
        if report_date and today <= report_date <= window_end:
            upcoming.append((ticker, report_date, timing))
        if i % 50 == 0:
            info(f"  ...{i}/{len(tickers)} scanned, {len(upcoming)} reporting in window so far")
    success(f"{len(upcoming)} tickers report within the next {lookahead_days} days")

    all_results = []
    for ticker, report_date, timing in upcoming:
        result = evaluate_upcoming_candidate(ticker, report_date, timing, quarters)
        all_results.append(result)

    rejected = [r for r in all_results if r["status"] == "rejected"]
    evaluated = [r for r in all_results if r["status"] == "evaluated"]

    # Rank by breakeven hit rate (rule 5) desc, then ratio (rule 4) asc --
    # favors names that both clear their own breakeven often AND aren't
    # richly priced relative to history. Only "options viable" verdicts are
    # promoted to final candidates; "use shares"/"no trade -- ..." verdicts
    # are kept in a separate bucket, not silently dropped.
    tradeable = [r for r in evaluated if r["econ"]["verdict"] == "options viable"]
    other_evaluated = [r for r in evaluated if r["econ"]["verdict"] != "options viable"]

    def _rank_key(r):
        econ = r["econ"]
        hit_rate = (econ["breakeven_hit_count"] / econ["breakeven_hit_n"]) if econ.get("breakeven_hit_n") else 0
        return (-hit_rate, econ.get("ratio") or 999)

    tradeable.sort(key=_rank_key)
    candidates = tradeable[:max_candidates]

    return {
        "upcoming_count": len(upcoming),
        "candidates": candidates,
        "other_evaluated": other_evaluated,
        "rejected": rejected,
    }


def print_live_screener_report(result, lookahead_days=EARNINGS_LOOKAHEAD_DAYS):
    print()
    print("=" * 78)
    print(f"Earnings-reaction screener -- {result['upcoming_count']} tickers reporting within {lookahead_days} days")
    print("=" * 78)

    if not result["candidates"]:
        print("\nNO TRADE -- nothing in the reporting window cleared all 6 rules.")
    else:
        print(f"\n{len(result['candidates'])} candidate(s) (of {result['upcoming_count']} reporting this window), ranked by breakeven hit rate then price:")
        header = f"{'Ticker':<8}{'Report':<12}{'Timing':<7}{'AvgMove%':>9}{'Implied%':>9}{'Ratio':>7}{'Breakeven':>11}{'MinOI':>8}"
        print(header)
        print("-" * len(header))
        for r in result["candidates"]:
            econ = r["econ"]
            hit = f"{econ['breakeven_hit_count']}/{econ['breakeven_hit_n']}"
            print(f"{r['ticker']:<8}{r['report_date']:<12}{r['timing']:<7}{econ['avg_historical_move_pct']:>9.2f}"
                  f"{econ['implied_move_pct']:>9.2f}{econ['ratio']:>7.2f}{hit:>11}{econ['min_open_interest']:>8}")

    if result["other_evaluated"]:
        print(f"\nCleared rule 2 but not a final candidate ({len(result['other_evaluated'])}):")
        for r in result["other_evaluated"]:
            print(f"  {r['ticker']}: {r['econ']['verdict']}")

    print(f"\nRejected at rule 2/data-availability ({len(result['rejected'])}):")
    for r in result["rejected"][:20]:
        print(f"  {r['ticker']}: {r['reason']}")
    if len(result["rejected"]) > 20:
        print(f"  ... and {len(result['rejected']) - 20} more")

    print()
    print("*** Sample size: each candidate's history is 5-6 quarters. This cannot distinguish real edge from  ***")
    print("*** luck for any single name. Ranking picks the best of what's available this week, not a         ***")
    print("*** guarantee any of it clears a genuine statistical bar -- read the per-name detail before acting.***")


# ---- single-ticker backtest -------------------------------------------------
# Scope, repeated from the module docstring because it matters: this checks,
# for each of the last N reported quarters, whether that event's OWN real
# historical implied move (from Alpaca) would have been cleared by the real
# realized gap -- a genuine per-event hit/miss using real data throughout.
# It does NOT simulate dollar P&L (no direction assumption is made -- see
# docstring) and CANNOT cover quarters before ALPACA_OPTIONS_DATA_FLOOR,
# which are reported as skipped, not estimated.

def backtest_single_ticker(ticker, quarters=REACTION_LOOKBACK_QUARTERS):
    """Returns a dict: reaction history, rule-2 quality verdict, and a
    per-quarter breakdown of real implied-move-vs-realized-move for whatever
    quarters fall within Alpaca's historical coverage."""
    history = fetch_reaction_history(ticker, quarters)
    if not history:
        return {"ticker": ticker, "history": [], "error": "No reaction history available"}

    quality_pass, quality_detail = evaluate_reaction_quality(history)

    per_quarter = []
    for event in history:
        report_date = datetime.fromisoformat(event["report_date"]).date()
        if report_date < ALPACA_OPTIONS_DATA_FLOOR:
            per_quarter.append({
                **event,
                "implied_move_pct": None,
                "ratio": None,
                "hit_breakeven": None,
                "skip_reason": f"Before Alpaca's historical options floor ({ALPACA_OPTIONS_DATA_FLOOR})",
            })
            continue

        implied_info = compute_implied_move_historical(ticker, event["report_date"], event["timing"], event["pre_close"])
        if not implied_info:
            per_quarter.append({
                **event,
                "implied_move_pct": None,
                "ratio": None,
                "hit_breakeven": None,
                "skip_reason": "No usable historical option data for this event",
            })
            continue

        implied_move_pct = implied_info["implied_move_pct"]
        realized_abs_move = abs(event["gap_pct"])
        ratio = round(realized_abs_move / implied_move_pct, 2) if implied_move_pct else None
        per_quarter.append({
            **event,
            "implied_move_pct": implied_move_pct,
            "ratio": ratio,
            "hit_breakeven": realized_abs_move >= implied_move_pct,
            "min_open_interest": min(implied_info["call_open_interest"], implied_info["put_open_interest"]),
            "strike": implied_info["strike"],
            "expiration": implied_info["expiration"],
            "skip_reason": None,
        })

    covered = [q for q in per_quarter if q["ratio"] is not None]
    summary = None
    if covered:
        ratios = [q["ratio"] for q in covered]
        summary = {
            "quarters_covered": len(covered),
            "quarters_total": len(per_quarter),
            "hit_count": sum(1 for q in covered if q["hit_breakeven"]),
            "mean_ratio": round(float(statistics.mean(ratios)), 2),
            "median_ratio": round(float(statistics.median(ratios)), 2),
        }

    return {
        "ticker": ticker,
        "history": history,
        "quality_pass": quality_pass,
        "quality_detail": quality_detail,
        "per_quarter": per_quarter,
        "summary": summary,
    }


def print_backtest_report(result):
    ticker = result["ticker"]
    print()
    print(f"=== Earnings-reaction backtest: {ticker} ===")
    if result.get("error"):
        print(f"  {result['error']}")
        return

    qd = result["quality_detail"]
    print(f"Rule 1/2 -- reaction history quality ({qd.get('n', 0)} reported quarters):")
    if "positive_count" in qd:
        print(f"  {qd['positive_count']}/{qd['n']} positive, avg |move| {qd['avg_abs_move_pct']}%, median |move| {qd['median_abs_move_pct']}%")
    print(f"  Verdict: {'PASSES rule 2 threshold' if result['quality_pass'] else 'FAILS rule 2 threshold'}" +
          (f" -- {qd['reason']}" if qd.get("reason") else ""))

    print()
    print("Per-quarter detail (real data; quarters before Alpaca's Feb 2024 floor are skipped, not estimated):")
    header = f"{'Report Date':<12}{'Timing':<7}{'EPSsurp%':>9}{'Gap%':>8}{'ImpliedMove%':>14}{'Ratio':>7}{'Breakeven':>11}"
    print(header)
    print("-" * len(header))
    for q in result["per_quarter"]:
        eps = f"{q['eps_surprise_pct']:.2f}" if q["eps_surprise_pct"] is not None else "N/A"
        if q["skip_reason"]:
            print(f"{q['report_date']:<12}{q['timing']:<7}{eps:>9}{q['gap_pct']:>8.2f}{'SKIPPED':>14}  {q['skip_reason']}")
        else:
            hit = "YES" if q["hit_breakeven"] else "no"
            print(f"{q['report_date']:<12}{q['timing']:<7}{eps:>9}{q['gap_pct']:>8.2f}{q['implied_move_pct']:>14.2f}{q['ratio']:>7.2f}{hit:>11}")

    summary = result["summary"]
    print()
    if not summary:
        print("No quarters fell within Alpaca's historical options coverage (since Feb 2024) -- no real implied-move")
        print("backtest could be run for this ticker's history. This is a data-availability limit, not a verdict.")
    else:
        print(f"Summary (n={summary['quarters_covered']} of {summary['quarters_total']} quarters had real Alpaca options data):")
        print(f"  Breakeven hit rate: {summary['hit_count']}/{summary['quarters_covered']}")
        print(f"  Mean ratio (realized/implied): {summary['mean_ratio']}   Median ratio: {summary['median_ratio']}")
        print()
        print(f"  *** SAMPLE SIZE WARNING: {summary['quarters_covered']} observations cannot distinguish real edge from")
        print("  luck. This is a filter/hit-rate validation using 100% real historical data (real EPS surprises,")
        print("  real price gaps, real historical option prices/OI from Alpaca) -- it is NOT a dollar P&L backtest,")
        print("  since these rules deliberately make no directional assumption (calls vs puts, long vs short).")
        print("  Treat this as evidence toward a decision, not a verdict on whether 'the strategy works.' ***")


# ---- step-by-step case study (all 6 rules, one name, most recent report) --
# Evaluates the MOST RECENT reported quarter as if it were today's candidate:
# rules 1/2/5 use the full N-quarter history (as specified), rule 3 checks
# the run-up BEFORE that most recent report, and rules 4/6 use that report's
# own real historical implied move/OI. This is the exact same underlying
# data/functions as backtest_single_ticker -- just walked through and
# labeled rule-by-rule instead of collapsed into a summary table.

def run_stepwise_case_study(ticker, quarters=REACTION_LOOKBACK_QUARTERS):
    history = fetch_reaction_history(ticker, quarters)
    if not history:
        return {"ticker": ticker, "error": "No reaction history available"}

    quality_pass, quality_detail = evaluate_reaction_quality(history)
    most_recent = history[0]

    runup_flagged, runup_pct = check_preearnings_runup(ticker, most_recent["report_date"])

    report_date = datetime.fromisoformat(most_recent["report_date"]).date()
    if report_date < ALPACA_OPTIONS_DATA_FLOOR:
        implied_info = None
        econ = {"verdict": "no options data", "reason": f"{report_date} predates Alpaca's historical floor ({ALPACA_OPTIONS_DATA_FLOOR})"}
    else:
        implied_info = compute_implied_move_historical(ticker, most_recent["report_date"], most_recent["timing"], most_recent["pre_close"])
        econ = evaluate_options_economics(history, implied_info)

    return {
        "ticker": ticker,
        "history": history,
        "quality_pass": quality_pass,
        "quality_detail": quality_detail,
        "most_recent": most_recent,
        "runup_flagged": runup_flagged,
        "runup_pct": runup_pct,
        "implied_info": implied_info,
        "econ": econ,
    }


def print_stepwise_report(result):
    ticker = result["ticker"]
    print()
    print("=" * 78)
    print(f"{ticker} -- step-by-step per the 6-rule strategy")
    print("=" * 78)
    if result.get("error"):
        print(f"  {result['error']}")
        return

    history = result["history"]
    qd = result["quality_detail"]

    print(f"\nRULE 1 -- last {len(history)} post-earnings moves + EPS surprise (most recent first):")
    for h in history:
        eps = f"{h['eps_surprise_pct']:+.2f}%" if h["eps_surprise_pct"] is not None else "N/A"
        print(f"  {h['report_date']} ({h['timing']})  EPS surprise {eps:>8}  ->  gap {h['gap_pct']:+.2f}%")

    print(f"\nRULE 2 -- reject if under 3/6 positive OR avg |move| under {MIN_AVG_ABS_MOVE_PCT}%:")
    if "positive_count" in qd:
        print(f"  Positive: {qd['positive_count']}/{qd['n']}   Avg |move|: {qd['avg_abs_move_pct']}%   Median |move|: {qd['median_abs_move_pct']}%")
    verdict_line = "PASS -- tradeable event" if result["quality_pass"] else f"REJECT -- {qd.get('reason', 'insufficient data')}"
    print(f"  Verdict: {verdict_line}")

    print(f"\nRULE 3 -- flag if up >{RUNUP_THRESHOLD_PCT}% in the {RUNUP_LOOKBACK_DAYS} days before the report:")
    if result["runup_pct"] is None:
        print("  Could not compute (price data unavailable)")
    else:
        flag = "FLAGGED -- expectations may exceed consensus" if result["runup_flagged"] else "not flagged"
        print(f"  Run-up into {result['most_recent']['report_date']}: {result['runup_pct']:+.2f}%  ({flag})")

    econ = result["econ"]
    print(f"\nRULE 4 -- historical avg move / implied move (most recent report's own real implied move):")
    if econ.get("implied_move_pct") is None:
        print(f"  Could not compute -- {econ.get('reason', 'no options data available')}")
    else:
        print(f"  Implied move (from real {econ['expiration']} ${econ['strike']} straddle, source: {econ['source']}): {econ['implied_move_pct']}%")
        print(f"  Avg historical |move| (same {len(history)}-quarter window as rule 2): {econ['avg_historical_move_pct']}%")
        print(f"  Ratio: {econ['ratio']}  ({'BELOW' if econ['ratio'] is not None and econ['ratio'] < IV_RICH_RATIO_THRESHOLD else 'at/above'} the {IV_RICH_RATIO_THRESHOLD} threshold -- "
              f"{'use shares, options pricing more move than history supports' if econ['ratio'] is not None and econ['ratio'] < IV_RICH_RATIO_THRESHOLD else 'options are reasonably priced vs. history'})")

    print(f"\nRULE 5 -- breakeven as a % move, and how many of the last {len(history)} would have cleared it:")
    if econ.get("breakeven_pct") is None:
        print("  Could not compute (no implied move available)")
    else:
        print(f"  Breakeven: {econ['breakeven_pct']}%")
        for h in history:
            cleared = "cleared" if abs(h["gap_pct"]) >= econ["breakeven_pct"] else "missed"
            print(f"    {h['report_date']}: |{h['gap_pct']:+.2f}%|  -> {cleared}")
        print(f"  {econ['breakeven_hit_count']}/{econ['breakeven_hit_n']} historical quarters would have cleared this breakeven")

    print(f"\nRULE 6 -- open interest (>= {MIN_OPTION_OPEN_INTEREST}) and spread (<= {MAX_SPREAD_PCT_OF_MARK*100:.0f}% of mark):")
    if econ.get("min_open_interest") is None:
        print("  Could not compute (no options data available)")
    else:
        oi_ok = econ["min_open_interest"] >= MIN_OPTION_OPEN_INTEREST
        print(f"  Min open interest (thinner leg): {econ['min_open_interest']}  ({'OK' if oi_ok else 'TOO THIN'})")
        spread = econ.get("spread_pct_of_mark")
        if spread is None:
            print("  Spread: not available from historical data (Alpaca's historical bars are OHLC only, no quotes -- live-screening only)")
        else:
            print(f"  Spread: {spread}% of mark  ({'OK' if spread <= MAX_SPREAD_PCT_OF_MARK * 100 else 'TOO WIDE to reliably exit after a gap'})")

    print(f"\nFINAL VERDICT: {econ.get('verdict', 'no options data')}")
    if not result["quality_pass"]:
        print("  (Note: rule 2 already rejects this name on reaction-quality grounds regardless of the options verdict above.)")
    print()
    print(f"  *** {len(history)} observations, 1 name. Cannot distinguish edge from luck. This uses 100% real data ***")
    print( "  *** (real EPS surprise, real price gaps, real historical option prices/OI) but is a filter/hit-rate  ***")
    print( "  *** check, not a dollar P&L simulation -- no trade direction is assumed.                            ***")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Earnings-reaction screener")
    parser.add_argument("ticker", nargs="?", default=None,
                         help="Ticker to backtest, e.g. AAPL. Omit and use --live to scan the whole active universe instead.")
    parser.add_argument("--quarters", type=int, default=REACTION_LOOKBACK_QUARTERS,
                         help=f"How many reported quarters of history to pull (default {REACTION_LOOKBACK_QUARTERS})")
    parser.add_argument("--stepwise", action="store_true",
                         help="Show the full rule-by-rule walkthrough for the most recent reported quarter, "
                              "instead of the compact per-quarter backtest table")
    parser.add_argument("--live", action="store_true",
                         help="Scan the active universe for tickers reporting within --lookahead-days and rank candidates "
                              "against all 6 rules, instead of backtesting a single ticker")
    parser.add_argument("--lookahead-days", type=int, default=EARNINGS_LOOKAHEAD_DAYS,
                         help=f"--live only: how many days ahead counts as 'reporting soon' (default {EARNINGS_LOOKAHEAD_DAYS})")
    parser.add_argument("--max-candidates", type=int, default=MAX_CANDIDATES,
                         help=f"--live only: cap on final candidates (default {MAX_CANDIDATES})")
    args = parser.parse_args()

    if args.live:
        banner(f"Earnings-reaction screener: live scan, next {args.lookahead_days} days")
        result = run_live_earnings_screener(lookahead_days=args.lookahead_days, quarters=args.quarters,
                                             max_candidates=args.max_candidates)
        print_live_screener_report(result, lookahead_days=args.lookahead_days)
    elif args.ticker:
        banner(f"Earnings-reaction backtest: {args.ticker}")
        if args.stepwise:
            result = run_stepwise_case_study(args.ticker.upper(), quarters=args.quarters)
            print_stepwise_report(result)
        else:
            result = backtest_single_ticker(args.ticker.upper(), quarters=args.quarters)
            print_backtest_report(result)
    else:
        parser.error("Provide a ticker to backtest, or pass --live to scan the active universe")
