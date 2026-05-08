"""
Render exact Portuguese templates: Template A (Análise de Inteligência), Template B (Flash Setorial).
Message splitting for WhatsApp when text exceeds WHATSAPP_MAX_CHARS (default 3500).
"""
import os
from typing import Any, Optional

from apps.shared.env_helpers import get_int_env, parse_int

# WhatsApp-safe: configurable max chars (default 3500)
WHATSAPP_MAX_CHARS = get_int_env("WHATSAPP_MAX_CHARS", default=3500)

HEADER_INTEL = "🚨 GNI — Análise de Inteligência"
HEADER_FLASH_PREFIX = "🚨 GNI |"
SEPARATOR = "⸻"
BULLET_PREFIX = "\t• "
CHECKLIST_PREFIX = "\t• ✅ "

# Section labels (Template A)
LABEL_TEMA = "Tema:"
LABEL_LEITURA_RAPIDA = "Leitura rápida"
LABEL_POR_QUE_IMPORTA = "Por que isso importa"
LABEL_CHECKLIST_OSINT = "Como validar (checklist OSINT)"
LABEL_INSIGHT_CENTRAL = "Insight central"

# Section labels (Template B)
LABEL_EM_DESTAQUE = "Em destaque:"
LABEL_INSIGHT = "📌 Insight:"


def _format_bullets(items: list[str], prefix: str = BULLET_PREFIX) -> str:
    """Format items with prefix (\t• or \t• ✅)."""
    if not items:
        return ""
    return "\n".join(prefix + str(i).strip() for i in items if i is not None and str(i).strip())


def _is_template_a_payload(payload: dict[str, Any]) -> bool:
    """True if payload has Template A (ANALISE_INTEL) fields."""
    return any(
        payload.get(k) for k in ("tema", "leitura_rapida", "por_que_importa", "checklist_osint", "insight_central")
    )


def _is_template_b_payload(payload: dict[str, Any]) -> bool:
    """True if payload has Template B (FLASH_SETORIAL) fields."""
    return any(payload.get(k) for k in ("setor", "linha_1", "em_destaque", "insight"))


def render_intelligence(payload: dict[str, Any]) -> str:
    """
    Template A (ANALISE_INTEL): exact Portuguese layout.
    Header, Tema:, Leitura rápida (\t•), Por que isso importa (\t•), Como validar (checklist) (\t• ✅), Insight central, separator ⸻.
    """
    parts: list[str] = [HEADER_INTEL, ""]

    if _is_template_a_payload(payload):
        # Tema
        tema = payload.get("tema", "").strip() if payload.get("tema") else ""
        if tema:
            parts.append(LABEL_TEMA)
            parts.append(tema)
            parts.append("")

        # Leitura rápida (3 bullets)
        leitura = payload.get("leitura_rapida") or []
        if isinstance(leitura, list) and leitura:
            parts.append(LABEL_LEITURA_RAPIDA)
            parts.append(_format_bullets([str(x).strip() for x in leitura if x]))
            parts.append("")

        # Por que isso importa (2 bullets)
        por_que = payload.get("por_que_importa") or []
        if isinstance(por_que, list) and por_que:
            parts.append(LABEL_POR_QUE_IMPORTA)
            parts.append(_format_bullets([str(x).strip() for x in por_que if x]))
            parts.append("")

        # Como validar (checklist OSINT) (3 items with ✅)
        checklist = payload.get("checklist_osint") or []
        if isinstance(checklist, list) and checklist:
            parts.append(LABEL_CHECKLIST_OSINT)
            parts.append(_format_bullets([str(x).strip() for x in checklist if x], prefix=CHECKLIST_PREFIX))
            parts.append("")

        # Insight central
        insight = payload.get("insight_central", "").strip() if payload.get("insight_central") else ""
        if insight:
            parts.append(LABEL_INSIGHT_CENTRAL)
            parts.append(insight)
            parts.append("")
    else:
        # Legacy: headline/body/bullets
        headline = payload.get("headline", "").strip() if payload.get("headline") else ""
        body = payload.get("body", "").strip() if payload.get("body") else ""
        bullets = payload.get("bullets") or []
        if headline:
            parts.append(LABEL_TEMA)
            parts.append(headline)
            parts.append("")
        if body:
            for line in body.splitlines():
                if line.strip():
                    parts.append(BULLET_PREFIX + line.strip())
            parts.append("")
        if isinstance(bullets, list) and bullets:
            parts.append(_format_bullets([str(b).strip() for b in bullets if b]))
            parts.append("")

    parts.append(SEPARATOR)
    return "\n".join(parts).strip("\n")


