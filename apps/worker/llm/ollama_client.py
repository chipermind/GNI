"""
Ollama HTTP client. Two-step: classify -> generate.
Async-first with sync wrappers for compatibility.
Validates JSON with Pydantic; retries once (max 1) with STRICT JSON REPAIR on invalid output.
Request timeout prevents pipeline freeze.

OLLAMA_MODE: native (default) uses /api/chat; openai_compat uses /v1/chat/completions.
If /api/chat returns 404 (some Ollama setups), native mode falls back to /api/generate.
"""
import asyncio
import os
import re
import time
from typing import Optional

import httpx

from apps.worker.cache import (
    get_llm_classify_cached,
    get_llm_generate_cached,
    prompt_hash,
    set_llm_classify_cached,
    set_llm_generate_cached,
)

from .prompts import (
    CLASSIFY_SYSTEM,
    STRICT_JSON_REPAIR,
    classify_prompt,
    generate_prompt,
    get_generate_system,
)
from apps.shared.env_helpers import get_int_env

from .schemas import ClassifyResult, GenerateResult, validate_generate_payload

OLLAMA_REQUEST_TIMEOUT = float(os.environ.get("OLLAMA_REQUEST_TIMEOUT", "120.0"))
MAX_JSON_RETRY = get_int_env("OLLAMA_MAX_JSON_RETRY", 1)
OLLAMA_MODE = (os.environ.get("OLLAMA_MODE", "native") or "native").lower()
OLLAMA_BASE_URL_DEFAULT = "http://ollama:11434"


def _ollama_base_url() -> str:
    return os.environ.get("OLLAMA_BASE_URL", OLLAMA_BASE_URL_DEFAULT)


def _normalize_base_url(url: str) -> str:
    """Strip /v1 suffix; endpoints are built per OLLAMA_MODE."""
    u = url.rstrip("/")
    if u.endswith("/v1"):
        u = u[:-3].rstrip("/")
    return u


def _chat_endpoint(base_url: str) -> str:
    """Return full chat URL per OLLAMA_MODE."""
    base = _normalize_base_url(base_url)
    if OLLAMA_MODE == "openai_compat":
        return f"{base}/v1/chat/completions"
    return f"{base}/api/chat"


def _generate_endpoint(base_url: str) -> str:
    """Legacy /api/generate endpoint (fallback when /api/chat returns 404)."""
    base = _normalize_base_url(base_url)
    return f"{base}/api/generate"


def _extract_json(text: str) -> Optional[str]:
    """Try to extract a single JSON object from model output (strip markdown/code fences)."""
    if not text or not text.strip():
        return None
    text = text.strip()
    if "```" in text:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if match:
            text = match.group(1).strip()
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _extract_content_from_response(data: dict) -> str:
    """Extract content from native (/api/chat) or openai_compat (/v1/chat/completions) response."""
    if OLLAMA_MODE == "openai_compat":
        choices = data.get("choices") or []
        if choices and isinstance(choices[0], dict):
            msg = choices[0].get("message") or {}
            return (msg.get("content") or "").strip()
        return ""
    msg = data.get("message") or {}
    return (msg.get("content") or "").strip()


def _extract_content_from_generate(data: dict) -> str:
    """Extract content from /api/generate response (returns {"response": "..."})."""
    return (data.get("response") or "").strip()


async def _chat_async(
    base_url: str,
    model: str,
    system: str,
    user: str,
    retry_with_repair: bool = False,
    timeout: float = OLLAMA_REQUEST_TIMEOUT,
    operation: str = "chat",
) -> str:
    """POST to chat endpoint; return combined response content. Non-blocking with timeout."""
    t0 = time.perf_counter()
    url = _chat_endpoint(base_url)
    user_content = user + (STRICT_JSON_REPAIR if retry_with_repair else "")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
            # Fallback to /api/generate when /api/chat returns 404 (some Ollama setups)
            if resp.status_code == 404 and OLLAMA_MODE != "openai_compat":
                url_gen = _generate_endpoint(base_url)
                prompt = f"System: {system}\n\nUser: {user_content}"
                gen_payload = {"model": model, "prompt": prompt, "stream": False}
                resp_gen = await client.post(url_gen, json=gen_payload)
                resp_gen.raise_for_status()
                data = resp_gen.json()
                return _extract_content_from_generate(data)
            resp.raise_for_status()
        data = resp.json()
        return _extract_content_from_response(data)
    finally:
        try:
            from apps.observability.metrics import record_llm_latency
            record_llm_latency(operation, time.perf_counter() - t0)
        except ImportError:
            pass


def _validate_and_fill_result(json_str: str, template: str) -> GenerateResult:
    """Parse GenerateResult JSON and validate payload against template schema."""
    result = GenerateResult.model_validate_json(json_str)
    result.payload = validate_generate_payload(result.payload, template)
    return result


