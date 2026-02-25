"""
LLM-driven strategic intelligence report formatter.
Takes structured radar inputs, calls OpenAI-compatible LLM, returns Telegram-ready string.
Deterministic fallback on failure. 30-min cache. No modification to Telegram client.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore

from apps.worker.cache import cache_get, cache_set
from apps.shared.env_helpers import get_int_env

logger = logging.getLogger(__name__)

RADAR_CACHE_TTL = get_int_env("RADAR_CACHE_TTL", 1800)  # 30 min
RADAR_LLM_TIMEOUT = get_int_env("RADAR_LLM_TIMEOUT", 60)
RADAR_MAX_TOKENS = get_int_env("RADAR_MAX_TOKENS", 2048)
RADAR_TEMPERATURE = 0.3

RADAR_CACHE_PREFIX = "radar:report:"

SYSTEM_PROMPT = """You are a geopolitical strategic intelligence analyst writing concise operational briefs.

Rules:
- Professional, institutional tone (hedge fund intelligence desk)
- No sensationalism
- No political bias
- No speculation without conditional framing ("if X, then Y")
- Expand each radar point with: Strategic Implication, Risk Level (Low/Moderate/Elevated/Critical), Time Horizon (24h/72h/7d), Secondary Effects
- Keep concise but strategic
- Output EXACTLY the format specified — no extra text before or after"""


def _radar_hash(radar_data: dict[str, Any]) -> str:
    """Deterministic hash for cache key."""
    canonical = json.dumps(radar_data, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_user_prompt(radar_data: dict[str, Any]) -> str:
    """Build user prompt from radar inputs."""
    sections = []
    labels = [
        ("geopolitics", "🌍 Geopolitics"),
        ("cyber", "🛡️ Cyber Activity"),
        ("crypto", "💰 Institutional Flows"),
        ("ai", "🤖 AI & Tech Acceleration"),
        ("energy", "⚡ Energy"),
    ]
    for key, label in labels:
        val = radar_data.get(key)
        if val and isinstance(val, str) and val.strip():
            sections.append(f"{label}\n{val.strip()}")
    if not sections:
        sections.append("No specific radar inputs. Provide a brief situational awareness summary.")
    return "\n\n---\n\n".join(sections)


def _output_format_instruction() -> str:
    return """
Output EXACTLY this structure (no markdown, no code fences):

---------------------------------------------------
🌐 GLOBAL NEWS INTEL (GNI)
🧠 Strategic Intelligence Desk

🕒 Time Horizon: 48–72h
📍 Volatility Level: Elevated
🧭 Strategic Posture: Repositioning Phase

━━━━━━━━━━━━━━━━━━━━━━

🔎 ACTIVE RADAR

🌍 Geopolitics
• Summary:
• Strategic Implication:
• Risk Level:
• Time Horizon:
• Secondary Effects:

🛡️ Cyber Activity
• Summary:
• Strategic Implication:
• Risk Level:
• Time Horizon:
• Secondary Effects:

💰 Institutional Flows
• Summary:
• Strategic Implication:
• Risk Level:
• Time Horizon:
• Secondary Effects:

🤖 AI & Tech Acceleration
• Summary:
• Strategic Implication:
• Risk Level:
• Time Horizon:
• Secondary Effects:

━━━━━━━━━━━━━━━━━━━━━━

📊 GNI STRATEGIC READ

Concise 4–6 line synthesis paragraph.

━━━━━━━━━━━━━━━━━━━━━━

📡 Monitoring Status: Active
— GNI Intelligence Unit
---------------------------------------------------

Fill each section with content derived from the radar inputs. Skip sections without input. Risk Level must be one of: Low, Moderate, Elevated, Critical. Time Horizon: 24h, 72h, or 7d."""


def _static_fallback(radar_data: dict[str, Any]) -> str:
    """Deterministic fallback when LLM fails. Telegram-safe. Empty sections get short placeholder."""
    _empty = "Monitoring. No new signal."
    geo = (radar_data.get("geopolitics") or "").strip() or _empty
    cyber = (radar_data.get("cyber") or "").strip() or _empty
    crypto = (radar_data.get("crypto") or "").strip() or _empty
    ai = (radar_data.get("ai") or "").strip() or _empty

    return f"""---------------------------------------------------
🌐 GLOBAL NEWS INTEL (GNI)
🧠 Strategic Intelligence Desk

🕒 Time Horizon: 48–72h
📍 Volatility Level: Elevated
🧭 Strategic Posture: Repositioning Phase

━━━━━━━━━━━━━━━━━━━━━━

🔎 ACTIVE RADAR

