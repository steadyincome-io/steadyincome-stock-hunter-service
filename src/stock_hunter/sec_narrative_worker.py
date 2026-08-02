"""
sec_narrative_worker.py
-----------------------
Fetches the actual 10-Q HTML filing from SEC EDGAR and extracts qualitative
narrative sections using heading-based text extraction.

Sections extracted:
  - Management Discussion & Analysis (MD&A)
  - Risk Factors
  - Legal Proceedings
  - Commitments and Contingencies
  - Share Repurchases
  - Liquidity and Capital Resources
  - Subsequent Events

Each section is stored as a JSON text blob (≤5,000 chars) in sec_financials.
Only applies to 10-Q filings (10-K sections are too long to be useful at this limit).
"""

import re
import json
import time
import requests

try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None

from .logger import info, success, warning, error

SEC_USER_AGENT = "DrawdownAnalyzer Research research@drawdownanalyzer.com"
HEADERS = {"User-Agent": SEC_USER_AGENT}

MAX_SECTION_CHARS = 5000  # Truncate each section to keep DB lean but LLM-usable
MIN_SECTION_BODY_CHARS = 40

_last_request_time = 0.0


def rate_limited_get(url, headers=HEADERS, timeout=15, min_interval=0.15):
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_request_time = time.time()
    return requests.get(url, headers=headers, timeout=timeout)


# ---------------------------------------------------------------------------
# Section heading patterns
# Each entry: (section_key, [list of regex patterns to detect the heading])
# Patterns are tried in order; first match wins.
# ---------------------------------------------------------------------------
SECTION_PATTERNS = [
    ("mda", [
        r"^(?:item\s+\d+[a-z]?\.?\s*)?(?:management.{0,10}s?\s+discussion\s+and\s+analysis(?:\s+of\s+financial\s+condition\s+and\s+results\s+of\s+operations)?|md&a)$",
    ]),
    ("risk_factors", [
        r"^(?:item\s+\d+[a-z]?\.?\s*)?risk\s+factors$",
    ]),
    ("legal", [
        r"^(?:item\s+\d+[a-z]?\.?\s*)?legal\s+proceedings$",
    ]),
    ("commitments", [
        r"^(?:item\s+\d+[a-z]?\.?\s*)?commitments?(?:\s*,?\s*and\s+contingenc(?:y|ies))?$",
    ]),
    ("buybacks", [
        r"^(?:item\s+\d+[a-z]?\.?\s*)?(?:issuer\s+)?purchases?\s+of\s+equity\s+securities$",
        r"^(?:item\s+\d+[a-z]?\.?\s*)?share\s+repurchase$",
        r"^(?:item\s+\d+[a-z]?\.?\s*)?stock\s+repurchase$",
        r"^issuer\s+purchases\s+of\s+equity\s+securities$",
    ]),
    ("liquidity", [
        r"^(?:item\s+\d+[a-z]?\.?\s*)?liquidity\s+and\s+capital\s+resources$",
    ]),
    ("subsequent", [
        r"^(?:item\s+\d+[a-z]?\.?\s*)?subsequent\s+events?$",
    ]),
]


def _compile_patterns():
    """Pre-compile all section heading patterns (case-insensitive)."""
    compiled = []
    for key, patterns in SECTION_PATTERNS:
        compiled.append((key, [re.compile(p, re.IGNORECASE | re.DOTALL) for p in patterns]))
    return compiled


_COMPILED_PATTERNS = _compile_patterns()


def _clean_text(html_element):
    """Strip HTML tags and normalize whitespace from a BeautifulSoup element."""
    text = html_element.get_text(separator=" ")
    # Collapse multiple whitespace/newlines
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _normalize_text(text):
    return re.sub(r"\s+", " ", (text or "").strip())


def _identify_section(heading_text):
    """Return the section key if the heading text matches a known section, else None."""
    for key, patterns in _COMPILED_PATTERNS:
        for pat in patterns:
            if pat.search(heading_text):
                return key
    return None


