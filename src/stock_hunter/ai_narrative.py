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
    # gemini default -- NIM's free-tier meta/llama-3.1-8b-instruct deployment was
    # observed going HTTP 400 "DEGRADED function cannot be invoked" for hours at a
    # time during a real 2026-08-19 pipeline run (confirmed server-side, not a
    # client rate/payload issue), while Gemini's confirmed quota (30 RPM / 14.4K
    # RPD on gemma-4-31b-it) held up cleanly. NIM/Cohere remain available as the
    # fallback provider -- see _get_fallback_provider.
    return os.getenv("NARRATIVE_PROVIDER", "gemini").strip().lower()


def _get_fallback_provider() -> str:
    _ensure_env_loaded()
    fallback = os.getenv("NARRATIVE_FALLBACK_PROVIDER", "").strip().lower()
    if fallback:
        return fallback
    provider = _get_provider()
    if provider in ("gemini", "cohere"):
        return "nim"
    return ""


def _get_model(provider: str | None = None) -> str:
    _ensure_env_loaded()
    provider = (provider or _get_provider()).strip().lower()
    fallback_provider = _get_fallback_provider()
    fallback_model = os.getenv("NARRATIVE_FALLBACK_MODEL", "").strip()
    if fallback_model and provider == fallback_provider:
        return fallback_model
    if provider == "gemini":
        return _get_gemini_model()
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


def _get_gemini_key() -> str:
    _ensure_env_loaded()
    return os.getenv("GEMINI_API_KEY", "")


def _get_gemini_model() -> str:
    _ensure_env_loaded()
    # `or` rather than the getenv(name, default) form -- GitHub Actions substitutes
    # an empty string for a declared-but-unset secret, and that empty value would
    # otherwise win over the default (this exact pattern broke NVIDIA_NIM_MODEL once).
    # gemma-4-31b-it chosen over gemini-2.0-flash because the account's free-tier
    # quota for gemini-2.0-flash was 0/0 (confirmed via HTTP 429 "limit: 0"); the
    # Gemma tier had real quota (30 RPM / 14.4K RPD per the account's rate-limit page).
    return os.getenv("GEMINI_MODEL") or "gemma-4-31b-it"


def _get_gemini_base() -> str:
    _ensure_env_loaded()
    return os.getenv("GEMINI_API_BASE") or "https://generativelanguage.googleapis.com/v1beta"


def _get_min_interval_sec(provider: str | None = None) -> float:
    _ensure_env_loaded()
    provider = (provider or _get_provider()).strip().lower()
    if provider == "gemini":
        # Confirmed via the account's actual rate-limit page: gemma-4-31b-it
        # allows 30 RPM / 14.4K RPD. 0.4 rps (24 RPM) leaves headroom under
        # that ceiling now that gemini is the primary narrative provider
        # (~285 tickers x 6 sections/ticker per full run) as well as the
        # concentration/news-sentiment caller -- both route through this same
        # provider-keyed throttle now, so the limit is shared correctly
        # instead of each call site enforcing its own independent pace and
        # stacking above the real quota.
        rps = max(_env_float("GEMINI_MAX_RPS", 0.4), 0.0)
        return (1.0 / rps) if rps > 0 else 0.0
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


def _llm_backend_available() -> bool:
    provider = _get_provider()
    if provider == "gemini":
        return bool(_get_gemini_key())
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


# Keyed by provider (not a single global clock) so each upstream API's quota
# is tracked independently -- e.g. narrative generation and concentration/
# news-sentiment scoring can both hit Gemini and correctly share one rate
# limit instead of each enforcing its own pace and stacking above the real
# quota (this used to be two separate throttles -- a global one for whatever
# NARRATIVE_PROVIDER was, and a per-provider one only used for the Gemini
# side calls -- which would have double-counted Gemini traffic the moment
# Gemini became both the primary provider and the side-call provider).
_last_request_time_by_provider: Dict[str, float] = {}


