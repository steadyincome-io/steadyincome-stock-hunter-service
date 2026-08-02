"""Small terminal-style logger for pipeline output."""

from datetime import datetime


def _ts():
    return datetime.now().strftime("%H:%M:%S")


def _emit(level, message):
    print(f"[{_ts()}] [{level}] {message}")


def banner(title):
    print("=" * 78)
    _emit("RUN", title)
    print("=" * 78)


def step(message):
    _emit("STEP", message)


def info(message):
    _emit("INFO", message)


def success(message):
    _emit("OK", message)


def warning(message):
    _emit("WARN", message)


def error(message):
    _emit("ERR", message)


def ticker_start(ticker, message):
    _emit("TICK", f"{ticker}: {message}")


def ticker_done(ticker, message):
    _emit("DONE", f"{ticker}: {message}")


def progress(percent, message):
    pct = max(0, min(100, int(round(percent))))
    _emit("PROG", f"Progress: {pct}% | {message}")
