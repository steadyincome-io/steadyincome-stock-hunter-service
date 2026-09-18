from datetime import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from stock_hunter.dividend_worker import (
    _extract_mongo_dividends,
    _normalise_mongo_date,
)


def test_normalise_mongo_date_accepts_iso_and_datetime():
    assert _normalise_mongo_date("2026-01-15T00:00:00Z") == "2026-01-15"
    assert _normalise_mongo_date(datetime(2026, 2, 1, 12, 30)) == "2026-02-01"


def test_extract_mongo_nested_dividend_document():
    document = {
        "symbol": "vym",
        "history": [
            {"ex_date": "2026-03-20", "amount_per_share": 0.88},
            {"dividendDate": "2026-06-19", "dividend": "0.91"},
        ],
    }
    assert _extract_mongo_dividends(document) == [
        ("VYM", "2026-03-20", 0.88),
        ("VYM", "2026-06-19", 0.91),
    ]


def test_extract_mongo_single_payment_document():
    assert _extract_mongo_dividends({
        "ticker": "AAPL",
        "date": "2026-02-12",
        "amount": 0.25,
    }) == [("AAPL", "2026-02-12", 0.25)]