def fetch_filing_index(report_url):
    """
    Given the primary document URL (e.g., .../000123/0001234-filing.htm),
    derive the filing index URL and fetch a list of all documents in the filing.
    Returns list of (description, document_url) tuples.
    """
    # Derive the index URL from the primary doc URL
    # SEC index is at the same base path with index.json suffix
    parts = report_url.rsplit('/', 1)
    if len(parts) < 2:
        return []
    base_url = parts[0]
    index_url = base_url + "/index.json"
    try:
        resp = rate_limited_get(index_url)
        if resp.status_code != 200:
            return []
        data = resp.json()
        docs = []
        for item in data.get("directory", {}).get("item", []):
            name = item.get("name", "")
            if name.lower().endswith(('.htm', '.html')):
                docs.append({
                    "name": name,
                    "description": item.get("type", ""),
                    "size": int(item.get("size") or 0),
                    "url": base_url + "/" + name,
                })
        return docs
    except Exception:
        return []


def _choose_filing_document(report_url, docs):
    """Pick the best HTML document from a filing index."""
    if not docs:
        return None

    primary_name = report_url.rsplit("/", 1)[-1].lower()

    def score(doc):
        name = (doc.get("name") or "").lower()
        desc = (doc.get("description") or "").lower()
        size = int(doc.get("size") or 0)
        value = 0

        if name == primary_name:
            value += 1000
        if "10-q" in name or "10-k" in name or "10q" in name or "10k" in name:
            value += 150
        if "10-q" in desc or "10-k" in desc:
            value += 50
        if "ex" in name and "10-" not in name:
            value -= 40
        if "index" in name or "toc" in name:
            value -= 30
        value += min(size // 1000, 100)
        return value

    best = max(docs, key=score)
    return best.get("url")


def extract_narratives_from_html(html_content):
    """
    Parse a 10-Q HTML document and extract the 7 qualitative sections.

    Strategy:
      1. Parse with BeautifulSoup, collect ALL visible text nodes in document order.
      2. For each short text chunk (< 150 chars), check if it matches a known section heading.
      3. When a heading is detected, accumulate subsequent text until the next known heading.
      4. Truncate each section to MAX_SECTION_CHARS.

    This handles EDGAR filings that use <div>/<span> exclusively (no <h2>/<h3> tags).
    Duplicate-element prevention: BS4 find_all returns elements including parents and children,
    so we deduplicate by only considering leaf-like elements or elements without children of the same type.
    """
    soup = BeautifulSoup(html_content, "html.parser")

    # Remove script/style noise
    for tag in soup(["script", "style", "meta", "link", "noscript"]):
        tag.decompose()

    block_tags = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"}

    def is_leaf_block(el):
        for child in el.children:
            if hasattr(child, "name") and child.name and child.name.lower() in block_tags:
                return False
        return True

    def is_body_line(text):
        if not text:
            return False
        if len(text) < 3:
            return False
        if re.fullmatch(r"[\d\s\.\-–—,()]+", text):
            return False
        if re.search(r"\bPage\s+\d+\b", text, re.IGNORECASE):
            return False
        if "apple inc. |" in text.lower():
            return False
        alpha_count = len(re.findall(r"[A-Za-z]", text))
        digit_count = len(re.findall(r"\d", text))
        word_count = len(text.split())
        if alpha_count == 0:
            return False
        if digit_count > alpha_count * 1.5 and word_count < 20:
            return False
        return True

    lines = []
    for el in soup.find_all(block_tags):
        if not is_leaf_block(el):
            continue
        text = _normalize_text(el.get_text(separator=" "))
        if is_body_line(text):
            lines.append(text)

    # Now walk through lines and detect sections in document order.
    sections = {}
    current_section = None
    current_parts = []

    def flush():
        nonlocal current_section, current_parts
        if current_section and current_parts:
            raw = " ".join(current_parts).strip()
            # Only store if enough actual body text was found (prevents TOC false positives)
            if len(raw) >= MIN_SECTION_BODY_CHARS:
                if len(raw) > MAX_SECTION_CHARS:
                    raw = raw[:MAX_SECTION_CHARS] + "…"
                existing = sections.get(current_section)
                # Keep the longest body for each section so the real section body
                # replaces table-of-contents hits or partial matches.
                if existing is None or len(raw) > len(existing):
                    sections[current_section] = raw
        current_section = None
        current_parts = []

    for line in lines:
        if len(line) <= 180:
            matched_key = _identify_section(line)
            if matched_key:
                current_body = " ".join(current_parts).strip()
                if current_section is None:
                    current_section = matched_key
                    current_parts = []
                    continue

                if len(current_body) < MIN_SECTION_BODY_CHARS:
                    # Most likely a table-of-contents hit; replace it with the real heading.
                    current_section = matched_key
                    current_parts = []
                    continue

                flush()
                current_section = matched_key
                current_parts = []
                continue

        if current_section:
            total = sum(len(p) for p in current_parts)
            if total < MAX_SECTION_CHARS + 1000:
                current_parts.append(line)

    flush()  # Flush last section
    return sections




def fetch_narratives_for_filing(report_url):
    """
    Given a 10-Q primary document URL, fetch the HTML and extract all narrative sections.
    Returns a dict with keys matching narrative_* column names, values are JSON strings.
    Falls back to empty dict on any error.
    """
    if not report_url:
        return {}
    if BeautifulSoup is None:
        warning("BeautifulSoup is unavailable; narrative extraction skipped")
        return {}

    try:
        resp = rate_limited_get(report_url)
        main_doc_url = report_url
        if resp.status_code != 200:
            docs = fetch_filing_index(report_url)
            main_doc_url = _choose_filing_document(report_url, docs)
            if not main_doc_url:
                return {}
            resp = rate_limited_get(main_doc_url)
            if resp.status_code != 200:
                return {}

        html_content = resp.text
        narratives = extract_narratives_from_html(html_content)
        if not narratives:
            docs = fetch_filing_index(report_url)
            main_doc_url = _choose_filing_document(report_url, docs)
            if main_doc_url and main_doc_url != report_url:
                resp = rate_limited_get(main_doc_url)
                if resp.status_code == 200:
                    narratives = extract_narratives_from_html(resp.text)

        # Convert to column-mapped dict with JSON strings
        result = {}
        key_to_col = {
            "mda":          "narrative_mda",
            "risk_factors": "narrative_risk_factors",
            "legal":        "narrative_legal",
            "commitments":  "narrative_commitments",
            "buybacks":     "narrative_buybacks",
            "liquidity":    "narrative_liquidity",
            "subsequent":   "narrative_subsequent",
        }
        for k, col in key_to_col.items():
            text = narratives.get(k, "")
            if text:
                # Store as a simple JSON object with metadata
                result[col] = json.dumps({
                    "extracted": True,
                    "char_count": len(text),
                    "text": text
                }, ensure_ascii=False)
            else:
                result[col] = None

        found = [k for k, v in result.items() if v]
        if found:
            length_parts = []
            for col in found:
                try:
                    blob = json.loads(result[col] or "{}")
                    text = blob.get("text", "")
                    length_parts.append(f"{col.replace('narrative_', '')}={len(text)}")
                except Exception:
                    continue
            success(
                f"Narrative extraction found {len(found)}/7 sections: "
                f"{', '.join(s.replace('narrative_', '') for s in found)}"
                + (f" | lengths: {', '.join(length_parts)}" if length_parts else "")
            )
        else:
            info("Narrative extraction found no sections")

        return result

    except Exception as e:
        error(f"Narrative parsing failed for {report_url}: {e}")
        return {}


if __name__ == "__main__":
    # Quick test against a known NVDA 10-Q
    import sys
    test_url = (
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581024000063/nvda-20240128.htm"
    )
    if len(sys.argv) > 1:
        test_url = sys.argv[1]

    info(f"Testing narrative extraction for: {test_url}")
    result = fetch_narratives_for_filing(test_url)
    for col, blob in result.items():
        if blob:
            data = json.loads(blob)
            print(f"\n{'='*60}")
            print(f"  {col.upper()} ({data['char_count']} chars)")
            print(f"{'='*60}")
            print(data['text'][:800], "...")
        else:
            print(f"\n  {col.upper()}: [not found]")
