"""Measure ETF NAV erosion from reported SEC NAV observations.

N-PORT is monthly rather than daily, so this deliberately reports a measured
monthly NAV result and returns ``unknown`` when the history is too short. It
does not confuse market-price decay with NAV decay.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from .logger import info, success, warning


MIN_HISTORY_DAYS = 330
MIN_OBSERVATIONS = 4


def sync_etf_nav_metrics(db_path="drawdown_analyzer.db"):
    """Calculate one-year NAV metrics from SEC N-PORT and distributions.

    ``status=yes`` means reported NAV fell by at least 5% over the available
    approximately one-year period. This is a conservative avoid flag, not a
    claim that a fund's decline was caused by distributions.
    """
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    cur = conn.cursor()
    cur.execute("""
        SELECT ticker, period_end_date, nav_per_share
        FROM sec_etf_reports
        WHERE form_type IN ('N-PORT', 'NPORT-P', 'NPORT-EX')
          AND nav_per_share IS NOT NULL
          AND nav_per_share > 0
        ORDER BY ticker, period_end_date ASC, filing_date ASC
    """)
    observations = {}
    for ticker, period_end, nav in cur.fetchall():
        if not period_end:
            continue
        # A fund can have duplicate/amended reports for the same period. Keep
        # the last row encountered after the filing-date ordering above.
        observations.setdefault(ticker, {})[period_end] = float(nav)

    # Prefer issuer-published daily NAV history when available. SEC N-PORT is
    # retained as a fallback for funds without an issuer adapter.
    cur.execute("""
        SELECT ticker, nav_date, nav_per_share, source
        FROM etf_nav_history
        WHERE nav_per_share > 0
        ORDER BY ticker, nav_date ASC
    """)
    issuer_observations = {}
    issuer_sources = {}
    for ticker, nav_date, nav, source in cur.fetchall():
        issuer_observations.setdefault(ticker, {})[nav_date] = float(nav)
        issuer_sources[ticker] = source

    cur.execute("SELECT ticker, ex_date, amount_per_share FROM dividend_history")
    distributions = {}
    for ticker, ex_date, amount in cur.fetchall():
        distributions.setdefault(ticker, []).append((ex_date, float(amount)))

    cur.execute("SELECT ticker FROM universe WHERE asset_type = 'ETF'")
    etfs = [row[0] for row in cur.fetchall()]
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    written = 0

    for ticker in etfs:
        active_observations = issuer_observations.get(ticker) or observations.get(ticker, {})
        source = issuer_sources.get(ticker, "SEC N-PORT monthly NAV")
        points = sorted(active_observations.items())
        status = "unknown"
        values = (ticker, None, None, None, None, None, None, None, len(points), status,
                  source, now)

        if len(points) >= MIN_OBSERVATIONS:
            end_date, end_nav = points[-1]
            end_dt = datetime.fromisoformat(end_date)
            target_start = end_dt - timedelta(days=365)
            # Use the first reported NAV on/after the one-year lookback target.
            # This avoids accidentally measuring the entire five-year SEC cache
            # while tolerating monthly reporting dates.
            window = [(d, n) for d, n in points if datetime.fromisoformat(d) >= target_start]
            start_date, start_nav = window[0] if window else points[0]
            window_count = len(window)
            try:
                days = (end_dt - datetime.fromisoformat(start_date)).days
            except ValueError:
                days = 0
            if days >= MIN_HISTORY_DAYS:
                paid = sum(amount for ex_date, amount in distributions.get(ticker, [])
                           if start_date < ex_date <= end_date)
                raw_return = ((end_nav / start_nav) - 1) * 100
                adjusted_return = (((end_nav + paid) / start_nav) - 1) * 100
                # A negative raw NAV return of this size is the avoid signal.
                # Distribution-adjusted return is retained to show whether cash
                # distributions offset the NAV decline.
                status = "yes" if raw_return <= -5.0 else "no"
                values = (ticker, start_date, end_date, start_nav, end_nav,
                          round(raw_return, 4), round(paid, 6),
                          round(adjusted_return, 4), window_count, status,
                          f"{source} + dividend_history", now)

        cur.execute("""
            INSERT INTO etf_nav_metrics (
                ticker, nav_start_date, nav_end_date, nav_start, nav_end,
                raw_nav_return_pct, distributions_per_share,
                distribution_adjusted_return_pct, observation_count, status,
                source, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                nav_start_date=excluded.nav_start_date,
                nav_end_date=excluded.nav_end_date,
                nav_start=excluded.nav_start,
                nav_end=excluded.nav_end,
                raw_nav_return_pct=excluded.raw_nav_return_pct,
                distributions_per_share=excluded.distributions_per_share,
                distribution_adjusted_return_pct=excluded.distribution_adjusted_return_pct,
                observation_count=excluded.observation_count,
                status=excluded.status,
                source=excluded.source,
                updated_at=excluded.updated_at
        """, values)
        written += 1

    conn.commit()
    conn.close()
    success(f"ETF NAV metrics sync complete: {written} ETF(s) evaluated")
    return written
