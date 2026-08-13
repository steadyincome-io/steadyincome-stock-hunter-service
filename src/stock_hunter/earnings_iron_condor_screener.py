"""Iron condor variant of the earnings-reaction strategy -- SHORT premium,
profits when the stock stays WITHIN a range through the reaction. The
mirror-image bet of the long-straddle module
(earnings_reaction_screener.py), built for the exact case that module's
rule 2 rejects: names whose historical reactions are small relative to
what the options market is pricing in.

Reuses that module's data plumbing directly (reaction history, live/
historical implied move via the ATM straddle, Alpaca auth, universe
loading) rather than duplicating it -- only the RULES are reworked, since
every one of them inverts or changes shape for a short-premium, defined-
risk structure:

- Rule 2 (entry gate): straddle rejects "too quiet"; here quiet historical
  moves are exactly what's wanted. The gate instead becomes "is the options
  market pricing meaningfully MORE movement than history supports" --
  ratio (avg historical move / implied move) must be BELOW
  RICH_RATIO_THRESHOLD, the same numeric idea as the straddle module's
  IV_RICH_RATIO_THRESHOLD but used as a requirement to enter, not a
  fallback-to-shares warning. The straddle's directional "3/6 positive"
  check is dropped entirely -- a condor doesn't care which way the stock
  moves, only whether it moves too much, so a directional requirement
  doesn't make sense here (this was already flagged as a weak fit for the
  straddle's own magnitude-only philosophy; it's an even worse fit here).
- New: a single outlier-quarter check (MAX_SINGLE_QUARTER_MOVE_PCT) --
  average move being small doesn't protect against one quarter that
  gapped hard; a condor seller needs to know if that's ever happened.
- Rule 5 (breakeven) becomes two breakevens (short strikes +/- credit
  received), and "cleared" flips to "stayed within" -- a win for the
  straddle is a loss here and vice versa.
- Rule 6 (liquidity) now needs FOUR legs checked (short call, long call,
  short put, long put), not two.
- Strike selection is new: short strikes are placed at SHORT_STRIKE_MOVE_
  MULTIPLE x the implied move away from spot (roughly the edge of the
  market's own expected move), long strikes further out by WING_WIDTH_
  FRACTION x the implied move, defining the max loss.

Entry/exit timing -- a deliberate, non-obvious design point, not copied
from the straddle module (whose "sell in the first 30-60 minutes" rule is
built for a LONG option position and doesn't transfer to short premium,
where the profit comes from IV crush and time decay playing out, not an
immediate directional capture). This is also close to the ONLY mechanically
viable timing using standard listed options, since options markets don't
trade extended hours: you cannot enter later than the prior session's close
before a BMO report (it drops before the market opens), and you cannot
enter later than the report-day close before an AMC report (it drops after
the close). So:
  BMO: enter T-1, 15:00-16:00 ET  ->  exit T,   first 30-45 min after open
  AMC: enter T,   15:00-16:00 ET  ->  exit T+1, first 30-45 min after open
Both work out to a symmetric ~18-hour hold. This does NOT reduce overnight
gap risk -- it's the entire source of the trade's premium. The defined-risk
structure caps how bad a loss can be; it doesn't make the risk small.

Same fail-open-with-a-reason and no-invented-numbers philosophy as the
straddle module throughout.
"""
import statistics
from datetime import date, datetime, timedelta

import requests

from .logger import banner, step, info, success, warning, error
from .premium_screener import fetch_live_price
from .earnings_reaction_screener import (
    REACTION_LOOKBACK_QUARTERS,
    RUNUP_LOOKBACK_DAYS,
    RUNUP_THRESHOLD_PCT,
    ALPACA_OPTIONS_DATA_FLOOR,
    ALPACA_DATA_BASE,
    ALPACA_TRADING_BASE,
    EARNINGS_LOOKAHEAD_DAYS,
    MAX_CANDIDATES,
    _alpaca_headers,
    _yf_symbol,
    _classify_timing,
    _nearest_strike,
    fetch_reaction_history,
    check_preearnings_runup,
    compute_implied_move_live,
    compute_implied_move_historical,
    get_upcoming_earnings_date,
    _load_active_tickers,
)

