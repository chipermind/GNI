"""Convert queue payloads into Telegram-ready plain text.

V1 rules:
  - Preserve the priority emoji (already on payload.title for ALERTA/RADAR;
    inline at the start of FLASH).
  - Preserve the source link as the last line.
  - No raw JSON. No HTML/Markdown directives — Telegram parse_mode = None.
  - Hard cap at 3500 chars (Telegram limit is 4096; leave headroom).

Used by ``run_publish.py``. Pure functions, no I/O.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MAX_TELEGRAM_CHARS = 3500


def _strip_synthetic_url(url: str) -> str:
    """Hide pseudo URLs (source://...) from end-readers."""
    if url and url.startswith("source://"):
        return ""
    return url or ""


def _truncate(text: str, limit: int = MAX_TELEGRAM_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def format_flash(payload: dict) -> str:
    """FLASH: short, single line. payload['text'] is already emoji-led."""
    text = (payload.get("text") or "").strip()
    return _truncate(text)


def format_alerta(payload: dict) -> str:
    """ALERTA: title + summary + key_points + impact + source."""
    title = (payload.get("title") or "").strip()
    summary = (payload.get("summary") or "").strip()
    key_points = payload.get("key_points") or []
    impact = (payload.get("impact") or "").strip()
    source = _strip_synthetic_url(payload.get("source") or "")

    parts: list[str] = []
    if title:
        parts.append(title)
    if summary:
        parts.append("")
        parts.append(summary)
    if isinstance(key_points, list) and key_points:
        parts.append("")
        for kp in key_points:
            if isinstance(kp, str) and kp.strip():
                parts.append(f"• {kp.strip()}")
    if impact:
        parts.append("")
        parts.append(f"📌 Impacto: {impact}")
    if source:
        parts.append("")
        parts.append(source)
    return _truncate("\n".join(parts))


def format_radar(payload: dict) -> str:
    """RADAR: title + signal + context + probability + implication + source."""
    title = (payload.get("title") or "").strip()
    signal = (payload.get("signal") or "").strip()
    context = (payload.get("context") or "").strip()
    probability = (payload.get("probability") or "").strip()
    implication = (payload.get("implication") or "").strip()
    source = _strip_synthetic_url(payload.get("source") or "")

    parts: list[str] = []
    if title:
        parts.append(title)
    if signal:
        parts.append("")
        parts.append(f"🔎 Sinal: {signal}")
    if context:
        parts.append("")
        parts.append(context)
    if probability:
        parts.append("")
        parts.append(f"🎯 Probabilidade: {probability}")
    if implication:
        parts.append("")
        parts.append(f"📌 Leitura GNI: {implication}")
    if source:
        parts.append("")
        parts.append(source)
    return _truncate("\n".join(parts))


def format_payload(template: str, payload: dict) -> str:
    """Dispatch by template. Returns Telegram-ready plain text.

    Returns "" for templates that should never auto-publish from this layer
    (BRIEFING / FECHAMENTO are operator-built downstream).
    """
    t = (template or "").upper().strip()
    if t == "FLASH":
        return format_flash(payload)
    if t == "ALERTA":
        return format_alerta(payload)
    if t == "RADAR":
        return format_radar(payload)
    logger.warning("formatter: unsupported template=%r → empty text", template)
    return ""
