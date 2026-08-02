import requests
import sqlite3
import time
import json
import struct
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from .schema import migrate_db
from .sec_edgar_worker import fetch_sec_cik_mapping
from .sec_narrative_worker import fetch_narratives_for_filing
from .logger import banner, step, info, success, warning, error, ticker_start, ticker_done, progress
from .ai_narrative import (
    score_risk_factors,
    summarize_mda,
    sentiment_from_text,
    summarize_legal,
    summarize_commitments,
    summarize_buybacks,
    summarize_liquidity,
    summarize_subsequent,
    get_comprehensive_narrative_analysis,
)

SEC_USER_AGENT = "DrawdownAnalyzer Research research@drawdownanalyzer.com"
HEADERS = {"User-Agent": SEC_USER_AGENT}

COMPANYFACTS_CACHE_DIR = Path(__file__).resolve().parents[2] / "cache" / "sec_companyfacts"

_last_request_time = 0.0


def rate_limited_get(url, headers=HEADERS, timeout=10, min_interval=0.15):
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_request_time = time.time()
    return requests.get(url, headers=headers, timeout=timeout)


def _days_back_cutoff(days_back: int) -> str:
    return (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")


def _normalize_cik(cik_str) -> str:
    return str(cik_str).zfill(10)


def _cache_companyfacts_path(ticker: str, cik_str: str) -> Path:
    return COMPANYFACTS_CACHE_DIR / f"{ticker.upper()}_{_normalize_cik(cik_str)}.json"


def get_companyfacts_cached(ticker: str, cik_str: str, max_age_days: int = 30):
    cik = _normalize_cik(cik_str)
    cache_path = _cache_companyfacts_path(ticker, cik)
    COMPANYFACTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if cache_path.exists():
        age_seconds = time.time() - cache_path.stat().st_mtime
        if age_seconds < max_age_days * 24 * 3600:
            try:
                with cache_path.open("r", encoding="utf-8") as handle:
                    return json.load(handle)
            except Exception:
                pass

    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    try:
        resp = rate_limited_get(url, headers=HEADERS, timeout=20, min_interval=0.3)
        if resp.status_code != 200:
            return None
        data = resp.json()
        try:
            with cache_path.open("w", encoding="utf-8") as handle:
                json.dump(data, handle)
        except Exception:
            pass
        return data
    except Exception:
        return None


def _decode_fact_value(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, bytes):
        if len(value) == 8:
            try:
                return int.from_bytes(value, "little", signed=True)
            except Exception:
                try:
                    return struct.unpack("<d", value)[0]
                except Exception:
                    return None
        try:
            return value.decode("utf-8")
        except Exception:
            return None
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _pick_fact_value(facts, tag_list, as_of_date=None, units=None):
    """Pick the freshest reported value for a business concept across every
    candidate XBRL tag name, rather than the first tag that has any data at
    all. Companies periodically rename/retire us-gaap concepts (e.g. Apple
    moved dividends from PaymentsOfDividendsCommonStock to PaymentsOfDividends
    around 2018); the old tag can still resolve to an ancient-but-valid
    value that would otherwise silently win just for appearing first.
    """
    if not facts or "facts" not in facts:
        return None
    usgaap = facts["facts"].get("us-gaap", {})
    if not usgaap:
        return None
    cutoff = pd.to_datetime(as_of_date) if as_of_date else None
    desired_units = units or ["USD", "pure", "USD/shares", "USD/share"]
    allowed_forms = {"10-Q", "10-K", "10-Q/A", "10-K/A"}

    best_row = None
    for tag in tag_list:
        tag_payload = usgaap.get(tag)
        if not tag_payload:
            continue
        tag_units = tag_payload.get("units", {})
        for unit in desired_units:
            entries = tag_units.get(unit)
            if not entries:
                continue
            df = pd.DataFrame(entries)
            if df.empty or "val" not in df.columns:
                continue
            if "form" in df.columns:
                df = df[df["form"].isin(allowed_forms)]
            if df.empty:
                continue
            if "end" in df.columns:
                df["end"] = pd.to_datetime(df["end"], errors="coerce")
            if "filed" in df.columns:
                df["filed"] = pd.to_datetime(df["filed"], errors="coerce")
            if cutoff is not None:
                if "end" in df.columns:
                    df = df[df["end"].notna() & (df["end"] <= cutoff)]
                if "filed" in df.columns:
                    df = df[df["filed"].isna() | (df["filed"] <= cutoff)]
            if df.empty:
                continue
            sort_cols = [col for col in ("end", "filed") if col in df.columns]
            if sort_cols:
                df = df.sort_values(sort_cols)
            if "end" in df.columns:
                df = df.drop_duplicates(subset=["end"], keep="last")
            row = df.iloc[-1]
            value = _decode_fact_value(row.get("val"))
            if value is None:
                continue

            row_end = row.get("end") if "end" in df.columns else None
            row_filed = row.get("filed") if "filed" in df.columns else None
            sort_key = (
                row_end if row_end is not None and pd.notna(row_end) else pd.Timestamp.min,
                row_filed if row_filed is not None and pd.notna(row_filed) else pd.Timestamp.min,
            )
            if best_row is None or sort_key > best_row[0]:
                best_row = (sort_key, value)

    return best_row[1] if best_row else None


def _extract_numeric_fundamentals_from_companyfacts(ticker: str, cik_str: str, filing_date: str, companyfacts=None) -> dict:
    facts = companyfacts if companyfacts is not None else get_companyfacts_cached(ticker, cik_str)
    if not facts:
        return {}

    fields = {
        "revenue_usd": (["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "RevenueFromContractWithCustomerExcludingTax"], ["USD"]),
        "net_income_usd": (["NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic", "ProfitLoss"], ["USD"]),
        "operating_income_usd": (["OperatingIncomeLoss"], ["USD"]),
        "total_assets_usd": (["Assets"], ["USD"]),
        "total_liabilities_usd": (["Liabilities"], ["USD"]),
        "stockholders_equity_usd": (["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"], ["USD"]),
        "total_debt_usd": (
            [
                "LongTermDebtAndCapitalLeaseObligations",
                "LongTermDebtAndFinanceLeaseObligations",
                "LongTermDebtNoncurrent",
                "DebtAndCapitalLeaseObligations",
                "LongTermDebtCurrent",
                "ShortTermBorrowings",
                "CurrentPortionOfLongTermDebt",
            ],
            ["USD"],
        ),
        "eps_diluted": (["EarningsPerShareDiluted", "EarningsPerShareDilutedAndBasic", "EarningsPerShareBasicAndDiluted"], ["USD/shares", "USD/share", "pure", "USD"]),
        "current_assets_usd": (["AssetsCurrent"], ["USD"]),
        "current_liabilities_usd": (["LiabilitiesCurrent"], ["USD"]),
        "retained_earnings_usd": (["RetainedEarningsAccumulatedDeficit"], ["USD"]),
        "operating_cash_flow_usd": (["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"], ["USD"]),
        "cash_usd": (["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"], ["USD"]),
        "shares_outstanding": (["CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding", "WeightedAverageNumberOfDilutedSharesOutstanding"], ["shares", "USD/shares"]),
        "capex_usd": (["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"], ["USD"]),
        "depreciation_amortization_usd": (["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet", "DepreciationAndAmortization"], ["USD"]),
        "dividends_paid_usd": (["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"], ["USD"]),
    }
    result = {}
    for column, (tags, units) in fields.items():
        result[column] = _pick_fact_value(facts, tags, as_of_date=filing_date, units=units)
    return result


def _populate_fundamentals_for_row(cursor, ticker: str, cik_str: str, filing_date: str, rowid: int, companyfacts_cache: dict) -> dict:
    companyfacts = companyfacts_cache.get(ticker)
    if ticker not in companyfacts_cache:
        companyfacts = get_companyfacts_cached(ticker, cik_str)
        companyfacts_cache[ticker] = companyfacts

    update_values = {
        key: value
        for key, value in _extract_numeric_fundamentals_from_companyfacts(
            ticker,
            cik_str,
            filing_date,
            companyfacts=companyfacts,
        ).items()
        if value is not None
    }
    equity = update_values.get("stockholders_equity_usd")
    debt = update_values.get("total_debt_usd")
    if debt is not None:
        if equity and equity != 0:
            update_values["debt_to_equity_ratio"] = round(float(debt) / float(equity), 4)
        else:
            update_values["debt_to_equity_ratio"] = None
    if not update_values:
        return {"source": "SEC companyfacts", "updated": False, "fields": []}

    set_clause = ", ".join(f"{column} = ?" for column in update_values)
    cursor.execute(
        f"""
        UPDATE sec_financials
        SET {set_clause}
        WHERE rowid = ?
        """,
        (*update_values.values(), rowid),
    )
    return {"source": "SEC companyfacts", "updated": cursor.rowcount > 0, "fields": list(update_values.keys())}


def fetch_10k_10q_filings(ticker, cik_str, days_back=365):
    """Fetch all 10-K and 10-Q filings for a stock from the last N days."""
    url = f"https://data.sec.gov/submissions/CIK{cik_str}.json"
    filings = []
    cutoff_date = _days_back_cutoff(days_back)

    try:
        resp = rate_limited_get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return []

        data = resp.json()
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        report_dates = recent.get("reportDate", [])
        accession_numbers = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])
        doc_descs = recent.get("primaryDocDescription", [])

        for i, form in enumerate(forms):
            if form not in ("10-K", "10-Q"):
                continue

            f_date = filing_dates[i] if i < len(filing_dates) else ""
            if f_date and f_date < cutoff_date:
                continue

            acc_num = accession_numbers[i] if i < len(accession_numbers) else ""
            acc_no_hyphens = acc_num.replace("-", "")
            p_doc = primary_docs[i] if i < len(primary_docs) else ""
            report_url = ""
            if acc_no_hyphens and p_doc:
                report_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik_str)}/{acc_no_hyphens}/{p_doc}"

            p_date = report_dates[i] if i < len(report_dates) else f_date
            doc_desc = doc_descs[i] if i < len(doc_descs) else form
            filing = {
                "ticker": ticker,
                "cik": cik_str,
                "form_type": form,
                "filing_date": f_date,
                "period_end_date": p_date,
                "accession_number": acc_num,
                "primary_doc_description": doc_desc,
                "report_url": report_url,
            }
            filings.append(filing)
    except Exception as e:
        error(f"{ticker}: error fetching 10-K/10-Q filings: {e}")

    filings.sort(key=lambda item: (item["filing_date"], item["form_type"]), reverse=True)
    return filings