try:
    import yfinance as yf
except Exception:
    yf = None


# ---- condor-specific thresholds (documented judgment calls) ---------------
# Same numeric value as the straddle module's IV_RICH_RATIO_THRESHOLD, but
# used the opposite way: for the straddle, ratio < 0.7 means "options too
# rich, don't buy." Here, ratio < 0.7 is exactly the ENTRY requirement --
# the market pricing meaningfully more move than history supports is the
# whole edge a premium seller is being paid for.
RICH_RATIO_THRESHOLD = 0.7
# A quiet average doesn't rule out one violent outlier quarter -- reject if
# any single historical quarter moved this much, regardless of the average.
# 15% is a judgment call: roughly 2-3x a "normal" 5-7% single-name earnings
# move, chosen to catch genuine one-off blowups without being so tight that
# a single moderately active quarter disqualifies an otherwise-good name.
MAX_SINGLE_QUARTER_MOVE_PCT = 15.0
# Short strikes sit this many multiples of the implied move away from spot
# -- 1.0x roughly places them at the edge of the market's own 1-SD implied
# move, the standard "sell at the expected-move boundary" condor
# construction. Long strikes (the wings, defining max loss) sit an
# additional WING_WIDTH_FRACTION x implied move beyond that.
SHORT_STRIKE_MOVE_MULTIPLE = 1.0
WING_WIDTH_FRACTION = 0.5
MIN_CONDOR_LEG_OPEN_INTEREST = 200
MAX_CANDIDATES_CONDOR = MAX_CANDIDATES

# Entry/exit timing -- see module docstring for the full reasoning.
ENTRY_WINDOW_ET = "15:00-16:00"
EXIT_WINDOW_MINUTES_AFTER_OPEN = (30, 45)


def evaluate_condor_reaction_quality(history, rich_ratio_threshold=RICH_RATIO_THRESHOLD,
                                      max_single_quarter_pct=MAX_SINGLE_QUARTER_MOVE_PCT):
    """Rule 2 for the condor: no directional requirement (a condor doesn't
    care which way the stock moves). Flags an outlier-quarter risk
    independent of the ratio check below, since that's evaluated once an
    implied move is known -- this only checks what the reaction history
    itself says. Returns (has_outlier, detail_dict)."""
    if len(history) < 2:
        return True, {"reason": f"Only {len(history)} reported quarter(s) of history -- too thin to evaluate", "n": len(history)}
    abs_moves = [abs(h["gap_pct"]) for h in history]
    avg_abs_move = round(float(statistics.mean(abs_moves)), 2)
    median_abs_move = round(float(statistics.median(abs_moves)), 2)
    max_abs_move = round(float(max(abs_moves)), 2)
    has_outlier = max_abs_move >= max_single_quarter_pct
    detail = {
        "n": len(history), "avg_abs_move_pct": avg_abs_move,
        "median_abs_move_pct": median_abs_move, "max_abs_move_pct": max_abs_move,
    }
    if has_outlier:
        detail["reason"] = f"A past quarter moved {max_abs_move}% -- at/above the {max_single_quarter_pct}% outlier flag"
    return has_outlier, detail


