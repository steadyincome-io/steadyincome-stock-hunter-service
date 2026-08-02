"""Drawdown and recovery analytics computed from stored daily price history.

For every security, walks the closing-price series to find every drawdown
episode (peak -> bottom -> recovery) at or beyond a minimum threshold, then
summarizes them per architecture doc section 11.
"""
import json
from datetime import datetime, date
from statistics import mean, median

from .logger import info, warning

RECOVERY_BUCKETS_DAYS = {
    "6_months": 182,
    "1_year": 365,
    "2_years": 730,
    "3_years": 1095,
    "5_years": 1825,
}


def _parse_date(value):
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


def compute_drawdown_events(price_series, threshold_pct=5.0):
    """price_series: list of (date_str, close_price) sorted ascending by date.

    Returns (events, current_drawdown_pct) where each event is a dict.
    """
    if len(price_series) < 2:
        return [], 0.0

    peak_price = price_series[0][1]
    peak_date = price_series[0][0]
    in_drawdown = False
    bottom_price = None
    bottom_date = None
    events = []

    for trade_date, price in price_series[1:]:
        if price >= peak_price:
            if in_drawdown:
                dd_pct = round(((bottom_price - peak_price) / peak_price) * 100, 2)
                if abs(dd_pct) >= threshold_pct:
                    events.append({
                        "peak_date": peak_date,
                        "peak_price": peak_price,
                        "bottom_date": bottom_date,
                        "bottom_price": bottom_price,
                        "drawdown_pct": dd_pct,
                        "recovery_date": trade_date,
                        "recovery_price": price,
                        "days_to_bottom": (_parse_date(bottom_date) - _parse_date(peak_date)).days,
                        "days_underwater": (_parse_date(trade_date) - _parse_date(peak_date)).days,
                        "recovery_duration_days": (_parse_date(trade_date) - _parse_date(bottom_date)).days,
                        "is_ongoing": 0,
                    })
                in_drawdown = False
            peak_price = price
            peak_date = trade_date
            bottom_price = None
            bottom_date = None
        else:
            if bottom_price is None or price < bottom_price:
                bottom_price = price
                bottom_date = trade_date
            dd_pct_now = ((price - peak_price) / peak_price) * 100
            if dd_pct_now <= -threshold_pct:
                in_drawdown = True

    current_drawdown_pct = 0.0
    last_date, last_price = price_series[-1]
    if peak_price:
        current_drawdown_pct = round(((last_price - peak_price) / peak_price) * 100, 2)

    if in_drawdown:
        events.append({
            "peak_date": peak_date,
            "peak_price": peak_price,
            "bottom_date": bottom_date,
            "bottom_price": bottom_price,
            "drawdown_pct": round(((bottom_price - peak_price) / peak_price) * 100, 2),
            "recovery_date": None,
            "recovery_price": None,
            "days_to_bottom": (_parse_date(bottom_date) - _parse_date(peak_date)).days,
            "days_underwater": (_parse_date(last_date) - _parse_date(peak_date)).days,
            "recovery_duration_days": None,
            "is_ongoing": 1,
        })

    return events, current_drawdown_pct


def summarize_drawdowns(ticker, events, current_drawdown_pct, years_of_history):
    completed = [e for e in events if not e["is_ongoing"]]
    magnitudes = [abs(e["drawdown_pct"]) for e in events]
    recovery_days = [e["recovery_duration_days"] for e in completed if e["recovery_duration_days"] is not None]

    recovery_probability = {}
    if completed:
        for label, window_days in RECOVERY_BUCKETS_DAYS.items():
            recovered_within = sum(1 for d in recovery_days if d <= window_days)
            recovery_probability[label] = round(recovered_within / len(completed), 3)

    summary = {
        "ticker": ticker,
        "completed_drawdowns": len(completed),
        "drawdowns_over_10pct": sum(1 for m in magnitudes if m >= 10),
        "drawdowns_over_20pct": sum(1 for m in magnitudes if m >= 20),
        "drawdowns_over_30pct": sum(1 for m in magnitudes if m >= 30),
        "drawdowns_over_40pct": sum(1 for m in magnitudes if m >= 40),
        "avg_drawdown_pct": round(mean(magnitudes), 2) if magnitudes else 0.0,
        "median_drawdown_pct": round(median(magnitudes), 2) if magnitudes else 0.0,
        "worst_drawdown_pct": round(max(magnitudes), 2) if magnitudes else 0.0,
        "avg_recovery_days": round(mean(recovery_days), 1) if recovery_days else None,
        "longest_recovery_days": max(recovery_days) if recovery_days else None,
        "current_drawdown_pct": current_drawdown_pct,
        "years_of_history": round(years_of_history, 2),
        "recovery_probability_json": json.dumps(recovery_probability),
    }
    return summary