def _get_financial_rowid(cursor, ticker: str, form_type: str, filing_date: str, accession_number: str) -> int | None:
    cursor.execute(
        """
        SELECT rowid
        FROM sec_financials
        WHERE ticker = ? AND form_type = ? AND filing_date = ? AND accession_number = ?
        """,
        (ticker, form_type, filing_date, accession_number),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def _extract_narrative_text(json_blob):
    """Extract plain text from narrative JSON blob; return empty string if invalid."""
    if not json_blob:
        return ""
    try:
        data = json.loads(json_blob)
        if isinstance(data, dict) and "text" in data:
            return data["text"]
    except (json.JSONDecodeError, TypeError):
        pass
    return ""


def _ensure_narratives_loaded(conn, cursor, rowid, report_url, narrative_blobs):
    """Fetch and persist narrative sections when they are missing."""
    if not report_url or all(narrative_blobs.values()):
        return narrative_blobs

    fetched = fetch_narratives_for_filing(report_url)
    if not fetched:
        return narrative_blobs

    updates = {col: fetched.get(col) for col in narrative_blobs.keys() if fetched.get(col)}
    if not updates:
        return narrative_blobs

    set_clause = ", ".join(f"{col} = ?" for col in updates)
    cursor.execute(
        f"UPDATE sec_financials SET {set_clause} WHERE rowid = ?",
        (*updates.values(), rowid),
    )
    conn.commit()
    narrative_blobs.update(updates)
    return narrative_blobs


def sync_10k_10q_financials(db_path, days_back=365, reset_financials=False, resume_llm_only=False):
    """
    Process 10-K and 10-Q filings to derive LLM-based narrative metrics:
    - risk_score, risk_summary, risk_sentiment
    - md_a_summary
    - full_sentiment (overall sentiment)
    - comprehensive_summary (JSON string of bullet points)
    Updates sec_financials table.
    Returns number of records updated.
    """
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    cursor = conn.cursor()
    migrate_db(db_path)
    companyfacts_cache = {}

    if resume_llm_only:
        if reset_financials:
            conn.close()
            raise ValueError("resume_llm_only cannot be used with reset_financials")
        info("Resuming from existing sec_financials rows; skipping SEC fetch and reseed")
        seeded_rows = 0
    else:
        if reset_financials:
            warning("Resetting sec_financials table before refresh")
            cursor.execute("DELETE FROM sec_financials")
        conn.commit()
        success("sec_financials table cleared")
        cik_map = fetch_sec_cik_mapping()
        if not cik_map:
            warning("SEC CIK map unavailable")
            conn.close()
            return 0

        cursor.execute("SELECT ticker FROM universe WHERE asset_type = 'Stock' AND status = 'active'")
        stock_tickers = [row[0] for row in cursor.fetchall()]
        seeded_rows = 0

        total_stock_tickers = len(stock_tickers)
        for index, ticker in enumerate(stock_tickers, start=1):
            clean_ticker = ticker.replace('.', '-')
            cik = cik_map.get(clean_ticker)
            if not cik:
                progress(
                    (index / max(total_stock_tickers, 1)) * 20,
                    f"Phase 1/5: SEC filing sync | Ticker {index}/{total_stock_tickers}: {ticker} skipped (no CIK mapping)",
                )
                continue

            ticker_start(ticker, "fetching 10-K / 10-Q filings")
            filings = fetch_10k_10q_filings(ticker, cik, days_back=days_back)
            if not filings:
                ticker_done(ticker, "no recent 10-K / 10-Q filings found")
                progress(
                    (index / max(total_stock_tickers, 1)) * 20,
                    f"Phase 1/5: SEC filing sync | Ticker {index}/{total_stock_tickers}: {ticker} complete",
                )
                continue

            seeded_for_ticker = 0
            for filing in filings:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO sec_financials (
                        ticker, cik, form_type, filing_date, period_end_date,
                        accession_number, primary_doc_description, report_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        filing["ticker"],
                        filing["cik"],
                        filing["form_type"],
                        filing["filing_date"],
                        filing["period_end_date"],
                        filing["accession_number"],
                        filing["primary_doc_description"],
                        filing["report_url"],
                    ),
                )
                if cursor.rowcount > 0:
                    seeded_rows += 1
                    seeded_for_ticker += 1

                cursor.execute(
                    """
                    UPDATE sec_financials
                    SET period_end_date = COALESCE(NULLIF(period_end_date, ''), ?),
                        primary_doc_description = COALESCE(NULLIF(primary_doc_description, ''), ?),
                        report_url = COALESCE(NULLIF(report_url, ''), ?)
                    WHERE ticker = ? AND form_type = ? AND filing_date = ? AND accession_number = ?
                    """,
                    (
                        filing["period_end_date"],
                        filing["primary_doc_description"],
                        filing["report_url"],
                        filing["ticker"],
                        filing["form_type"],
                        filing["filing_date"],
                        filing["accession_number"],
                    ),
                )

                narratives = fetch_narratives_for_filing(filing["report_url"])
                updates = {col: narratives.get(col) for col in narratives.keys() if narratives.get(col)}
                if updates:
                    set_clause = ", ".join(f"{col} = ?" for col in updates)
                    cursor.execute(
                        f"""
                        UPDATE sec_financials
                        SET {set_clause}
                        WHERE ticker = ? AND form_type = ? AND filing_date = ? AND accession_number = ?
                        """,
                        (*updates.values(), filing["ticker"], filing["form_type"], filing["filing_date"], filing["accession_number"]),
                    )

                rowid = _get_financial_rowid(cursor, filing["ticker"], filing["form_type"], filing["filing_date"], filing["accession_number"])
                if rowid:
                    fundamentals_result = _populate_fundamentals_for_row(
                        cursor,
                        filing["ticker"],
                        filing["cik"],
                        filing["filing_date"],
                        rowid,
                        companyfacts_cache,
                    )
                    if fundamentals_result.get("updated"):
                        info(
                            f"{ticker}: fundamentals updated for {filing['form_type']} {filing['filing_date']} "
                            f"via {fundamentals_result.get('source')} ({', '.join(fundamentals_result.get('fields', []))})"
                        )

            conn.commit()
            ticker_done(ticker, f"seeded {seeded_for_ticker} filings")
            progress(
                (index / max(total_stock_tickers, 1)) * 20,
                f"Phase 1/5: SEC filing sync | Ticker {index}/{total_stock_tickers}: {ticker} complete",
            )

    # Determine cutoff date
    cutoff_date = _days_back_cutoff(days_back)

    # Select only the latest 10-K per ticker; rows without narrative text will be skipped later.
    banner("Step 1b/5: LLM narrative scoring pass" + (" (resume mode)" if resume_llm_only else ""))
    query = """
        SELECT rowid, ticker, form_type, filing_date, report_url,
               narrative_mda, narrative_risk_factors, narrative_legal,
               narrative_commitments, narrative_buybacks, narrative_liquidity,
               narrative_subsequent
        FROM sec_financials
        WHERE form_type = '10-K'
          AND filing_date >= ?
          AND filing_date = (
              SELECT MAX(filing_date)
              FROM sec_financials AS latest
              WHERE latest.ticker = sec_financials.ticker
                AND latest.form_type = '10-K'
                AND latest.filing_date >= ?
          )
    """
    params = [cutoff_date, cutoff_date]
    if resume_llm_only:
        query += """
          AND (
                risk_score IS NULL
             OR risk_summary IS NULL
             OR risk_sentiment IS NULL
             OR md_a_summary IS NULL
             OR full_sentiment IS NULL
             OR comprehensive_summary IS NULL
          )
        """
    cursor.execute(query, params)
    rows = cursor.fetchall()
    updated = 0
    if resume_llm_only:
        info(f"Resume mode selected {len(rows)} incomplete filings for LLM processing")

    for row in rows:
        (
            rowid,
            ticker,
            form_type,
            filing_date,
            report_url,
            nj_mda,
            nj_risk,
            nj_legal,
            nj_commit,
            nj_buybacks,
            nj_liquidity,
            nj_subsequent,
        ) = row

        narrative_blobs = {
            "narrative_mda": nj_mda,
            "narrative_risk_factors": nj_risk,
            "narrative_legal": nj_legal,
            "narrative_commitments": nj_commit,
            "narrative_buybacks": nj_buybacks,
            "narrative_liquidity": nj_liquidity,
            "narrative_subsequent": nj_subsequent,
        }
        narrative_blobs = _ensure_narratives_loaded(conn, cursor, rowid, report_url, narrative_blobs)

        # Extract raw text
        mda_text = _extract_narrative_text(narrative_blobs["narrative_mda"])
        risk_text = _extract_narrative_text(narrative_blobs["narrative_risk_factors"])
        legal_text = _extract_narrative_text(narrative_blobs["narrative_legal"])
        commit_text = _extract_narrative_text(narrative_blobs["narrative_commitments"])
        buybacks_text = _extract_narrative_text(narrative_blobs["narrative_buybacks"])
        liquidity_text = _extract_narrative_text(narrative_blobs["narrative_liquidity"])
        subsequent_text = _extract_narrative_text(narrative_blobs["narrative_subsequent"])

        # Skip if no meaningful text at all
        if not any([mda_text, risk_text, legal_text, commit_text, buybacks_text, liquidity_text, subsequent_text]):
            continue

        # Risk factors: score, summary, sentiment
        risk_result = {"score": 50, "summary": "No risk factors provided.", "sentiment": "neutral"}
        if risk_text.strip():
            risk_result = score_risk_factors(
                risk_text,
                label=f"{ticker} {form_type} {filing_date} risk_factors",
            )

        # MDA summary
        mda_summary = ["No MD&A provided."]
        if mda_text.strip():
            mda_summary = summarize_mda(
                mda_text,
                label=f"{ticker} {form_type} {filing_date} mda",
            )

        # Legal summary
        legal_summary = ["No legal proceedings disclosed."]
        if legal_text.strip():
            legal_summary = summarize_legal(
                legal_text,
                label=f"{ticker} {form_type} {filing_date} legal",
            )["summary_bullets"]

        # Commitments summary
        commit_summary = ["No material commitments or contingencies."]
        if commit_text.strip():
            commit_summary = summarize_commitments(
                commit_text,
                label=f"{ticker} {form_type} {filing_date} commitments",
            )["summary_bullets"]

        # Buybacks summary
        buybacks_summary = ["No share repurchase activity reported."]
        if buybacks_text.strip():
            buybacks_summary = summarize_buybacks(
                buybacks_text,
                label=f"{ticker} {form_type} {filing_date} buybacks",
            )["summary_bullets"]

        # Liquidity summary
        liquidity_summary = ["No liquidity details provided."]
        if liquidity_text.strip():
            liquidity_summary = summarize_liquidity(
                liquidity_text,
                label=f"{ticker} {form_type} {filing_date} liquidity",
            )["summary_bullets"]

        # Subsequent events summary
        subsequent_summary = ["No subsequent events reported."]
        if subsequent_text.strip():
            subsequent_summary = summarize_subsequent(
                subsequent_text,
                label=f"{ticker} {form_type} {filing_date} subsequent",
            )["summary_bullets"]

        # Overall sentiment and comprehensive summary
        overall_sentiment = "neutral"
        comprehensive_summary = []
        comp_result = get_comprehensive_narrative_analysis(
            risk_text,
            mda_text,
            legal_text,
            commit_text,
            buybacks_text,
            liquidity_text,
            subsequent_text,
            label=f"{ticker} {form_type} {filing_date} comprehensive",
        )
        overall_sentiment = comp_result.get("overall_sentiment", "neutral")
        raw_bullets = comp_result.get("comprehensive_summary", [])
        if isinstance(raw_bullets, list):
            comprehensive_summary = [str(b).strip() for b in raw_bullets if isinstance(b, str)]
        else:
            comprehensive_summary = []

        # Prepare update
        update_sql = """
            UPDATE sec_financials SET
                risk_score = ?,
                risk_summary = ?,
                risk_sentiment = ?,
                md_a_summary = ?,
                full_sentiment = ?,
                comprehensive_summary = ?
            WHERE rowid = ?
        """
        # Ensure md_a_summary is a string (list of bullets -> join with space or take first?)
        # According to schema, md_a_summary is TEXT, <=250 characters, short MD&A digest.
        # We'll join the bullets with space and truncate.
        md_a_summary_str = " ".join(mda_summary) if isinstance(mda_summary, list) else str(mda_summary)
        if len(md_a_summary_str) > 250:
            md_a_summary_str = md_a_summary_str[:250]

        # risk_summary already string, truncate to 200
        risk_summary_str = risk_result.get("summary", "")
        if len(risk_summary_str) > 200:
            risk_summary_str = risk_summary_str[:200]

        # risk_sentiment string
        risk_sentiment_str = risk_result.get("sentiment", "neutral")
        # Ensure it's one of expected values
        if risk_sentiment_str not in ("positive", "neutral", "negative"):
            risk_sentiment_str = "neutral"

        # overall_sentiment string
        if overall_sentiment not in ("positive", "neutral", "negative"):
            overall_sentiment = "neutral"

        # comprehensive_summary JSON string
        try:
            comp_json = json.dumps(comprehensive_summary, ensure_ascii=False)
        except Exception:
            comp_json = "[]"

        cursor.execute(
            update_sql,
            (
                risk_result.get("score", 50),
                risk_summary_str,
                risk_sentiment_str,
                md_a_summary_str,
                overall_sentiment,
                comp_json,
                rowid,
            ),
        )
        conn.commit()
        updated += 1
        if updated % 10 == 0:
            info(f"Financial narrative updates processed: {updated}")

    conn.close()
    total_touched = seeded_rows + updated
    success(
        f"Finished seeding {seeded_rows} filings and updating {updated} financial records with LLM-derived metrics"
    )
    banner("Step 1b/5: LLM narrative scoring pass complete")
    return total_touched


if __name__ == "__main__":
    # Allow direct testing
    from .schema import DB_NAME
    step(f"Testing sync_10k_10q_financials on {DB_NAME}")
    updated = sync_10k_10q_financials(DB_NAME, days_back=365)
    success(f"Updated {updated} records")