def select_condor_strikes(strikes, spot_price, implied_move_pct,
                           short_multiple=SHORT_STRIKE_MOVE_MULTIPLE, wing_fraction=WING_WIDTH_FRACTION):
    """Picks 4 strikes from an available strikes list: short_put < long_put
    < spot < short_call < long_call is NOT required (short_put/short_call
    bracket spot; long legs sit further out). Returns a dict of the 4
    strikes, or None if the chain doesn't have enough strikes on either
    side to build the full structure."""
    implied_move_dollars = spot_price * (implied_move_pct / 100)
    short_call_target = spot_price + implied_move_dollars * short_multiple
    short_put_target = spot_price - implied_move_dollars * short_multiple
    long_call_target = short_call_target + implied_move_dollars * wing_fraction
    long_put_target = short_put_target - implied_move_dollars * wing_fraction

    calls_above = sorted(s for s in strikes if s >= spot_price)
    puts_below = sorted((s for s in strikes if s <= spot_price), reverse=True)
    if not calls_above or not puts_below:
        return None

    short_call = _nearest_strike(calls_above, short_call_target)
    short_put = _nearest_strike(puts_below, short_put_target)
    calls_beyond_short = [s for s in calls_above if s > short_call]
    puts_beyond_short = [s for s in puts_below if s < short_put]
    if not calls_beyond_short or not puts_beyond_short:
        return None
    long_call = _nearest_strike(calls_beyond_short, long_call_target)
    long_put = _nearest_strike(puts_beyond_short, long_put_target)

    return {"short_call": short_call, "long_call": long_call, "short_put": short_put, "long_put": long_put}


def _occ_symbol(ticker, expiration, strike, option_type):
    """Builds a standard OCC option symbol -- TICKER + YYMMDD + C/P + strike*1000, 8 digits."""
    exp_str = datetime.fromisoformat(expiration).strftime("%y%m%d")
    cp = "C" if option_type == "call" else "P"
    strike_str = f"{int(round(strike * 1000)):08d}"
    return f"{ticker}{exp_str}{cp}{strike_str}"


def compute_condor_economics_historical(ticker, report_date_str, timing, spot_price):
    """HISTORICAL path (Alpaca) -- past report, backtest use. Anchors strike
    selection off the same ATM straddle implied move the straddle module
    computes, then prices and checks liquidity on the actual 4 condor legs.
    Returns None (with a logged reason) if anything required is missing --
    never fabricates a number."""
    anchor = compute_implied_move_historical(ticker, report_date_str, timing, spot_price)
    if not anchor:
        return None

    headers = _alpaca_headers()
    if headers is None:
        return None

    report_date = datetime.fromisoformat(report_date_str).date()
    reaction_date = report_date if timing == "BMO" else report_date + timedelta(days=1)
    try:
        resp = requests.get(
            f"{ALPACA_TRADING_BASE}/v2/options/contracts",
            headers=headers,
            params={
                "underlying_symbols": ticker,
                "expiration_date": anchor["expiration"],
                "strike_price_gte": spot_price * 0.75,
                "strike_price_lte": spot_price * 1.25,
                "status": "inactive",
                "limit": 200,
            },
            timeout=15,
        )
        resp.raise_for_status()
        contracts = resp.json().get("option_contracts", [])
        if not contracts:
            warning(f"{ticker}: no contracts found for condor strike selection at {anchor['expiration']}")
            return None

        all_strikes = sorted({float(c["strike_price"]) for c in contracts})
        legs = select_condor_strikes(all_strikes, spot_price, anchor["implied_move_pct"])
        if not legs:
            warning(f"{ticker}: not enough strikes available to build a full condor at {anchor['expiration']}")
            return None

        by_strike_type = {(float(c["strike_price"]), c["type"]): c for c in contracts}
        leg_contracts = {}
        for name, strike in legs.items():
            opt_type = "call" if "call" in name else "put"
            contract = by_strike_type.get((strike, opt_type))
            if not contract:
                warning(f"{ticker}: missing {name} contract at strike {strike}")
                return None
            leg_contracts[name] = contract

        symbols = ",".join(c["symbol"] for c in leg_contracts.values())
        bars_resp = requests.get(
            f"{ALPACA_DATA_BASE}/v1beta1/options/bars",
            headers=headers,
            params={"symbols": symbols, "timeframe": "1Day",
                     "start": (report_date - timedelta(days=5)).isoformat(), "end": report_date.isoformat(), "limit": 20},
            timeout=15,
        )
        bars_resp.raise_for_status()
        bars = bars_resp.json().get("bars", {})

        closes = {}
        for name, contract in leg_contracts.items():
            leg_bars = bars.get(contract["symbol"], [])
            if not leg_bars:
                warning(f"{ticker}: no bars for {name} ({contract['symbol']})")
                return None
            closes[name] = leg_bars[-1]["c"]

        credit = (closes["short_call"] - closes["long_call"]) + (closes["short_put"] - closes["long_put"])
        call_width = legs["long_call"] - legs["short_call"]
        put_width = legs["short_put"] - legs["long_put"]
        max_loss = max(call_width, put_width) - credit

        return {
            "source": "historical (Alpaca)",
            "expiration": anchor["expiration"],
            "legs": legs,
            "leg_symbols": {k: v["symbol"] for k, v in leg_contracts.items()},
            "leg_open_interest": {k: int(float(v.get("open_interest") or 0)) for k, v in leg_contracts.items()},
            "credit": round(credit, 2),
            "max_loss": round(max_loss, 2),
            "breakeven_upper": round(legs["short_call"] + credit, 2),
            "breakeven_lower": round(legs["short_put"] - credit, 2),
            "breakeven_upper_pct": round((legs["short_call"] + credit - spot_price) / spot_price * 100, 2),
            "breakeven_lower_pct": round((legs["short_put"] - credit - spot_price) / spot_price * 100, 2),
            "implied_move_pct": anchor["implied_move_pct"],
        }
    except requests.HTTPError as exc:
        warning(f"{ticker}: Alpaca API error: {exc}")
        return None
    except Exception as exc:
        warning(f"{ticker}: historical condor economics fetch failed: {exc}")
        return None


