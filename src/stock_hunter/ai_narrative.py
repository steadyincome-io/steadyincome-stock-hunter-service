import os
import json
import re
import time
from typing import Any, Dict, List

from .logger import info, success, warning, error

def _ensure_env_loaded():
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

try:
    from tenacity import retry, wait_exponential, stop_after_attempt
except Exception:
    def retry(*args, **kwargs):
        def decorator(fn):
            return fn
        return decorator

    def wait_exponential(*args, **kwargs):
        return None

    def stop_after_attempt(*args, **kwargs):
        return None

try:
    from openai import OpenAI
except Exception:
    OpenAI = None
try:
    import requests
except Exception:
    requests = None

# ---- configuration getters -------------------------------------------------
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


class LLMHTTPError(RuntimeError):
    def __init__(self, provider: str, status_code: int, body: str = ""):
        self.provider = provider
        self.status_code = status_code
        self.body = body or ""
        message = f"{provider} HTTP {status_code}"
        if self.body:
            message = f"{message}: {self.body}"
        super().__init__(message)


def _get_provider() -> str:
    _ensure_env_loaded()
    return os.getenv("NARRATIVE_PROVIDER", "openai").strip().lower()


def _get_fallback_provider() -> str:
    _ensure_env_loaded()
    fallback = os.getenv("NARRATIVE_FALLBACK_PROVIDER", "").strip().lower()
    if fallback:
        return fallback
    if _get_provider() == "cohere":
        return "nim"
    return ""


def _get_model(provider: str | None = None) -> str:
    _ensure_env_loaded()
    provider = (provider or _get_provider()).strip().lower()
    fallback_provider = _get_fallback_provider()
    fallback_model = os.getenv("NARRATIVE_FALLBACK_MODEL", "").strip()
    if fallback_model and provider == fallback_provider:
        return fallback_model
    if provider == "cohere":
        return os.getenv("COHERE_MODEL", os.getenv("NARRATIVE_MODEL", "command-a-03-2025"))
    if provider == "nim":
        return os.getenv("NVIDIA_NIM_MODEL", os.getenv("NIM_MODEL", os.getenv("NARRATIVE_MODEL", "meta/llama-3.1-8b-instruct")))
    if provider == "openai":
        return os.getenv("OPENAI_MODEL", os.getenv("NARRATIVE_MODEL", "gpt-4o-mini"))
    return os.getenv("NARRATIVE_MODEL", "gpt-4o-mini")


def _get_cohere_key() -> str:
    _ensure_env_loaded()
    return os.getenv("COHERE_API_KEY", "")


def _get_openai_key() -> str:
    _ensure_env_loaded()
    return os.getenv("OPENAI_API_KEY", "")


def _get_nim_key() -> str:
    _ensure_env_loaded()
    return os.getenv("NVIDIA_NIM_API_KEY", "")


def _get_min_interval_sec() -> float:
    _ensure_env_loaded()
    provider = _get_provider()
    if "NARRATIVE_MAX_RPS" in os.environ:
        rps = max(_env_float("NARRATIVE_MAX_RPS", 1.0), 0.0)
    else:
        provider_defaults = {
            "openai": 1.0,
            "cohere": 0.2,
            "nim": 0.5,
        }
        legacy_calls_per_min = _env_int("NARRATIVE_MAX_CALLS_PER_MIN", 0)
        legacy_min_interval = _env_float("NARRATIVE_MIN_INTERVAL_SEC", 0.0)
        if legacy_calls_per_min > 0:
            rps = legacy_calls_per_min / 60.0
        elif legacy_min_interval > 0:
            rps = 1.0 / legacy_min_interval
        else:
            rps = provider_defaults.get(provider, 1.0)
    return (1.0 / rps) if rps > 0 else 0.0


_last_narrative_request_time = 0.0


def _llm_backend_available() -> bool:
    provider = _get_provider()
    if provider == "cohere":
        return bool(_get_cohere_key())
    if provider == "nim":
        return bool(_get_nim_key())
    return bool(_get_openai_key())

# ---- prompt templates ------------------------------------------------
RISK_PROMPT = """
You are an expert equity analyst. Given the following risk-factor text from a 10-K/10-Q filing,
produce:
1. A risk score from 0 (no risk) to 100 (extremely high risk).
2. A one-sentence (max 200 characters) plain-English summary of the most material risk.
3. An overall sentiment for this section (positive/neutral/negative).

Return ONLY a JSON object with keys "score", "summary", and "sentiment".

Text:
"""