🌍 Geopolitics
• Summary: {geo[:200]}{"..." if len(geo) > 200 else ""}
• Strategic Implication: Monitoring.
• Risk Level: Moderate
• Time Horizon: 72h
• Secondary Effects: TBD

🛡️ Cyber Activity
• Summary: {cyber[:200]}{"..." if len(cyber) > 200 else ""}
• Strategic Implication: Monitoring.
• Risk Level: Moderate
• Time Horizon: 72h
• Secondary Effects: TBD

💰 Institutional Flows
• Summary: {crypto[:200]}{"..." if len(crypto) > 200 else ""}
• Strategic Implication: Monitoring.
• Risk Level: Moderate
• Time Horizon: 72h
• Secondary Effects: TBD

🤖 AI & Tech Acceleration
• Summary: {ai[:200]}{"..." if len(ai) > 200 else ""}
• Strategic Implication: Monitoring.
• Risk Level: Moderate
• Time Horizon: 72h
• Secondary Effects: TBD

━━━━━━━━━━━━━━━━━━━━━━

📊 GNI STRATEGIC READ

Environment under observation. Key vectors monitored. No actionable signal at this time. Maintain awareness.

━━━━━━━━━━━━━━━━━━━━━━

📡 Monitoring Status: Active
— GNI Intelligence Unit
---------------------------------------------------"""


def _call_llm(user_prompt: str, base_url: str, model: str, api_key: str | None) -> tuple[str | None, dict[str, Any]]:
    """
    Call OpenAI-compatible chat completions. Returns (content, usage_info).
    usage_info: prompt_tokens, completion_tokens, latency_sec. Never includes API key.
    """
    if not httpx:
        return None, {"error": "httpx not installed"}

    base = base_url.rstrip("/")
    if "/chat/completions" in base:
        url = base
    elif base.endswith("/v1"):
        url = base + "/chat/completions"
    else:
        url = base + "/v1/chat/completions"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT + _output_format_instruction()},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": RADAR_TEMPERATURE,
        "max_tokens": RADAR_MAX_TOKENS,
        "stream": False,
    }

    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=float(RADAR_LLM_TIMEOUT)) as client:
            resp = client.post(url, json=payload, headers=headers)
            latency = time.perf_counter() - t0
            resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        latency = time.perf_counter() - t0
        logger.warning("radar_llm_request_failed reason=%s latency_sec=%.2f", str(e)[:200], latency)
        return None, {"error": str(e)[:200], "latency_sec": round(latency, 2)}

    content = ""
    usage = data.get("usage") or {}
    prompt_tokens = usage.get("prompt_tokens") or 0
    completion_tokens = usage.get("completion_tokens") or 0

    choices = data.get("choices") or []
    if choices and isinstance(choices[0], dict):
        msg = choices[0].get("message") or {}
        content = (msg.get("content") or "").strip()

    usage_info = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "latency_sec": round(time.perf_counter() - t0, 2),
    }
    return content if content else None, usage_info


def generate_report(radar_data: dict[str, Any]) -> str:
    """
    Generate LLM-driven strategic intelligence report from radar inputs.
    Returns single Telegram-ready string. Falls back to static format on failure.
    Cache: 30 min for identical radar input.
    """
    if not radar_data or not isinstance(radar_data, dict):
        return _static_fallback({})

    cache_key = RADAR_CACHE_PREFIX + _radar_hash(radar_data)
    cached = cache_get(cache_key)
    if cached:
        logger.info("radar_report_cache_hit key=%s", cache_key[:24])
        return cached

    ollama_base = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434").rstrip("/")
    base_url = os.environ.get("RADAR_LLM_BASE_URL") or (ollama_base + "/v1")
    model = os.environ.get("RADAR_LLM_MODEL") or os.environ.get("OLLAMA_MODEL", "llama3.2")
    api_key = os.environ.get("RADAR_LLM_API_KEY") or None

    user_prompt = _build_user_prompt(radar_data)
    content, usage_info = _call_llm(user_prompt, base_url, model, api_key)

    if content and len(content) > 50:
        cache_set(cache_key, content, ttl=RADAR_CACHE_TTL)
        logger.info(
            "radar_llm_success prompt_tokens=%s completion_tokens=%s latency_sec=%s",
            usage_info.get("prompt_tokens", 0),
            usage_info.get("completion_tokens", 0),
            usage_info.get("latency_sec", 0),
        )
        return content

    reason = usage_info.get("error", "empty_or_short_response")
    logger.warning("radar_llm_fallback reason=%s fallback=static", reason)
    fallback = _static_fallback(radar_data)
    return fallback