def compute_condor_economics_live(ticker, report_date_str, timing, spot_price):
    """LIVE path (yfinance) -- upcoming report, live-screening use."""
    if yf is None:
        return None
    anchor = compute_implied_move_live(ticker, report_date_str, timing, spot_price)
    if not anchor:
        return None
    try:
        stock = yf.Ticker(_yf_symbol(ticker))
        chain = stock.option_chain(anchor["expiration"])
        calls, puts = chain.calls, chain.puts
        if calls.empty or puts.empty:
            return None

        all_strikes = sorted(set(calls["strike"].tolist()) | set(puts["strike"].tolist()))
        legs = select_condor_strikes(all_strikes, spot_price, anchor["implied_move_pct"])
        if not legs:
            return None

        def _leg_row(strike, side):
            table = calls if side == "call" else puts
            row = table[table["strike"] == strike]
            return row.iloc[0] if not row.empty else None

        rows = {}
        for name, strike in legs.items():
            side = "call" if "call" in name else "put"
            row = _leg_row(strike, side)
            if row is None:
                return None
            rows[name] = row

        def _mid(row):
            bid, ask = float(row["bid"] or 0), float(row["ask"] or 0)
            return (bid + ask) / 2

        credit = (_mid(rows["short_call"]) - _mid(rows["long_call"])) + (_mid(rows["short_put"]) - _mid(rows["long_put"]))
        call_width = legs["long_call"] - legs["short_call"]
        put_width = legs["short_put"] - legs["long_put"]
        max_loss = max(call_width, put_width) - credit

        return {
            "source": "live (yfinance)",
            "expiration": anchor["expiration"],
            "legs": legs,
            "leg_open_interest": {k: int(rows[k].get("openInterest") or 0) for k in rows},
            "credit": round(credit, 2),
            "max_loss": round(max_loss, 2),
            "breakeven_upper": round(legs["short_call"] + credit, 2),
            "breakeven_lower": round(legs["short_put"] - credit, 2),
            "breakeven_upper_pct": round((legs["short_call"] + credit - spot_price) / spot_price * 100, 2),
            "breakeven_lower_pct": round((legs["short_put"] - credit - spot_price) / spot_price * 100, 2),
            "implied_move_pct": anchor["implied_move_pct"],
        }
    except Exception as exc:
        warning(f"{ticker}: live condor economics fetch failed: {exc}")
        return None