COMBINED_ANALYSIS_PROMPT = """
You are a senior fund analyst analyzing ALL narrative sections from a 10-Q filing:

RISK FACTORS: 
{risk_text}

MANAGEMENT DISCUSSION & ANALYSIS (MD&A):
{mda_text}

LEGAL PROCEEDINGS:
{legal_text}

COMMITMENTS & CONTINGENCIES:
{commitments_text}

SHARE REPURCHASES:
{buybacks_text}

LIQUIDITY & CAPITAL RESOURCES:
{liquidity_text}

SUBSEQUENT EVENTS:
{subsequent_text}

Please analyze and provide:
1. An overall filing sentiment (positive/neutral/negative) - risk factors weighted 60%, MD&A 40%, others 5% each
2. Three concise bullet points (≤150 chars each) covering:
   - Overall investment outlook and strategic direction
   - Key risk management approach and exposure
   - Capital allocation and liquidity strategy
   - Any significant legal or regulatory developments
   - Growth initiatives and performance drivers

Return ONLY a JSON object with keys "sentiment" and "summary_bullets".

Use risk factors first, then balance with other sections for balanced view.
"""

SECTION_SPECIFIC_PROMPTS = {
    'risk_factors': """
You are an equity analyst specializing in risk assessment. Analyze this risk factors text:

{risk_text}

Provide:
1. Risk score (0-100, higher = riskier)
2. One-sentence risk summary (≤200 chars) 
3. Risk sentiment (positive/neutral/negative based on risk framing)

Return ONLY JSON with keys "score", "summary", "sentiment"
""",

    'mda': """
You are a growth strategist. Analyze this MD&A text:

{mda_text}

Provide:
1. Three bullet points (≤150 chars each) covering:
   - Performance highlights and trends
   - Strategic outlook and growth initiatives
   - Key challenges and mitigation strategies

Return ONLY JSON with key "summary_bullets" (array of strings)
""",

    'legal': """
You are a compliance analyst. Analyze legal proceedings:

{legal_text}

Provide:
1. Two bullet points (≤150 chars each) on:
   - Material legal challenges or risks
   - Compliance status and remediation

Return ONLY JSON with key "summary_bullets" (array of strings)
""",

    'commitments': """
You are a financial analyst. Analyze commitments and contingencies:

{commitments_text}

Provide:
1. Two bullet points (≤150 chars each) covering:
   - Significant financial obligations or guarantees
   - Potential impact on cash flows and capital

Return ONLY JSON with key "summary_bullets" (array of strings)
""",

    'buybacks': """
You are a shareholder value analyst. Analyze share repurchase activities:

{buybacks_text}

Provide:
1. One bullet point (≤120 chars) on:
   - Share repurchase strategy and rationale
   - Impact on EPS and shareholder returns

Return ONLY JSON with key "summary_bullets" (array of strings)
""",

    'liquidity': """
You are a credit analyst. Analyze liquidity and capital resources:

{liquidity_text}

Provide:
1. Two bullet points (≤150 chars each) covering:
   - Liquidity position and funding sources
   - Capital allocation strategy and flexibility

Return ONLY JSON with key "summary_bullets" (array of strings)
""",

    'subsequent': """
You are an events analyst. Analyze subsequent events:

{subsequent_text}

Provide:
1. One bullet point (≤120 chars) on:
   - Material post-period developments
   - Near-term implications

Return ONLY JSON with key "summary_bullets" (array of strings)
"""
}

def _truncate(text: str, limit: int) -> str:
    return (text or "").strip()[:limit]

def _split_sentences(text: str, limit: int = 3) -> list:
    sentences = [
        part.strip()
        for part in re.split(r"(?<=[.!?])\s+", text or "")
        if part.strip()
    ]
    return sentences[:limit]

def _message_char_count(messages: List[Dict[str, Any]]) -> int:
    total = 0
    for message in messages or []:
        content = message.get("content", "")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    total += len(part["text"])
                elif isinstance(part, str):
                    total += len(part)
    return total

def _log_llm_request(label: str, messages: List[Dict[str, Any]]) -> None:
    info(f"LLM request [{label}] chars={_message_char_count(messages)}")

def _log_llm_response(label: str, response: str) -> None:
    info(f"LLM response [{label}] chars={len(response or '')}")

