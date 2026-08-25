"""Shared rate limit for all yfinance calls across the project.

yfinance has no documented/official rate limit (unlike Alpaca's 200/min) --
it's an unofficial scrape of Yahoo's undocumented endpoints. Before this,
each module that called it either had its own independent, uncoordinated
throttle (universe_scanner.py) or none at all (pipeline.py's main Step 4
loop, premium_screener.py, earnings_reaction_screener.py,
earnings_iron_condor_screener.py, dividend_worker.py) -- meaning the actual
combined request rate across the whole project was never really governed by
anything. This centralizes it into one clock, the same pattern already used
for Gemini (ai_narrative.py._throttle_provider) and SEC EDGAR
(sec_*_worker.py's rate_limited_get), following the same lesson: an
uncoordinated or missing throttle is exactly what caused a real SEC-blocking
incident on this project already.

yf.Ticker(symbol) itself doesn't hit the network -- the actual HTTP call
happens on first access to .info/.history()/.dividends/.calendar/.news/
.fast_info etc. So call throttle_yfinance() immediately before whichever of
those triggers the real request, not at Ticker() construction time.
"""
from __future__ import annotations

import os
import threading
import time

_last_call_time = 0.0
_lock = threading.Lock()


def _get_min_interval_sec() -> float:
    try:
        rps = float(os.getenv("YFINANCE_MAX_RPS", "5"))
    except (TypeError, ValueError):
        rps = 5.0
    return (1.0 / rps) if rps > 0 else 0.0


def throttle_yfinance() -> None:
    global _last_call_time
    min_interval = _get_min_interval_sec()
    if min_interval <= 0:
        return
    with _lock:
        now = time.time()
        elapsed = now - _last_call_time
        wait = min_interval - elapsed
        # Reserve the slot before sleeping so a concurrent caller (if this
        # project ever parallelizes yfinance calls the way NARRATIVE_CONCURRENCY
        # does for Gemini) sees this call's reserved time, not the stale one --
        # see ai_narrative.py's _throttle_provider for the same fix applied there.
        _last_call_time = now + max(wait, 0.0)
    if wait > 0:
        time.sleep(wait)
