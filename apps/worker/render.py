"""
Render Portuguese templates for GNI Telegram desk.
Templates: GNI_BRIEFING (📍), FLASH_INTEL (⚡), GNI_ALERTA (🚨), RADAR (🧭), FECHAMENTO (🌍).
Message splitting for Telegram/WhatsApp.
"""
import os
from typing import Any, Optional

from apps.shared.env_helpers import get_int_env, parse_int

# WhatsApp-safe: configurable max chars (default 3500)
WHATSAPP_MAX_CHARS = get_int_env("WHATSAPP_MAX_CHARS", default=3500)

# Headers
HEADER_INTEL = "📍 GNI BRIEFING"
HEADER_FLASH_PREFIX = "⚡ FLASH INTEL |"
HEADER_ALERTA = "🚨 GNI ALERTA"
HEADER_RADAR = "🧭 GNI RADAR"
HEADER_FECHAMENTO = "🌍 GNI | FECHAMENTO 24H"

SEPARATOR = "⸻"
BULLET_PREFIX = "\t• "
CHECKLIST_PREFIX = "\t• ✅ "

# Priority seals: P0=🔴 Crítico, P1=🟠 Alto, P2=🟡 Monitoramento, None=🔵 Contexto
_PRIORITY_SEALS: dict[int, str] = {0: "🔴", 1: "🟠", 2: "🟡"}
_PRIORITY_SEAL_DEFAULT = "🔵"


def _seal(priority: Optional[int]) -> str:
    if priority is None:
        return _PRIORITY_SEAL_DEFAULT
    return _PRIORITY_SEALS.get(priority, _PRIORITY_SEAL_DEFAULT)


# Section labels (Template A / BRIEFING)
LABEL_TEMA = "Tema:"
LABEL_LEITURA_RAPIDA = "Leitura GNI"
LABEL_POR_QUE_IMPORTA = "Por que importa"
LABEL_CHECKLIST_OSINT = "Como validar"
LABEL_INSIGHT_CENTRAL = "Veredito GNI"

# Section labels (Template B / FLASH INTEL)
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


def render_intelligence(payload: dict[str, Any], priority: Optional[int] = None) -> str:
    """
    Template ANALISE_INTEL / GNI BRIEFING: structured analysis format.
    Header 📍 with priority seal, Tema, Leitura GNI, Por que importa, Como validar, Veredito GNI.
    """
    parts: list[str] = [f"{HEADER_INTEL} {_seal(priority)}", ""]

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


def render_sector_flash(sector: str, flag: str, payload: dict[str, Any], priority: Optional[int] = None) -> str:
    """
    Template FLASH_SETORIAL / FLASH INTEL: sector flash format.
    Header ⚡ FLASH INTEL | {Setor} {flag} {seal}, linha_1, Em destaque, Insight.
    """
    s = payload.get("setor", sector or "").strip() or sector or "Setor"
    f = payload.get("flag_emoji", flag or "").strip() or flag or ""
    header = f"{HEADER_FLASH_PREFIX} {s} {f} {_seal(priority)}".rstrip()

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


def render_alerta(payload: dict[str, Any], priority: Optional[int] = None) -> str:
    """
    Template GNI_ALERTA: short breaking-news format.
    Header 🚨 GNI ALERTA {seal}, headline, O que aconteceu, Por que importa, Impacto provável.
    """
    parts: list[str] = [f"{HEADER_ALERTA} {_seal(priority)}", ""]
    headline = (payload.get("headline") or "").strip()
    if headline:
        parts += [headline, ""]
    o_que = (payload.get("o_que_aconteceu") or "").strip()
    if o_que:
        parts += [f"O que aconteceu: {o_que}", ""]
    por_que = (payload.get("por_que_importa") or "").strip()
    if por_que:
        parts += [f"Por que importa: {por_que}", ""]
    impacto = (payload.get("impacto_provavel") or "").strip()
    if impacto:
        parts += [f"Impacto provável: {impacto}", ""]
    parts.append(SEPARATOR)
    return "\n".join(parts).strip("\n")


def render_radar(items: list[dict[str, Any]], hour_label: str = "") -> str:
    """
    RADAR bulletin: aggregated list of recent items.
    items: list of {title, source_name, priority}
    """
    label = f"{HEADER_RADAR} — {hour_label}" if hour_label else HEADER_RADAR
    parts: list[str] = [label, ""]
    for i, item in enumerate(items[:5], 1):
        title = (item.get("title") or "")[:80].strip()
        source = (item.get("source_name") or "").strip()
        line = f"{i}. {title}"
        if source:
            line += f" ({source})"
        parts.append(line)
    parts += ["", SEPARATOR]
    return "\n".join(parts).strip("\n")


def render_fechamento(
    items: list[dict[str, Any]],
    signal: str = "",
    watchlist: str = "",
) -> str:
    """
    Fechamento 24H: daily close bulletin.
    items: list of {title, source_name}
    """
    parts: list[str] = [HEADER_FECHAMENTO, "", "O dia em 5 pontos:"]
    for i, item in enumerate(items[:5], 1):
        title = (item.get("title") or "")[:80].strip()
        parts.append(f"{i}. {title}")
    if signal:
        parts += ["", f"O sinal mais importante: {signal}"]
    if watchlist:
        parts.append(f"O que monitorar na madrugada: {watchlist}")
    parts += ["", SEPARATOR]
    return "\n".join(parts).strip("\n")


def render_intelligence_messages(
    payload: dict[str, Any],
    priority: Optional[int] = None,
    max_length: int = WHATSAPP_MAX_CHARS,
) -> list[str]:
    """Template ANALISE_INTEL rendered; split into list if over max_length."""
    text = render_intelligence(payload, priority=priority)
    return _split_message(text, max_length)


def render_sector_flash_messages(
    sector: str,
    flag: str,
    payload: dict[str, Any],
    priority: Optional[int] = None,
    max_length: int = WHATSAPP_MAX_CHARS,
) -> list[str]:
    """Template FLASH_SETORIAL rendered; split into list if over max_length."""
    text = render_sector_flash(sector, flag, payload, priority=priority)
    return _split_message(text, max_length)


def render(
    template: str,
    payload: dict[str, Any],
    sector: Optional[str] = None,
    flag: Optional[str] = None,
    max_length: Optional[int] = None,
    priority: Optional[int] = None,
) -> list[str]:
    """
    Dispatch by template name. Returns list of 1+ messages.
    Templates: GNI_ALERTA, FLASH_SETORIAL, ANALISE_INTEL (default).
    """
    ml = max_length if max_length is not None else WHATSAPP_MAX_CHARS
    if template == "GNI_ALERTA":
        return _split_message(render_alerta(payload, priority=priority), ml)
    if template == "FLASH_SETORIAL":
        s = payload.get("setor", sector or "").strip() or sector or "Setor"
        f = payload.get("flag_emoji", flag or "").strip() or flag or ""
        return render_sector_flash_messages(s, f, payload, priority=priority, max_length=ml)
    return render_intelligence_messages(payload, priority=priority, max_length=ml)


# Backward compat alias
WHATSAPP_SAFE_LENGTH = WHATSAPP_MAX_CHARS
