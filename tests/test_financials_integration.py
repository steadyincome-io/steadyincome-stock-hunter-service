#!/usr/bin/env python3
"""
Integration test for one stock, one real SEC filing, and one real external LLM write.

This uses a temporary SQLite database, a live SEC filing URL, and the configured
LLM provider. It is intended to validate the full parser + LLM + DB write path.
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


def _load_local_env():
    env_path = os.path.join(ROOT, ".env")
    if not os.path.exists(env_path):
        return
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


_load_local_env()

from stock_hunter.schema import init_db
from stock_hunter import sec_financials_worker as sfw


def _real_llm_skip_reason():
    if os.getenv("RUN_REAL_LLM_TESTS") != "1":
        return "Set RUN_REAL_LLM_TESTS=1 to enable the real LLM test."

    provider = (os.getenv("NARRATIVE_PROVIDER") or "").strip().lower()
    if not provider:
        return "Missing NARRATIVE_PROVIDER. Set it to openai, cohere, or nim."

    if provider == "openai" and not os.getenv("OPENAI_API_KEY"):
        return "NARRATIVE_PROVIDER=openai requires OPENAI_API_KEY."
    if provider == "cohere" and not os.getenv("COHERE_API_KEY"):
        return "NARRATIVE_PROVIDER=cohere requires COHERE_API_KEY."
    if provider == "nim" and not os.getenv("NVIDIA_NIM_API_KEY"):
        return "NARRATIVE_PROVIDER=nim requires NVIDIA_NIM_API_KEY."
    if provider not in {"openai", "cohere", "nim"}:
        return f"Unsupported NARRATIVE_PROVIDER={provider!r}. Use openai, cohere, or nim."

    return None


def _print_summary_table(row):
    headers = [
        "risk_score",
        "risk_summary",
        "md_a_summary",
        "full_sentiment",
        "comprehensive_summary",
        "narrative_mda",
        "narrative_risk_factors",
        "narrative_legal",
        "narrative_commitments",
        "narrative_buybacks",
        "narrative_liquidity",
        "narrative_subsequent",
    ]
    values = ["" if value is None else str(value) for value in row]
    widths = [max(len(h), len(v)) for h, v in zip(headers, values)]
    line = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    header_row = "| " + " | ".join(h.ljust(w) for h, w in zip(headers, widths)) + " |"
    value_row = "| " + " | ".join(v.ljust(w) for v, w in zip(values, widths)) + " |"
    print("\n[summary]")
    print(line)
    print(header_row)
    print(line)
    print(value_row)
    print(line)


class FinancialIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="stock_hunter_financials_")
        self.db_path = os.path.join(self.temp_dir, "integration.db")
        init_db(self.db_path)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM universe WHERE ticker != 'AAPL'")
        cursor.execute("DELETE FROM sec_financials")
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_one_stock_one_filing_real_external_llm(self):
        reason = _real_llm_skip_reason()
        if reason:
            print(f"[skip] {reason}")
            self.skipTest(reason)

        provider = (os.getenv("NARRATIVE_PROVIDER") or "openai").strip().lower()

        # Run end-to-end sync to pull live CIK mapping, fetch filings from SEC EDGAR,
        # extract narratives, and process with real external LLM.
        touched = sfw.sync_10k_10q_financials(self.db_path, years_back=1, reset_financials=True)
        self.assertGreater(touched, 0, "Expected at least 1 filing to be synced and processed with LLM")

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        row = cursor.execute(
            """
            SELECT
                risk_score,
                risk_summary,
                md_a_summary,
                full_sentiment,
                comprehensive_summary,
                narrative_mda,
                narrative_risk_factors,
                narrative_legal,
                narrative_commitments,
                narrative_buybacks,
                narrative_liquidity,
                narrative_subsequent
            FROM sec_financials
            WHERE ticker = 'AAPL' AND risk_score IS NOT NULL
            ORDER BY filing_date DESC
            LIMIT 1
            """
        ).fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertIsInstance(row[0], int)
        self.assertGreaterEqual(row[0], 0)
        self.assertLessEqual(row[0], 100)
        self.assertTrue(row[1])
        self.assertTrue(row[2])
        self.assertTrue(row[3])
        self.assertTrue(row[4])
        narrative_sections = [row[5], row[6], row[7], row[8], row[9], row[10], row[11]]
        self.assertTrue(any(narrative_sections), "At least one narrative section blob should be present")
        self.assertIn(provider, {"openai", "cohere", "nim"})
        _print_summary_table(row)


if __name__ == "__main__":
    unittest.main()