def _log_json_state(label: str, state: str, detail: str = "") -> None:
    suffix = f" {detail}" if detail else ""
    info(f"LLM JSON [{label}] state={state}{suffix}")

def _keyword_sentiment(text: str) -> str:
    return sentiment_from_text(text)

def _fallback_risk_summary(text: str) -> dict:
    t = (text or "").lower()
    positive = sum(word in t for word in ["improve", "strong", "growth", "stable", "benefit", "increase"])
    negative = sum(word in t for word in ["risk", "decline", "loss", "weak", "downturn", "litigation", "uncertain"])
    score = 50 + (negative - positive) * 8
    score = max(0, min(100, score))
    sentences = _split_sentences(text, 2)
    summary = sentences[0] if sentences else "No risk factors provided."
    return {
        "score": score,
        "summary": _truncate(summary, 200),
        "sentiment": _keyword_sentiment(text),
    }

def _fallback_bullets(text: str, max_bullets: int, max_chars: int) -> list:
    bullets = []
    for sentence in _split_sentences(text, max_bullets * 2):
        sentence = sentence.replace("\n", " ").strip()
        if sentence:
            bullets.append(_truncate(sentence, max_chars))
        if len(bullets) >= max_bullets:
            break
    return bullets


def _throttle_narrative_requests():
    global _last_narrative_request_time
    min_interval = _get_min_interval_sec()
    if min_interval <= 0:
        return
    elapsed = time.time() - _last_narrative_request_time
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_narrative_request_time = time.time()

def _prompt_from_messages(messages: List[Dict[str, Any]]) -> str:
    parts = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        parts.append(f"{role.upper()}: {content}")
    return "\n\n".join(parts)

def _extract_content(payload: Dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""
    if isinstance(payload.get("text"), str):
        return payload["text"]
    message = payload.get("message")
    if isinstance(message, dict):
        if isinstance(message.get("content"), str):
            return message["content"]
        content = message.get("content")
        if isinstance(content, list) and content:
            part = content[0] or {}
            if isinstance(part.get("text"), str):
                return part["text"]
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] or {}
        message = first.get("message") or {}
        if isinstance(message.get("content"), str):
            return message["content"]
        content = message.get("content")
        if isinstance(content, list) and content:
            part = content[0] or {}
            if isinstance(part.get("text"), str):
                return part["text"]
        if isinstance(first.get("text"), str):
            return first["text"]
    generations = payload.get("generations")
    if isinstance(generations, list) and generations:
        first = generations[0] or {}
        if isinstance(first.get("text"), str):
            return first["text"]
    return ""


def _format_http_error(response, provider: str) -> str:
    body = ""
    try:
        body = (response.text or "").strip()
    except Exception:
        body = ""
    if body and len(body) > 1000:
        body = body[:1000] + "..."
    if body:
        return f"{provider} HTTP {response.status_code}: {body}"
    return f"{provider} HTTP {response.status_code}"


def _raise_http_error(response, provider: str):
    body = ""
    try:
        body = (response.text or "").strip()
    except Exception:
        body = ""
    if body and len(body) > 1000:
        body = body[:1000] + "..."
    raise LLMHTTPError(provider, response.status_code, body)

def _strip_code_fences(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()

def _parse_json_response(text: str):
    cleaned = _strip_code_fences(text)
    if not cleaned:
        raise ValueError("Empty LLM response")

    candidates = [cleaned]

    obj_start = cleaned.find("{")
    obj_end = cleaned.rfind("}")
    if 0 <= obj_start < obj_end:
        candidates.append(cleaned[obj_start : obj_end + 1])

    arr_start = cleaned.find("[")
    arr_end = cleaned.rfind("]")
    if 0 <= arr_start < arr_end:
        candidates.append(cleaned[arr_start : arr_end + 1])

    last_error = None
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception as exc:
            last_error = exc

    raise ValueError(f"Unable to parse JSON from LLM response: {last_error}")

def _parse_json_response_or_raise(text: str, label: str):
    data = _parse_json_response(text)
    _log_json_state(label, "ok")
    return data


def _chat_completion_json(messages: List[Dict[str, Any]], label: str, repair_prompt: str):
    response = _chat_completion(messages, label=label)
    try:
        return _parse_json_response_or_raise(response, label)
    except Exception as exc:
        _log_json_state(label, "failed", str(exc))
        warning(f"LLM JSON parse failed [{label}]; retrying once with stricter JSON-only prompt")
        repair_label = f"{label} repair"
        repair_response = _chat_completion(
            [{"role": "user", "content": repair_prompt}],
            label=repair_label,
        )
        try:
            return _parse_json_response_or_raise(repair_response, repair_label)
        except Exception as repair_exc:
            _log_json_state(repair_label, "failed", str(repair_exc))
            error(f"LLM JSON parse failed [{repair_label}]: {repair_exc}")
            raise

def _call_openai_like(messages: List[Dict[str, Any]], api_key: str, base_url: str) -> str:
    if requests is None:
        raise RuntimeError("requests is unavailable")

    response = requests.post(
        base_url.rstrip("/") + "/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": _get_model(),
            "messages": messages,
            "temperature": _env_float("NARRATIVE_TEMPERATURE", 0.0),
            "max_tokens": _env_int("NARRATIVE_MAX_TOKENS", 800),
        },
        timeout=90,
    )
    try:
        response.raise_for_status()
    except Exception as exc:
        _raise_http_error(response, "OpenAI-compatible")
    data = response.json()
    content = _extract_content(data)
    if not content:
        raise RuntimeError("Empty response from OpenAI-compatible provider")
    return content