async def classify_async(
    title: str,
    summary: str = "",
    source_name: str = "",
    model: str = "llama3.2",
    base_url: Optional[str] = None,
    timeout: float = OLLAMA_REQUEST_TIMEOUT,
) -> ClassifyResult:
    """
    Call Ollama with classify prompt; validate with Pydantic.
    Max retry 1: if invalid JSON, retry once with STRICT JSON REPAIR.
    Caches by prompt hash; repeated items return cached result.
    """
    user = classify_prompt(title, summary, source_name)
    cache_key = prompt_hash(model, CLASSIFY_SYSTEM, user)
    cached = get_llm_classify_cached(cache_key)
    if cached:
        return ClassifyResult.model_validate_json(cached)
    url = base_url or _ollama_base_url()
    raw = await _chat_async(url, model, CLASSIFY_SYSTEM, user, retry_with_repair=False, timeout=timeout, operation="classify")
    json_str = _extract_json(raw)
    if json_str:
        try:
            result = ClassifyResult.model_validate_json(json_str)
            set_llm_classify_cached(cache_key, result.model_dump_json())
            return result
        except Exception:
            pass
    if MAX_JSON_RETRY >= 1:
        raw2 = await _chat_async(url, model, CLASSIFY_SYSTEM, user, retry_with_repair=True, timeout=timeout, operation="classify")
        json_str2 = _extract_json(raw2)
        if json_str2:
            try:
                result = ClassifyResult.model_validate_json(json_str2)
                set_llm_classify_cached(cache_key, result.model_dump_json())
                return result
            except Exception:
                pass
    raise ValueError(f"Invalid classify JSON after retry. Raw: {raw[:500]}...")


async def generate_async(
    title: str,
    summary: str = "",
    template: str = "DEFAULT",
    risk: str = "",
    model: str = "llama3.2",
    base_url: Optional[str] = None,
    timeout: float = OLLAMA_REQUEST_TIMEOUT,
) -> GenerateResult:
    """
    Call Ollama with generate prompt; validate with Pydantic.
    Max retry 1: if invalid JSON, retry once with STRICT JSON REPAIR.
    Caches by prompt hash; repeated items return cached result.
    """
    system = get_generate_system(template)
    user = generate_prompt(title, summary, template, risk)
    cache_key = prompt_hash(model, system, user)
    cached = get_llm_generate_cached(cache_key)
    if cached:
        return _validate_and_fill_result(cached, template)
    url = base_url or _ollama_base_url()
    raw = await _chat_async(url, model, system, user, retry_with_repair=False, timeout=timeout, operation="generate")
    json_str = _extract_json(raw)
    if json_str:
        try:
            result = _validate_and_fill_result(json_str, template)
            set_llm_generate_cached(cache_key, result.model_dump_json())
            return result
        except Exception:
            pass
    if MAX_JSON_RETRY >= 1:
        raw2 = await _chat_async(url, model, system, user, retry_with_repair=True, timeout=timeout, operation="generate")
        json_str2 = _extract_json(raw2)
        if json_str2:
            try:
                result = _validate_and_fill_result(json_str2, template)
                set_llm_generate_cached(cache_key, result.model_dump_json())
                return result
            except Exception:
                pass
    raise ValueError(f"Invalid generate JSON after retry. Raw: {raw[:500]}...")


async def run_classify_then_generate_async(
    title: str,
    summary: str = "",
    source_name: str = "",
    model: str = "llama3.2",
    base_url: Optional[str] = None,
    timeout: float = OLLAMA_REQUEST_TIMEOUT,
) -> tuple[ClassifyResult, GenerateResult]:
    """Classify then generate (async)."""
    c = await classify_async(title, summary, source_name, model=model, base_url=base_url, timeout=timeout)
    g = await generate_async(
        title, summary,
        template=c.template,
        risk=c.risk or "",
        model=model,
        base_url=base_url,
        timeout=timeout,
    )
    return c, g


# --- Sync wrappers for compatibility ---


def classify(
    title: str,
    summary: str = "",
    source_name: str = "",
    model: str = "llama3.2",
    base_url: Optional[str] = None,
) -> ClassifyResult:
    """Sync wrapper: runs classify_async via asyncio.run()."""
    return asyncio.run(classify_async(title, summary, source_name, model=model, base_url=base_url))


def generate(
    title: str,
    summary: str = "",
    template: str = "DEFAULT",
    risk: str = "",
    model: str = "llama3.2",
    base_url: Optional[str] = None,
) -> GenerateResult:
    """Sync wrapper: runs generate_async via asyncio.run()."""
    return asyncio.run(
        generate_async(title, summary, template=template, risk=risk, model=model, base_url=base_url)
    )


def run_classify_then_generate(
    title: str,
    summary: str = "",
    source_name: str = "",
    model: str = "llama3.2",
    base_url: Optional[str] = None,
) -> tuple[ClassifyResult, GenerateResult]:
    """Sync wrapper: runs run_classify_then_generate_async via asyncio.run(). Circuit breaker protected."""
    from apps.worker.circuit_breaker import get_circuit_breaker

    cb = get_circuit_breaker("ollama")
    return cb.call(
        lambda: asyncio.run(
            run_classify_then_generate_async(
                title, summary, source_name, model=model, base_url=base_url
            )
        )
    )