def _throttle_provider(provider: str):
    provider = provider.strip().lower()
    min_interval = _get_min_interval_sec(provider)
    if min_interval <= 0:
        return
    last = _last_request_time_by_provider.get(provider, 0.0)
    elapsed = time.time() - last
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_request_time_by_provider[provider] = time.time()

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

def _call_gemini(messages: List[Dict[str, Any]], api_key: str, model: str | None = None, temperature: float | None = None) -> str:
    """generateContent has its own request/response shape (contents/parts,
    not OpenAI-style messages/choices), so it can't go through
    _call_openai_like. Serves both Gemini and Gemma models (GEMINI_MODEL can
    point at either) -- only used for concentration-risk scoring, not the
    primary narrative provider."""
    if requests is None:
        raise RuntimeError("requests is unavailable")

    model = model or _get_gemini_model()
    if temperature is None:
        temperature = _env_float("NARRATIVE_TEMPERATURE", 0.0)
    prompt_text = _prompt_from_messages(messages)

    response = requests.post(
        f"{_get_gemini_base().rstrip('/')}/models/{model}:generateContent",
        params={"key": api_key},
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"role": "user", "parts": [{"text": prompt_text}]}],
            "generationConfig": {
                "temperature": temperature,
                # Deliberately NOT reusing NARRATIVE_MAX_TOKENS (800): thinking-capable
                # models (verified against gemma-4-31b-it) spend a large, non-optional
                # chunk of this budget on an internal reasoning trace before the real
                # answer -- a real concentration-risk prompt used ~1134 thinking tokens
                # plus ~63 answer tokens, so 800 would truncate mid-reasoning and never
                # reach the answer at all.
                "maxOutputTokens": _env_int("GEMINI_MAX_TOKENS", 4000),
                # No responseMimeType: "application/json" here -- that structured-output
                # mode isn't guaranteed to be supported by every model this can point at
                # (e.g. Gemma variants). The prompt's own "return ONLY JSON" instruction
                # plus the existing strip-code-fences + repair-retry parsing (same as
                # every other provider in this file) covers it instead.
            },
        },
        timeout=90,
    )
    try:
        response.raise_for_status()
    except Exception:
        _raise_http_error(response, "Gemini")
    data = response.json()
    candidates = data.get("candidates") or []
    if not candidates:
        block_reason = (data.get("promptFeedback") or {}).get("blockReason")
        detail = f", blockReason={block_reason}" if block_reason else ""
        raise RuntimeError(f"Empty response from Gemini (no candidates{detail})")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    # Thinking-capable models (verified against gemma-4-31b-it) return their
    # internal reasoning as a separate part with "thought": true ahead of the
    # real answer part. That reasoning text often contains its own example
    # braces/JSON snippets, which would corrupt the {..} span our JSON parser
    # looks for if concatenated in -- so only the non-thought parts count as
    # the actual response.
    text = "".join(
        p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought")
    )
    if not text:
        raise RuntimeError("Empty response from Gemini")
    return text


def _route_chat_completion(messages: List[Dict[str, Any]], provider: str | None = None) -> str:
    provider = (provider or _get_provider()).strip().lower()
    if provider == "gemini":
        key = _get_gemini_key()
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        return _call_gemini(messages, key, temperature=_env_float("NARRATIVE_TEMPERATURE", 0.0))
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


def _is_transient_llm_error(exc: Exception) -> bool:
    """True for failures worth falling back to a different provider for,
    rather than just exhausting this call's own retries and giving up.

    Originally 429-only. Broadened after a real run (2026-08-19) spent ~6
    hours hammering a degraded NIM endpoint with zero fallback: 143 plain
    read-timeouts (not HTTP responses at all, so LLMHTTPError never applied)
    and 20 HTTP 400 "DEGRADED function cannot be invoked" (NIM's own
    server-side unhealthy-deployment signal, not a 429) -- neither was
    classified as a rate limit, so the fallback path never triggered despite
    a fallback provider being configured and available the whole time.
    """
    if isinstance(exc, LLMHTTPError):
        if exc.status_code == 429 or exc.status_code >= 500:
            return True
        if exc.status_code == 400 and "DEGRADED" in (exc.body or "").upper():
            return True
        return False
    if requests is not None and isinstance(exc, requests.exceptions.RequestException):
        # Covers ReadTimeout/ConnectTimeout/ConnectionError -- never an
        # LLMHTTPError since the request never got an HTTP response at all.
        return True
    return False