def _call_cohere(messages: List[Dict[str, Any]], api_key: str) -> str:
    if requests is None:
        raise RuntimeError("requests is unavailable")

    cohere_base = os.getenv("COHERE_API_BASE", "https://api.cohere.ai")
    if "api.cohere.com" in cohere_base:
        cohere_base = cohere_base.replace("api.cohere.com", "api.cohere.ai")

    response = requests.post(
        cohere_base.rstrip("/") + "/v2/chat",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "accept": "application/json",
        },
        json={
            "model": _get_model(),
            "messages": messages,
            "stream": False,
            "temperature": _env_float("NARRATIVE_TEMPERATURE", 0.0),
            "max_tokens": _env_int("NARRATIVE_MAX_TOKENS", 800),
            "response_format": {"type": "json_object"},
        },
        timeout=90,
    )
    try:
        response.raise_for_status()
    except Exception as exc:
        _raise_http_error(response, "Cohere")
    data = response.json()
    content = _extract_content(data)
    if not content:
        raise RuntimeError("Empty response from Cohere")
    return content

def _route_chat_completion(messages: List[Dict[str, Any]], provider: str | None = None) -> str:
    provider = (provider or _get_provider()).strip().lower()
    if provider == "cohere":
        key = _get_cohere_key()
        if not key:
            raise RuntimeError("COHERE_API_KEY is not set")
        return _call_cohere(messages, key)
    if provider == "nim":
        key = _get_nim_key()
        if not key:
            raise RuntimeError("NVIDIA_NIM_API_KEY is not set")
        nim_base = os.getenv("NVIDIA_NIM_API_BASE", "https://integrate.api.nvidia.com/v1")
        return _call_openai_like(messages, key, nim_base)
    key = _get_openai_key()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    openai_base = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
    return _call_openai_like(messages, key, openai_base)


def _is_rate_limit_error(exc: Exception) -> bool:
    return isinstance(exc, LLMHTTPError) and exc.status_code == 429


def _maybe_fallback_on_rate_limit(messages: List[Dict[str, Any]], label: str, primary_provider: str, exc: Exception) -> str | None:
    if not _is_rate_limit_error(exc):
        return None
    fallback_provider = _get_fallback_provider()
    if not fallback_provider or fallback_provider == primary_provider:
        warning(f"LLM rate limit hit [{label}] and no fallback provider is configured")
        return None
    info(f"LLM rate limit hit [{label}]; falling back from {primary_provider} to {fallback_provider}")
    fallback_key_available = {
        "cohere": bool(_get_cohere_key()),
        "nim": bool(_get_nim_key()),
        "openai": bool(_get_openai_key()),
    }.get(fallback_provider, False)
    if not fallback_key_available:
        warning(f"LLM fallback provider unavailable [{label}] provider={fallback_provider}")
        return None
    return _route_chat_completion(messages, provider=fallback_provider)

@retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(3))
def _chat_completion(messages, label: str = "narrative"):
    provider = _get_provider()
    model = _get_model(provider)
    max_tokens = _env_int("NARRATIVE_MAX_TOKENS", 800)
    info(f"LLM call start [{label}] provider={provider} model={model} max_tokens={max_tokens}")
    _log_llm_request(label, messages)
    _throttle_narrative_requests()
    try:
        response = _route_chat_completion(messages, provider=provider)
    except Exception as exc:
        fallback_response = _maybe_fallback_on_rate_limit(messages, label, provider, exc)
        if fallback_response is not None:
            fallback_provider = _get_fallback_provider()
            fallback_model = _get_model(fallback_provider)
            info(
                f"LLM fallback succeeded [{label}] provider={fallback_provider} "
                f"model={fallback_model}"
            )
            _log_llm_response(label, fallback_response)
            success(f"LLM call done [{label}]")
            return fallback_response
        error(f"LLM call failed [{label}]: {exc}")
        raise
    _log_llm_response(label, response)
    success(f"LLM call done [{label}]")
    return response

def score_risk_factors(text: str, label: str = "risk_factors") -> dict:
    """
    Returns {"score": int, "summary": str, "sentiment": str}
    """
    if not text or not text.strip():
        return {"score": 50, "summary": "No risk factors provided.", "sentiment": "neutral"}
    if not _llm_backend_available():
        raise RuntimeError(f"LLM backend unavailable for [{label}]")

    prompt = RISK_PROMPT + text[:4000]
    repair_prompt = (
        prompt
        + "\n\nSTRICT REPAIR INSTRUCTION: return only a valid JSON object with keys "
        + '"score", "summary", and "sentiment". No prose, no markdown, no code fences.'
    )
    data = _chat_completion_json([{"role": "user", "content": prompt}], label, repair_prompt)
    
    score = max(0, min(100, int(data.get("score", 50))))
    summary = _truncate(data.get("summary", ""), 200)
    sentiment = data.get("sentiment", "neutral")
    
    return {"score": score, "summary": summary, "sentiment": sentiment}

def get_comprehensive_narrative_analysis(risk_text, mda_text, legal_text,
                                         commitments_text, buybacks_text,
                                         liquidity_text, subsequent_text,
                                         label: str = "comprehensive"):
    """
    Performs comprehensive analysis across ALL 7 narrative sections
    """
    if not _llm_backend_available():
        raise RuntimeError(f"LLM backend unavailable for [{label}]")

    comprehensive_prompt = COMBINED_ANALYSIS_PROMPT.format(
        risk_text=risk_text,
        mda_text=mda_text,
        legal_text=legal_text,
        commitments_text=commitments_text,
        buybacks_text=buybacks_text,
        liquidity_text=liquidity_text,
        subsequent_text=subsequent_text
    )
    repair_prompt = (
        comprehensive_prompt
        + "\n\nSTRICT REPAIR INSTRUCTION: return only a valid JSON object with keys "
        + '"sentiment" and "summary_bullets". No prose, no markdown, no code fences.'
    )
    data = _chat_completion_json([{"role": "user", "content": comprehensive_prompt}], label, repair_prompt)

    return {
        'overall_sentiment': data.get('sentiment', 'neutral'),
        'comprehensive_summary': data.get('summary_bullets', []),
        'has_comprehensive_data': True
    }

def summarize_mda(text: str, label: str = "mda") -> list:
    if not text or not text.strip():
        return ["No MD&A provided."]
    if not _llm_backend_available():
        raise RuntimeError(f"LLM backend unavailable for [{label}]")
    
    prompt = """
You are a senior fund analyst. Analyze this MD&A text:

{text}

Provide two bullet points (≤150 chars each) covering:
1. Key performance highlights and trends
2. Strategic outlook and growth initiatives

Return ONLY JSON with this shape:
{{"summary_bullets": ["bullet 1", "bullet 2"]}}
""".format(text=text[:4000])
    repair_prompt = (
        prompt
        + "\n\nSTRICT REPAIR INSTRUCTION: return only a valid JSON object with key "
        + '"summary_bullets" mapped to an array of strings. No prose, no markdown, no code fences.'
    )
    data = _chat_completion_json([{"role": "user", "content": prompt}], label, repair_prompt)

    if isinstance(data, dict):
        data = data.get("summary_bullets", [])

    bullets = [str(b).strip()[:150] for b in data if isinstance(b, str)]
    return bullets[:2]

def sentiment_from_text(text: str) -> str:
    pos = ["growth", "strong", "outperform", "upgrade", "bullish", "profit", "increase", "gain"]
    neg = ["decline", "weak", "downgrade", "loss", "risk", "lawsuit", "decline", "decrease", "fall", "drop"]
    t = text.lower()
    if any(w in t for w in pos):
        return "positive"
    if any(w in t for w in neg):
        return "negative"
    return "neutral"