def evaluate_condor_candidate_economics(history, condor_info, min_leg_oi=MIN_CONDOR_LEG_OPEN_INTEREST,
                                         rich_ratio_threshold=RICH_RATIO_THRESHOLD):
    """Combines the ratio entry gate, breakeven-vs-history, and 4-leg
    liquidity into one verdict. Mirrors evaluate_options_economics from the
    straddle module, but every check is condor-shaped."""
    if not condor_info:
        return {"verdict": "no options data", "reason": "Could not price the condor structure"}

    abs_moves = [abs(h["gap_pct"]) for h in history]
    avg_abs_move = float(statistics.mean(abs_moves))
    implied_move_pct = condor_info["implied_move_pct"]
    ratio = round(avg_abs_move / implied_move_pct, 2) if implied_move_pct else None

    # Percentage-space comparison, not dollar levels -- different historical
    # quarters have different price bases (the stock may have been at a very
    # different price 1-2 years ago), so breakevens are converted to a %
    # move relative to spot and compared directly against each quarter's own
    # gap_pct, the same way the straddle module's rule 5 does it.
    lower_pct, upper_pct = condor_info["breakeven_lower_pct"], condor_info["breakeven_upper_pct"]
    stayed_within_count = sum(1 for h in history if lower_pct <= h["gap_pct"] <= upper_pct)

    min_oi = min(condor_info["leg_open_interest"].values())
    oi_ok = min_oi >= min_leg_oi

    if not oi_ok:
        verdict = "no trade -- one or more legs too illiquid to reliably exit"
    elif ratio is None or ratio >= rich_ratio_threshold:
        verdict = "no trade -- options not rich enough vs. history to justify the wing risk"
    elif condor_info["credit"] <= 0:
        verdict = "no trade -- non-positive credit at a realistic fill"
    else:
        verdict = "condor viable"

    return {
        "verdict": verdict,
        "implied_move_pct": implied_move_pct,
        "avg_historical_move_pct": round(avg_abs_move, 2),
        "ratio": ratio,
        "credit": condor_info["credit"],
        "max_loss": condor_info["max_loss"],
        "breakeven_lower": condor_info["breakeven_lower"],
        "breakeven_upper": condor_info["breakeven_upper"],
        "breakeven_lower_pct": lower_pct,
        "breakeven_upper_pct": upper_pct,
        "stayed_within_count": stayed_within_count,
        "stayed_within_n": len(history),
        "legs": condor_info["legs"],
        "min_open_interest": min_oi,
        "expiration": condor_info["expiration"],
        "source": condor_info["source"],
    }


# ---- single-ticker backtest -------------------------------------------------
# Same scope statement as the straddle module: this checks whether each of
# the last N quarters' realized moves would have STAYED WITHIN that event's
# own real historical condor breakevens -- a genuine per-event win/loss
# using real data throughout. Not a dollar P&L simulation (credit collected
# vs. actual loss taken would need to be tracked per-quarter with real
# fills, which the current per-quarter Alpaca query doesn't attempt) --
# it's a hit-rate validation of the entry logic, same honesty boundary as
# the straddle module's backtest.