def _maybe_fallback_on_transient_error(messages: List[Dict[str, Any]], label: str, primary_provider: str, exc: Exception) -> str | None:
    if not _is_transient_llm_error(exc):
        return None
    fallback_provider = _get_fallback_provider()
    if not fallback_provider or fallback_provider == primary_provider:
        warning(f"LLM transient error [{label}] and no fallback provider is configured")
        return None
    info(f"LLM transient error [{label}]; falling back from {primary_provider} to {fallback_provider}")
    fallback_key_available = {
        "gemini": bool(_get_gemini_key()),
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
    max_tokens = _env_int("GEMINI_MAX_TOKENS", 4000) if provider == "gemini" else _env_int("NARRATIVE_MAX_TOKENS", 800)
    info(f"LLM call start [{label}] provider={provider} model={model} max_tokens={max_tokens}")
    _log_llm_request(label, messages)
    _throttle_provider(provider)
    try:
        response = _route_chat_completion(messages, provider=provider)
    except Exception as exc:
        fallback_response = _maybe_fallback_on_transient_error(messages, label, provider, exc)
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

CONCENTRATION_PROMPT = """
You are an expert equity analyst assessing structural concentration risk from a 10-K/10-Q filing's
risk-factor and MD&A text -- the kind of risk that isn't about the broader economy, but about this
specific company depending heavily on a small number of products, customers, suppliers, or a single
geographic region for its manufacturing or sales. Examples: a single product representing the majority
of revenue, a handful of customers representing a large share of sales, manufacturing concentrated with
one supplier or in one country/region.

Given the following text, produce:
1. A concentration_score from 0 (highly diversified, no material concentration) to 100 (severe
   concentration in one product/customer/supplier/region).
2. A one-sentence (max 200 characters) plain-English summary of the specific concentration disclosed,
   if any (e.g., "Manufacturing concentrated with contract manufacturers in Taiwan and China").
3. A concentration_type: one of "product", "customer", "supplier", "geographic", "none".

If the text does not disclose any material concentration, return concentration_score 0, an empty
summary, and concentration_type "none". Do not invent concentration that isn't stated in the text.

Return ONLY a JSON object with keys "concentration_score", "summary", and "concentration_type".

Text:
"""


# Keyword-anchored excerpt extraction, not blind truncation: 10-K risk-factor
# sections open with several paragraphs of generic macro/industry boilerplate
# before reaching company-specific concentration disclosures, which are often
# further in. Simply sending the first N characters (as every other narrative
# scorer in this file does) reliably misses that material -- verified against
# a real AAPL 10-K, where a genuine supplier-concentration disclosure was
# present in the stored text but outside a naive first-4000-char window.
_CONCENTRATION_KEYWORDS = [
    "concentrat", "single supplier", "limited number of suppliers", "sole source",
    "single customer", "few customers", "customers represent", "top customer",
    "single product", "one product", "primary product", "majority of our revenue",
    "majority of the company's", "depend on a limited number", "outsourcing partner",
    "contract manufactur", "single geographic", "single region", "single country",
    "reportable segment", "operating segment", "segment revenue", "of our revenue",
    "of total revenue", "of net revenue",
]


def _extract_concentration_context(text, keywords=None, window=400, max_len=4000):
    """Find every keyword hit in the FULL text (not just a truncated prefix)
    and return merged excerpts around each hit, capped at max_len -- gives
    the LLM better-targeted text than a blind prefix truncation when
    concentration-signaling language exists. Returns an empty string when no
    keyword matches anywhere; the caller falls back to a plain prefix in that
    case rather than treating the empty result as a verified negative, since
    this keyword list isn't exhaustive."""
    if not text:
        return ""
    keywords = keywords or _CONCENTRATION_KEYWORDS
    lower = text.lower()
    spans = []
    for kw in keywords:
        start = 0
        while True:
            idx = lower.find(kw, start)
            if idx == -1:
                break
            spans.append((max(0, idx - window), min(len(text), idx + len(kw) + window)))
            start = idx + len(kw)
    if not spans:
        return ""
    spans.sort()
    merged = [spans[0]]
    for s, e in spans[1:]:
        last_s, last_e = merged[-1]
        if s <= last_e:
            merged[-1] = (last_s, max(last_e, e))
        else:
            merged.append((s, e))
    excerpt = "\n...\n".join(text[s:e] for s, e in merged)
    return excerpt[:max_len]


def _parse_concentration_payload(data: dict) -> dict:
    score = max(0, min(100, int(data.get("concentration_score", 0))))
    summary = _truncate(data.get("summary", ""), 200)
    concentration_type = data.get("concentration_type", "none")
    if concentration_type not in ("product", "customer", "supplier", "geographic", "none"):
        concentration_type = "none"
    return {"concentration_score": score, "summary": summary, "concentration_type": concentration_type}


def score_concentration_risk(risk_text: str, mda_text: str = "", business_text: str = "", label: str = "concentration_risk") -> dict:
    """Structural concentration risk (product/customer/supplier/geographic), extracted from the
    risk-factor, MD&A, and Item 1 Business text already fetched for other narrative scoring -- no
    new data source required. Business text is included because segment/product/geographic revenue
    mix is usually narratively disclosed there, not in risk factors or MD&A.

    Routed through _chat_completion_json like every other narrative call -- i.e. whatever
    NARRATIVE_PROVIDER is set to, with the same retry and NARRATIVE_FALLBACK_PROVIDER behavior.
    This used to hardcode Gemini regardless of NARRATIVE_PROVIDER (a small model on NIM was once
    observed defaulting to a generic "none" verdict even when the input text plainly contained
    concentration language), which meant switching providers required remembering this function
    was the exception. Unified deliberately so flipping NARRATIVE_PROVIDER is a single source of
    truth for every LLM call in this file -- if that NIM-quality issue resurfaces, it'll now show
    up here too, which is the intended tradeoff (one lever, not a silent carve-out).

    Returns {"concentration_score": int, "summary": str, "concentration_type": str}.
    """
    combined_text = f"{business_text or ''}\n\n{risk_text or ''}\n\n{mda_text or ''}".strip()
    if not combined_text:
        return {"concentration_score": 0, "summary": "No risk factors or MD&A provided.", "concentration_type": "none"}

    # Prefer the keyword-anchored excerpt when concentration-signaling
    # language is found anywhere in the text (better-targeted than blind
    # truncation, verified against a real AAPL 10-K). But the LLM is ALWAYS
    # called either way -- a keyword miss is not treated as a verified
    # negative, since our keyword list isn't exhaustive and a company can
    # phrase concentration risk in ways it doesn't cover. Falling back to a
    # plain prefix keeps this a genuine LLM judgment call rather than a
    # silent, keyword-gated 0 that looks more authoritative than it is.
    excerpt = _extract_concentration_context(combined_text)
    text_for_prompt = excerpt if excerpt else combined_text[:4000]

    prompt = CONCENTRATION_PROMPT + text_for_prompt
    repair_prompt = (
        prompt
        + "\n\nSTRICT REPAIR INSTRUCTION: return only a valid JSON object with keys "
        + '"concentration_score", "summary", and "concentration_type". No prose, no markdown, no code fences.'
    )
    messages = [{"role": "user", "content": prompt}]

    if not _llm_backend_available():
        raise RuntimeError(f"LLM backend unavailable for [{label}]")
    data = _chat_completion_json(messages, label, repair_prompt)
    return _parse_concentration_payload(data)


NEWS_SENTIMENT_PROMPT = """
You are an equity analyst reviewing a batch of recent news headlines and summaries for a single stock,
called just before a trade decision. Your job is NOT general sentiment about the company's prospects --
it's specifically to catch near-term, event-driven risks that could move the stock but that a
quality/valuation/risk score derived from quarterly filings would never see, since filings are stale by
the time they're read.

Given the following headlines, produce:
1. A sentiment: one of "positive", "neutral", "negative" -- your overall read of these specific headlines
   for this stock over the next few days/weeks, not the company's long-term outlook.
2. A one-sentence (max 200 characters) plain-English summary of what's actually going on right now.
3. flagged_themes: a list of 0-5 objects, each with "theme" (one of "war_geopolitical", "oil_commodity",
   "supply_chain", "tariff_trade", "regulatory_legal", "other_macro") and "evidence" (max 150 characters,
   a direct paraphrase of the specific headline/detail that triggered the flag). Only include a theme if a
   headline genuinely references it for THIS company -- do not flag generic market commentary, and do not
   invent a theme that isn't actually present in the text.

If nothing notable is happening, return sentiment "neutral", a plain summary, and an empty flagged_themes
list. Do not invent news, catalysts, or certainty beyond what's in the text below.

Return ONLY a JSON object with keys "sentiment", "summary", and "flagged_themes".

Headlines:
"""

_NEWS_SENTIMENT_VALID_THEMES = {
    "war_geopolitical", "oil_commodity", "supply_chain", "tariff_trade", "regulatory_legal", "other_macro",
}


def _parse_news_sentiment_payload(data: dict) -> dict:
    sentiment = data.get("sentiment", "neutral")
    if sentiment not in ("positive", "neutral", "negative"):
        sentiment = "neutral"
    summary = _truncate(data.get("summary", ""), 200)
    themes = []
    for item in (data.get("flagged_themes") or [])[:5]:
        if not isinstance(item, dict):
            continue
        theme = item.get("theme")
        if theme not in _NEWS_SENTIMENT_VALID_THEMES:
            continue
        themes.append({"theme": theme, "evidence": _truncate(item.get("evidence", ""), 150)})
    return {"sentiment": sentiment, "summary": summary, "flagged_themes": themes}


def score_news_sentiment(headlines_text: str, label: str = "news_sentiment") -> dict:
    """Recent-news sentiment + event-risk flagging for a single ticker, meant
    to be called on a small final candidate list (premium_screener.py), not
    the whole universe -- headlines are noisy/time-sensitive, and this is one
    extra LLM call per candidate.

    Same NARRATIVE_PROVIDER-routed pattern as score_concentration_risk (see
    that docstring) -- one provider setting governs every LLM call in this
    file, no per-function carve-outs.

    Returns {"sentiment": str, "summary": str, "flagged_themes": [{"theme": str, "evidence": str}, ...]}.
    """
    if not headlines_text or not headlines_text.strip():
        return {"sentiment": "neutral", "summary": "No recent news found.", "flagged_themes": []}

    prompt = NEWS_SENTIMENT_PROMPT + headlines_text[:4000]
    repair_prompt = (
        prompt
        + "\n\nSTRICT REPAIR INSTRUCTION: return only a valid JSON object with keys "
        + '"sentiment", "summary", and "flagged_themes". No prose, no markdown, no code fences.'
    )
    messages = [{"role": "user", "content": prompt}]

    if not _llm_backend_available():
        raise RuntimeError(f"LLM backend unavailable for [{label}]")
    data = _chat_completion_json(messages, label, repair_prompt)
    return _parse_news_sentiment_payload(data)


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
