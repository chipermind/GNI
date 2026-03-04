"""
LLM-driven strategic intelligence report formatter.
Takes structured radar inputs, calls OpenAI-compatible LLM, returns Telegram-ready string.
Supports format_mode: BRIEFING_LONG (default), RADAR_SHORT, FLASH_BREAKING — each loads
the corresponding contract from gni/templates/. Deterministic fallback on failure. 30-min cache.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore

from apps.worker.cache import cache_get, cache_set
from apps.shared.env_helpers import get_int_env

from gni.templates import (
    DEFAULT_FORMAT_MODE,
    FORMAT_MODE_BRIEFING_LONG,
    FORMAT_MODE_FLASH_BREAKING,
    FORMAT_MODE_RADAR_SHORT,
    get_template_path as _get_template_path,
    load_template as _load_gni_template,
)

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


def _radar_hash(radar_data: dict[str, Any], format_mode: str) -> str:
    """Deterministic hash for cache key (includes format_mode so formats don't share cache)."""
    canonical = json.dumps({"radar": radar_data, "format_mode": format_mode}, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_user_prompt(radar_data: dict[str, Any]) -> str:
    """Build user prompt from radar inputs (normalized news/radar)."""
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


def _build_user_prompt_with_day_data(radar_data: dict[str, Any]) -> str:
    """Build full user prompt: dados do dia + notícias/radar normalizados (for template contract)."""
    now = datetime.now(timezone.utc)
    # Locale-agnostic for LLM: weekday name, DD MMM YYYY, HHhMM
    dia = now.strftime("%A")  # Monday, Tuesday...
    date_str = now.strftime("%d %b %Y")  # 04 Mar 2025
    time_str = now.strftime("%Hh%M")  # 14h30
    day_block = (
        "=== Dados do dia ===\n"
        f"Dia da semana: {dia}\n"
        f"Data: {date_str}\n"
        f"Hora (UTC): {time_str}\n"
    )
    radar_block = "=== Notícias / Radar normalizados ===\n" + _build_user_prompt(radar_data)
    return day_block + "\n" + radar_block


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


def _static_fallback_short(radar_data: dict[str, Any]) -> str:
    """Minimal fallback for RADAR_SHORT when LLM fails."""
    _empty = "Sem sinal novo."
    geo = (radar_data.get("geopolitics") or "").strip() or _empty
    return f"""🌐 GLOBAL NEWS INTEL (GNI) — Desk (fallback)

🔎 Radar Ativo
• {geo[:150]}{"..." if len(geo) > 150 else ""}

📌 Leitura GNI
Monitoramento ativo. Nenhum sinal acionável no momento.

— Equipe GNI"""


def _static_fallback_flash(radar_data: dict[str, Any]) -> str:
    """Minimal fallback for FLASH_BREAKING when LLM fails."""
    _empty = "Sem detalhes."
    geo = (radar_data.get("geopolitics") or "").strip() or _empty
    return f"""🚨 GNI — FLASH
• {geo[:120]}{"..." if len(geo) > 120 else ""}
• Contexto em monitoramento.
• Implicação: a avaliar.

📌 Impacto
Impacto operacional em avaliação. Horizonte 24–48h.

"""


def _call_llm(
    user_prompt: str,
    base_url: str,
    model: str,
    api_key: str | None,
    format_instruction: str | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """
    Call OpenAI-compatible chat completions. Returns (content, usage_info).
    format_instruction: template/contract content (anti-drift rules). If None, uses legacy
    _output_format_instruction() for backward compatibility.
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

    system_content = SYSTEM_PROMPT + (format_instruction or _output_format_instruction())
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_content},
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


def generate_report(
    radar_data: dict[str, Any],
    format_mode: str | None = None,
) -> str:
    """
    Generate LLM-driven strategic intelligence report from radar inputs.

    format_mode: One of BRIEFING_LONG, RADAR_SHORT, FLASH_BREAKING. When not provided,
    uses DEFAULT_FORMAT_MODE (BRIEFING_LONG) so existing callers and scheduler stay unchanged.
    Each mode loads the corresponding contract from gni/templates/ (template + anti-drift rules).
    The prompt sent to the LLM is: template + dados do dia + notícias normalizadas.

    Returns single Telegram-ready string. Falls back to static format on failure.
    Cache: 30 min per (radar_data, format_mode).
    """
    if not radar_data or not isinstance(radar_data, dict):
        radar_data = {}
    mode = (format_mode or DEFAULT_FORMAT_MODE).strip().upper()
    if mode not in (FORMAT_MODE_BRIEFING_LONG, FORMAT_MODE_RADAR_SHORT, FORMAT_MODE_FLASH_BREAKING):
        mode = DEFAULT_FORMAT_MODE

    cache_key = RADAR_CACHE_PREFIX + _radar_hash(radar_data, mode)
    cached = cache_get(cache_key)
    if cached:
        logger.info("radar_report_cache_hit key=%s", cache_key[:24])
        return cached

    # Load contract template (includes anti-drift rules)
    try:
        template_content = _load_gni_template(mode)
        template_path = _get_template_path(mode)
    except (ValueError, FileNotFoundError) as e:
        logger.warning("gni_template_load_failed format_mode=%s error=%s", mode, e)
        if mode == FORMAT_MODE_BRIEFING_LONG:
            template_content = None  # use legacy _output_format_instruction()
            template_path = None
        else:
            template_path = None
            fallback = (
                _static_fallback_short(radar_data)
                if mode == FORMAT_MODE_RADAR_SHORT
                else _static_fallback_flash(radar_data)
            )
            return fallback

    user_prompt = _build_user_prompt_with_day_data(radar_data)
    format_instruction = template_content if template_content else None
    ollama_base = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434").rstrip("/")
    base_url = os.environ.get("RADAR_LLM_BASE_URL") or (ollama_base + "/v1")
    model = os.environ.get("RADAR_LLM_MODEL") or os.environ.get("OLLAMA_MODEL", "llama3.2")
    api_key = os.environ.get("RADAR_LLM_API_KEY") or None

    content, usage_info = _call_llm(
        user_prompt, base_url, model, api_key, format_instruction=format_instruction
    )

    # DEBUG: format_mode, template_path, size of final prompt (system + user) and response
    prompt_len = len(SYSTEM_PROMPT) + len(format_instruction or _output_format_instruction()) + len(user_prompt)
    logger.debug(
        "format_mode=%s template_path=%s prompt_len=%s response_len=%s",
        mode,
        str(template_path) if template_path else "legacy",
        prompt_len,
        len(content) if content else 0,
    )

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
    logger.warning("radar_llm_fallback reason=%s fallback=static format_mode=%s", reason, mode)
    if mode == FORMAT_MODE_RADAR_SHORT:
        return _static_fallback_short(radar_data)
    if mode == FORMAT_MODE_FLASH_BREAKING:
        return _static_fallback_flash(radar_data)
    return _static_fallback(radar_data)