def compute_and_store_drawdowns(cursor, ticker, threshold_pct=5.0):
    """Read this ticker's stored price_history, compute drawdown events/summary, persist both."""
    cursor.execute(
        "SELECT trade_date, close_price FROM price_history WHERE ticker = ? ORDER BY trade_date ASC",
        (ticker,),
    )
    rows = cursor.fetchall()
    if len(rows) < 2:
        return None

    price_series = [(r[0], float(r[1])) for r in rows]
    events, current_drawdown_pct = compute_drawdown_events(price_series, threshold_pct=threshold_pct)

    first_date = _parse_date(price_series[0][0])
    last_date = _parse_date(price_series[-1][0])
    years_of_history = max((last_date - first_date).days / 365.25, 0.01)

    cursor.execute("DELETE FROM drawdown_events WHERE ticker = ?", (ticker,))
    for e in events:
        cursor.execute("""
            INSERT OR IGNORE INTO drawdown_events (
                ticker, peak_date, peak_price, bottom_date, bottom_price,
                drawdown_pct, recovery_date, recovery_price,
                days_to_bottom, days_underwater, recovery_duration_days, is_ongoing
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ticker, str(e["peak_date"]), e["peak_price"], str(e["bottom_date"]), e["bottom_price"],
            e["drawdown_pct"], str(e["recovery_date"]) if e["recovery_date"] else None, e["recovery_price"],
            e["days_to_bottom"], e["days_underwater"], e["recovery_duration_days"], e["is_ongoing"],
        ))

    summary = summarize_drawdowns(ticker, events, current_drawdown_pct, years_of_history)
    cursor.execute("""
        INSERT INTO drawdown_summary (
            ticker, completed_drawdowns, drawdowns_over_10pct, drawdowns_over_20pct,
            drawdowns_over_30pct, drawdowns_over_40pct, avg_drawdown_pct, median_drawdown_pct,
            worst_drawdown_pct, avg_recovery_days, longest_recovery_days, current_drawdown_pct,
            years_of_history, recovery_probability_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(ticker) DO UPDATE SET
            completed_drawdowns=excluded.completed_drawdowns,
            drawdowns_over_10pct=excluded.drawdowns_over_10pct,
            drawdowns_over_20pct=excluded.drawdowns_over_20pct,
            drawdowns_over_30pct=excluded.drawdowns_over_30pct,
            drawdowns_over_40pct=excluded.drawdowns_over_40pct,
            avg_drawdown_pct=excluded.avg_drawdown_pct,
            median_drawdown_pct=excluded.median_drawdown_pct,
            worst_drawdown_pct=excluded.worst_drawdown_pct,
            avg_recovery_days=excluded.avg_recovery_days,
            longest_recovery_days=excluded.longest_recovery_days,
            current_drawdown_pct=excluded.current_drawdown_pct,
            years_of_history=excluded.years_of_history,
            recovery_probability_json=excluded.recovery_probability_json,
            updated_at=datetime('now')
    """, (
        summary["ticker"], summary["completed_drawdowns"], summary["drawdowns_over_10pct"],
        summary["drawdowns_over_20pct"], summary["drawdowns_over_30pct"], summary["drawdowns_over_40pct"],
        summary["avg_drawdown_pct"], summary["median_drawdown_pct"], summary["worst_drawdown_pct"],
        summary["avg_recovery_days"], summary["longest_recovery_days"], summary["current_drawdown_pct"],
        summary["years_of_history"], summary["recovery_probability_json"],
    ))

    return summary


def drawdown_opportunity_score(summary):
    """0-100: how historically unusual/deep is the current drawdown vs this ticker's own history."""
    if not summary:
        return 50
    current = abs(summary.get("current_drawdown_pct") or 0)
    avg = summary.get("avg_drawdown_pct") or 0
    worst = summary.get("worst_drawdown_pct") or 0
    if current < 5:
        return 20
    score = 40 + (current / max(avg, 1)) * 15
    if worst > 0:
        score += (current / worst) * 20
    return max(0, min(100, int(round(score))))