def backtest_single_ticker_condor(ticker, quarters=REACTION_LOOKBACK_QUARTERS):
    history = fetch_reaction_history(ticker, quarters)
    if not history:
        return {"ticker": ticker, "history": [], "error": "No reaction history available"}

    has_outlier, quality_detail = evaluate_condor_reaction_quality(history)

    per_quarter = []
    for event in history:
        report_date = datetime.fromisoformat(event["report_date"]).date()
        if report_date < ALPACA_OPTIONS_DATA_FLOOR:
            per_quarter.append({**event, "ratio": None, "stayed_within": None,
                                 "skip_reason": f"Before Alpaca's historical options floor ({ALPACA_OPTIONS_DATA_FLOOR})"})
            continue

        condor_info = compute_condor_economics_historical(ticker, event["report_date"], event["timing"], event["pre_close"])
        if not condor_info:
            per_quarter.append({**event, "ratio": None, "stayed_within": None,
                                 "skip_reason": "No usable historical option data for this event's condor"})
            continue

        realized_abs_move = abs(event["gap_pct"])
        ratio = round(realized_abs_move / condor_info["implied_move_pct"], 2) if condor_info["implied_move_pct"] else None
        stayed_within = condor_info["breakeven_lower_pct"] <= event["gap_pct"] <= condor_info["breakeven_upper_pct"]
        per_quarter.append({
            **event, "ratio": ratio, "stayed_within": stayed_within,
            "credit": condor_info["credit"], "max_loss": condor_info["max_loss"],
            "breakeven_lower_pct": condor_info["breakeven_lower_pct"],
            "breakeven_upper_pct": condor_info["breakeven_upper_pct"],
            "min_open_interest": min(condor_info["leg_open_interest"].values()),
            "skip_reason": None,
        })

    covered = [q for q in per_quarter if q["ratio"] is not None]
    summary = None
    if covered:
        summary = {
            "quarters_covered": len(covered), "quarters_total": len(per_quarter),
            "win_count": sum(1 for q in covered if q["stayed_within"]),
            "mean_ratio": round(float(statistics.mean(q["ratio"] for q in covered)), 2),
            "median_ratio": round(float(statistics.median(q["ratio"] for q in covered)), 2),
        }

    return {"ticker": ticker, "history": history, "has_outlier": has_outlier,
            "quality_detail": quality_detail, "per_quarter": per_quarter, "summary": summary}


def print_condor_backtest_report(result):
    ticker = result["ticker"]
    print()
    print(f"=== Iron condor earnings backtest: {ticker} ===")
    if result.get("error"):
        print(f"  {result['error']}")
        return

    qd = result["quality_detail"]
    print(f"Outlier-quarter check ({qd.get('n', 0)} reported quarters):")
    if "avg_abs_move_pct" in qd:
        print(f"  Avg |move| {qd['avg_abs_move_pct']}%, median {qd['median_abs_move_pct']}%, max single quarter {qd['max_abs_move_pct']}%")
    print(f"  {'FLAGGED -- ' + qd['reason'] if result['has_outlier'] else 'No outlier quarter -- OK'}")

    print()
    print("Per-quarter detail (real data; quarters before Alpaca's Feb 2024 floor are skipped, not estimated):")
    header = f"{'Report Date':<12}{'Timing':<7}{'Gap%':>8}{'Ratio':>7}{'Breakeven Range%':>18}  {'Result':<16}"
    print(header)
    print("-" * len(header))
    for q in result["per_quarter"]:
        if q["skip_reason"]:
            print(f"{q['report_date']:<12}{q['timing']:<7}{q['gap_pct']:>8.2f}{'SKIPPED':>7}  {q['skip_reason']}")
        else:
            rng = f"[{q['breakeven_lower_pct']:.1f}, {q['breakeven_upper_pct']:.1f}]"
            res = "WIN (stayed in)" if q["stayed_within"] else "LOSS (breached)"
            print(f"{q['report_date']:<12}{q['timing']:<7}{q['gap_pct']:>8.2f}{q['ratio']:>7.2f}{rng:>18}  {res:<16}")

    summary = result["summary"]
    print()
    if not summary:
        print("No quarters fell within Alpaca's historical options coverage -- no real backtest could be run.")
    else:
        print(f"Summary (n={summary['quarters_covered']} of {summary['quarters_total']} quarters had real Alpaca options data):")
        print(f"  Stayed-within-range rate: {summary['win_count']}/{summary['quarters_covered']}")
        print(f"  Mean ratio (realized/implied): {summary['mean_ratio']}   Median ratio: {summary['median_ratio']}")
        print()
        print(f"  *** SAMPLE SIZE WARNING: {summary['quarters_covered']} observations. This is a win/loss hit-rate ***")
        print("  *** validation using real historical data (real EPS surprises, real gaps, real historical    ***")
        print("  *** option prices/OI) -- NOT a dollar P&L backtest (credit collected vs. realized loss per    ***")
        print("  *** quarter isn't tracked). A high stayed-within rate is a good sign, not a guarantee.        ***")