def summarize_legal(text: str, label: str = "legal") -> dict:
    if not text or not text.strip():
        return {"summary_bullets": ["No legal proceedings disclosed."]}
    if not _llm_backend_available():
        raise RuntimeError(f"LLM backend unavailable for [{label}]")
    prompt = SECTION_SPECIFIC_PROMPTS['legal'].format(legal_text=text[:4000])
    repair_prompt = (
        prompt
        + "\n\nSTRICT REPAIR INSTRUCTION: return only a valid JSON object with key "
        + '"summary_bullets" mapped to an array of strings. No prose, no markdown, no code fences.'
    )
    data = _chat_completion_json([{"role": "user", "content": prompt}], label, repair_prompt)
    bullets = [str(b).strip()[:150] for b in data.get("summary_bullets", [])]
    return {"summary_bullets": bullets[:2]}


def summarize_commitments(text: str, label: str = "commitments") -> dict:
    if not text or not text.strip():
        return {"summary_bullets": ["No material commitments or contingencies."]}
    if not _llm_backend_available():
        raise RuntimeError(f"LLM backend unavailable for [{label}]")
    prompt = SECTION_SPECIFIC_PROMPTS['commitments'].format(commitments_text=text[:4000])
    repair_prompt = (
        prompt
        + "\n\nSTRICT REPAIR INSTRUCTION: return only a valid JSON object with key "
        + '"summary_bullets" mapped to an array of strings. No prose, no markdown, no code fences.'
    )
    data = _chat_completion_json([{"role": "user", "content": prompt}], label, repair_prompt)
    bullets = [str(b).strip()[:150] for b in data.get("summary_bullets", [])]
    return {"summary_bullets": bullets[:2]}


def summarize_buybacks(text: str, label: str = "buybacks") -> dict:
    if not text or not text.strip():
        return {"summary_bullets": ["No share repurchase activity reported."]}
    if not _llm_backend_available():
        raise RuntimeError(f"LLM backend unavailable for [{label}]")
    prompt = SECTION_SPECIFIC_PROMPTS['buybacks'].format(buybacks_text=text[:4000])
    repair_prompt = (
        prompt
        + "\n\nSTRICT REPAIR INSTRUCTION: return only a valid JSON object with key "
        + '"summary_bullets" mapped to an array of strings. No prose, no markdown, no code fences.'
    )
    data = _chat_completion_json([{"role": "user", "content": prompt}], label, repair_prompt)
    bullets = [str(b).strip()[:120] for b in data.get("summary_bullets", [])]
    return {"summary_bullets": bullets[:1]}


def summarize_liquidity(text: str, label: str = "liquidity") -> dict:
    if not text or not text.strip():
        return {"summary_bullets": ["No liquidity details provided."]}
    if not _llm_backend_available():
        raise RuntimeError(f"LLM backend unavailable for [{label}]")
    prompt = SECTION_SPECIFIC_PROMPTS['liquidity'].format(liquidity_text=text[:4000])
    repair_prompt = (
        prompt
        + "\n\nSTRICT REPAIR INSTRUCTION: return only a valid JSON object with key "
        + '"summary_bullets" mapped to an array of strings. No prose, no markdown, no code fences.'
    )
    data = _chat_completion_json([{"role": "user", "content": prompt}], label, repair_prompt)
    bullets = [str(b).strip()[:150] for b in data.get("summary_bullets", [])]
    return {"summary_bullets": bullets[:2]}


def summarize_subsequent(text: str, label: str = "subsequent") -> dict:
    if not text or not text.strip():
        return {"summary_bullets": ["No subsequent events reported."]}
    if not _llm_backend_available():
        raise RuntimeError(f"LLM backend unavailable for [{label}]")
    prompt = SECTION_SPECIFIC_PROMPTS['subsequent'].format(subsequent_text=text[:4000])
    repair_prompt = (
        prompt
        + "\n\nSTRICT REPAIR INSTRUCTION: return only a valid JSON object with key "
        + '"summary_bullets" mapped to an array of strings. No prose, no markdown, no code fences.'
    )
    data = _chat_completion_json([{"role": "user", "content": prompt}], label, repair_prompt)
    bullets = [str(b).strip()[:120] for b in data.get("summary_bullets", [])]
    return {"summary_bullets": bullets[:1]}