def render_sector_flash(sector: str, flag: str, payload: dict[str, Any]) -> str:
    """
    Template B (FLASH_SETORIAL): exact Portuguese layout.
    Header 🚨 GNI | {Setor} {flag}, Em destaque: (\t•), 📌 Insight: ..., separator ⸻.
    """
    # Use payload setor/flag_emoji when available (from generator)
    s = payload.get("setor", sector or "").strip() or sector or "Setor"
    f = payload.get("flag_emoji", flag or "").strip() or flag or ""
    header = f"{HEADER_FLASH_PREFIX} {s} {f}".rstrip()

    parts: list[str] = [header, ""]

    if _is_template_b_payload(payload):
        # linha_1 (first line)
        linha_1 = payload.get("linha_1", "").strip() if payload.get("linha_1") else ""
        if linha_1:
            parts.append(linha_1)
            parts.append("")

        # Em destaque: (3 bullets)
        em_destaque = payload.get("em_destaque") or []
        if isinstance(em_destaque, list) and em_destaque:
            parts.append(LABEL_EM_DESTAQUE)
            parts.append(_format_bullets([str(x).strip() for x in em_destaque if x]))
            parts.append("")

        # 📌 Insight: ...
        insight = payload.get("insight", "").strip() if payload.get("insight") else ""
        if insight:
            parts.append(f"{LABEL_INSIGHT} {insight}")
            parts.append("")
    else:
        # Legacy: headline/body/bullets
        headline = payload.get("headline", "").strip() if payload.get("headline") else ""
        body = payload.get("body", "").strip() if payload.get("body") else ""
        bullets = payload.get("bullets") or []
        if headline:
            parts.append(headline)
            parts.append("")
        if body:
            parts.append(LABEL_EM_DESTAQUE)
            for line in body.splitlines():
                if line.strip():
                    parts.append(BULLET_PREFIX + line.strip())
            parts.append("")
        if isinstance(bullets, list) and bullets:
            parts.append(LABEL_EM_DESTAQUE)
            parts.append(_format_bullets([str(b).strip() for b in bullets if b]))
            parts.append("")

    parts.append(SEPARATOR)
    return "\n".join(parts).strip("\n")


def _split_message(text: str, max_len: int) -> list[str]:
    """
    If text exceeds max_len, split into messages. First part keeps header (first line);
    subsequent parts get the rest. Preserves form: split at newline when possible.
    Recursively splits until all parts <= max_len.
    """
    if len(text) <= max_len:
        return [text]
    first_line_end = text.find("\n")
    if first_line_end == -1:
        first_line_end = len(text)
    header_line = text[: first_line_end + 1]
    rest = text[first_line_end + 1 :].lstrip("\n")

    first_max = max_len - len(header_line)
    if first_max <= 0:
        return [text[:max_len]] + _split_message(text[max_len:], max_len) if len(text) > max_len else [text]

    first_body = rest[:first_max]
    last_nl = first_body.rfind("\n")
    if last_nl > first_max // 2:
        first_body = first_body[: last_nl + 1].rstrip()
        rest = rest[last_nl + 1 :].lstrip("\n")
    else:
        rest = rest[len(first_body):].lstrip("\n")

    part1 = (header_line + first_body).rstrip()
    if not rest:
        return [part1]
    return [part1] + _split_message(rest, max_len)


def render_intelligence_messages(
    payload: dict[str, Any],
    max_length: int = WHATSAPP_MAX_CHARS,
) -> list[str]:
    """Template A rendered; split into list if over max_length (header in first part, form preserved)."""
    text = render_intelligence(payload)
    return _split_message(text, max_length)


def render_sector_flash_messages(
    sector: str,
    flag: str,
    payload: dict[str, Any],
    max_length: int = WHATSAPP_MAX_CHARS,
) -> list[str]:
    """Template B rendered; split into list if over max_length (header in first part, form preserved)."""
    text = render_sector_flash(sector, flag, payload)
    return _split_message(text, max_length)


def render(
    template: str,
    payload: dict[str, Any],
    sector: Optional[str] = None,
    flag: Optional[str] = None,
    max_length: Optional[int] = None,
) -> list[str]:
    """
    Dispatch by template name (ANALISE_INTEL -> Template A, FLASH_SETORIAL -> Template B).
    Returns list of 1 or more messages. Uses WHATSAPP_MAX_CHARS when max_length not given.
    """
    ml = max_length if max_length is not None else WHATSAPP_MAX_CHARS
    if template == "FLASH_SETORIAL":
        s = payload.get("setor", sector or "").strip() or sector or "Setor"
        f = payload.get("flag_emoji", flag or "").strip() or flag or ""
        return render_sector_flash_messages(s, f, payload, max_length=ml)
    return render_intelligence_messages(payload, max_length=ml)


# Backward compat alias
WHATSAPP_SAFE_LENGTH = WHATSAPP_MAX_CHARS