# ---- live multi-name weekly screener ---------------------------------------

def evaluate_upcoming_condor_candidate(ticker, report_date, timing, quarters=REACTION_LOOKBACK_QUARTERS):
    history = fetch_reaction_history(ticker, quarters)
    if not history:
        return {"ticker": ticker, "report_date": report_date.isoformat(), "timing": timing,
                "status": "rejected", "reason": "No reaction history available"}

    has_outlier, quality_detail = evaluate_condor_reaction_quality(history)
    if has_outlier:
        return {"ticker": ticker, "report_date": report_date.isoformat(), "timing": timing,
                "status": "rejected", "reason": quality_detail.get("reason", "Outlier quarter flagged"),
                "history": history, "quality_detail": quality_detail}

    runup_flagged, runup_pct = check_preearnings_runup(ticker, report_date.isoformat())

    spot = fetch_live_price(ticker)
    if not spot:
        return {"ticker": ticker, "report_date": report_date.isoformat(), "timing": timing,
                "status": "rejected", "reason": "Could not fetch live price",
                "history": history, "quality_detail": quality_detail}

    condor_info = compute_condor_economics_live(ticker, report_date.isoformat(), timing, spot)
    econ = evaluate_condor_candidate_economics(history, condor_info)

    return {"ticker": ticker, "report_date": report_date.isoformat(), "timing": timing,
            "status": "evaluated", "history": history, "quality_detail": quality_detail,
            "runup_flagged": runup_flagged, "runup_pct": runup_pct, "econ": econ}


def run_live_condor_screener(tickers=None, lookahead_days=EARNINGS_LOOKAHEAD_DAYS,
                              quarters=REACTION_LOOKBACK_QUARTERS, max_candidates=MAX_CANDIDATES_CONDOR):
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

    all_results = [evaluate_upcoming_condor_candidate(t, d, timing, quarters) for t, d, timing in upcoming]
    rejected = [r for r in all_results if r["status"] == "rejected"]
    evaluated = [r for r in all_results if r["status"] == "evaluated"]

    tradeable = [r for r in evaluated if r["econ"]["verdict"] == "condor viable"]
    other_evaluated = [r for r in evaluated if r["econ"]["verdict"] != "condor viable"]

    # Rank by ratio ascending (cheaper relative to history = more edge),
    # then credit/max_loss descending (better reward for the risk taken).
    def _rank_key(r):
        econ = r["econ"]
        reward_risk = (econ["credit"] / econ["max_loss"]) if econ.get("max_loss") else 0
        return (econ.get("ratio") or 999, -reward_risk)

    tradeable.sort(key=_rank_key)
    candidates = tradeable[:max_candidates]

    return {"upcoming_count": len(upcoming), "candidates": candidates,
            "other_evaluated": other_evaluated, "rejected": rejected}


def print_live_condor_screener_report(result, lookahead_days=EARNINGS_LOOKAHEAD_DAYS):
    print()
    print("=" * 78)
    print(f"Iron condor earnings screener -- {result['upcoming_count']} tickers reporting within {lookahead_days} days")
    print("=" * 78)

    if not result["candidates"]:
        print("\nNO TRADE -- nothing in the reporting window cleared all rules.")
    else:
        print(f"\n{len(result['candidates'])} candidate(s) (of {result['upcoming_count']} reporting this window), ranked by ratio then reward/risk:")
        header = f"{'Ticker':<8}{'Report':<12}{'Timing':<7}{'Ratio':>7}{'Credit':>8}{'MaxLoss':>9}{'BE Range%':>16}{'MinOI':>7}"
        print(header)
        print("-" * len(header))
        for r in result["candidates"]:
            econ = r["econ"]
            rng = f"[{econ['breakeven_lower_pct']:.1f}, {econ['breakeven_upper_pct']:.1f}]"
            print(f"{r['ticker']:<8}{r['report_date']:<12}{r['timing']:<7}{econ['ratio']:>7.2f}"
                  f"{econ['credit']:>8.2f}{econ['max_loss']:>9.2f}{rng:>16}{econ['min_open_interest']:>7}")
        print()
        print(f"Entry/exit timing: BMO -> enter T-1 {ENTRY_WINDOW_ET} ET, exit T open+{EXIT_WINDOW_MINUTES_AFTER_OPEN[0]}-{EXIT_WINDOW_MINUTES_AFTER_OPEN[1]}min")
        print(f"                   AMC -> enter T {ENTRY_WINDOW_ET} ET, exit T+1 open+{EXIT_WINDOW_MINUTES_AFTER_OPEN[0]}-{EXIT_WINDOW_MINUTES_AFTER_OPEN[1]}min")

    if result["other_evaluated"]:
        print(f"\nCleared the outlier check but not a final candidate ({len(result['other_evaluated'])}):")
        for r in result["other_evaluated"]:
            print(f"  {r['ticker']}: {r['econ']['verdict']}")

    print(f"\nRejected at entry gate/data-availability ({len(result['rejected'])}):")
    for r in result["rejected"][:20]:
        print(f"  {r['ticker']}: {r['reason']}")
    if len(result["rejected"]) > 20:
        print(f"  ... and {len(result['rejected']) - 20} more")

    print()
    print("*** Sample size: each candidate's history is 5-6 quarters. A good ratio/stayed-within pattern is  ***")
    print("*** a real signal, not proof of edge for any single name. This does NOT reduce overnight gap risk ***")
    print("*** -- the position is fully exposed between entry and the report; the defined-risk wings cap how ***")
    print("*** bad a loss can be, they don't make the risk small.                                            ***")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Iron condor earnings-reaction screener")
    parser.add_argument("ticker", nargs="?", default=None,
                         help="Ticker to backtest, e.g. ADI. Omit and use --live to scan the whole active universe instead.")
    parser.add_argument("--quarters", type=int, default=REACTION_LOOKBACK_QUARTERS,
                         help=f"How many reported quarters of history to pull (default {REACTION_LOOKBACK_QUARTERS})")
    parser.add_argument("--live", action="store_true",
                         help="Scan the active universe for tickers reporting within --lookahead-days and rank condor candidates")
    parser.add_argument("--lookahead-days", type=int, default=EARNINGS_LOOKAHEAD_DAYS,
                         help=f"--live only: how many days ahead counts as 'reporting soon' (default {EARNINGS_LOOKAHEAD_DAYS})")
    parser.add_argument("--max-candidates", type=int, default=MAX_CANDIDATES_CONDOR,
                         help=f"--live only: cap on final candidates (default {MAX_CANDIDATES_CONDOR})")
    args = parser.parse_args()

    if args.live:
        banner(f"Iron condor earnings screener: live scan, next {args.lookahead_days} days")
        result = run_live_condor_screener(lookahead_days=args.lookahead_days, quarters=args.quarters,
                                           max_candidates=args.max_candidates)
        print_live_condor_screener_report(result, lookahead_days=args.lookahead_days)
    elif args.ticker:
        banner(f"Iron condor earnings backtest: {args.ticker}")
        result = backtest_single_ticker_condor(args.ticker.upper(), quarters=args.quarters)
        print_condor_backtest_report(result)
    else:
        parser.error("Provide a ticker to backtest, or pass --live to scan the active universe")
