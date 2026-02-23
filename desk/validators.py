"""
Hard-rule validator for composed desk posts.
"""
import hashlib
import os
import re
from datetime import datetime
from difflib import SequenceMatcher
from desk.types import CONDITIONAL_TYPES, DeskType, get_limits, parse_desk_type

SIMILARITY_THRESHOLD = 0.92
EVIDENCE_CONFIDENCE_MIN = float(os.environ.get("DESK24H_EVIDENCE_CONFIDENCE_MIN", "0.65"))

_PLACEHOLDER_MARKERS = ("monitoring", "tbd", "summary: —", "summary:—")
_LEITURA_PLACEHOLDER = "sem sinal confirmado"


def _evidence_ok(pack: dict, min_conf: float) -> bool:
    """True if pack has >=1 item with evidence_snippets and confidence >= min_conf."""
    items = pack.get("items") if isinstance(pack, dict) else None
    if not isinstance(items, list):
        return False
    for it in items:
        if not isinstance(it, dict):
            continue
        if not (it.get("evidence_snippets") and isinstance(it.get("evidence_snippets"), list)):
            continue
        conf = it.get("confidence")
        if conf is None:
            continue
        if isinstance(conf, (int, float)) and float(conf) >= min_conf:
            return True
    return False


def validate_evidence_policy(post_text: str, packs: dict, min_conf: float | None = None) -> tuple[bool, str]:
    """
    Enforce evidence policy: empty packs => placeholder required; no evidence_ok => Leitura/Insight placeholders.
    Returns (ok, reason). Uses simple substring checks.
    """
    if min_conf is None:
        min_conf = EVIDENCE_CONFIDENCE_MIN
    text = (post_text or "").lower()

    # Normalize packs to dict topic -> pack
    if isinstance(packs, dict) and "items" in packs:
        packs = {"_": packs}
    if not isinstance(packs, dict):
        return True, ""

    for topic, pack in packs.items():
        if not isinstance(pack, dict):
            continue
        items = pack.get("items")
        items_list = items if isinstance(items, list) else []
        section_exists = topic == "_" or (topic and str(topic).lower() in text)

        if not section_exists:
            continue

        # Empty items: require placeholder (Monitoring / TBD / Summary: —)
        if len(items_list) == 0:
            if not any(p in text for p in _PLACEHOLDER_MARKERS):
                return False, "evidence_missing_section_not_placeholder"

        # No evidence_ok: require "Sem sinal confirmado" (Leitura) and "—" (Insight)
        if not _evidence_ok(pack, min_conf):
            if _LEITURA_PLACEHOLDER not in text:
                return False, "evidence_gate_failed_reading_insight"
            if "—" not in text:
                return False, "evidence_gate_failed_reading_insight"

    return True, ""

REQUIRES_READING_PIN = frozenset({
    DeskType.PANORAMA_0900,
    DeskType.THREAT_MONITOR_1130,
    DeskType.RISK_MATRIX_1800,
    DeskType.EXEC_SUMMARY_2030,
    DeskType.OVERNIGHT_WATCH_2300,
})

_SILENCE_PHRASES = ("no action", "nothing to report")


def fingerprint(text: str) -> str:
    """SHA256 of normalized text: lowercased, collapsed whitespace, collapsed repeated punctuation."""
    s = (text or "").lower()
    s = " ".join(s.split())
    s = re.sub(r"([^\w\s])\1+", r"\1", s)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def validate(
    post: dict,
    *,
    prev_texts: list[str] | None = None,
    now_utc: datetime | None = None,
    packs: dict | None = None,
) -> tuple[bool, str]:
    """
    Validate post against size limits from desk/types.
    post: {type: str, text: str, meta?: dict}
    packs: optional evidence packs for evidence policy; if absent, skip evidence check.
    Returns (True, "") on pass; (False, "reason") on fail.
    """
    try:
        desk_type = parse_desk_type(post.get("type") or "")
    except ValueError as e:
        return False, f"invalid_type: {e}"

    text = post.get("text") or ""
    max_lines, max_chars = get_limits(desk_type)
    lines = text.splitlines()
    n_lines = len(lines)
    n_chars = len(text)

    if n_lines > max_lines:
        return False, f"size_exceeded: lines {n_lines} > {max_lines}"
    if n_chars > max_chars:
        return False, f"size_exceeded: chars {n_chars} > {max_chars}"

    if "{{" in text and "}}" in text and re.search(r"\{\{[^}]*\}\}", text):
        return False, "unfilled_placeholders"

    # Dedupe: prev_texts or fetch from storage
    texts_to_check = prev_texts
    if texts_to_check is None:
        try:
            from desk.storage import get_last_posts
            posts = get_last_posts(hours=24)
            texts_to_check = [p.get("text") or "" for p in posts if p.get("text")]
        except Exception:
            texts_to_check = []
    if texts_to_check:
        fp = fingerprint(text)
        for prev in texts_to_check:
            if fingerprint(prev or "") == fp:
                return False, "repeat_exact"
        for prev in texts_to_check:
            if prev and SequenceMatcher(None, text, prev).ratio() >= SIMILARITY_THRESHOLD:
                return False, "repeat_similar"

    if desk_type in CONDITIONAL_TYPES:
        t = text.strip()
        if not t:
            return False, "strategic_silence"
        if "{{SILENCE_REASON}}" in text or "silence" in text.lower():
            return False, "strategic_silence"
        for phrase in _SILENCE_PHRASES:
            if phrase in text.lower():
                return False, "strategic_silence"

    if desk_type in REQUIRES_READING_PIN and "📌" not in text:
        return False, "missing_reading_pin"

    # Assumptions block: if present with content, must have "If" and no http/published_at
    if "assumptions (" in text.lower():
        parts = text.lower().split("assumptions (if x then y):")
        if len(parts) > 1:
            block = parts[1].split("\n\n")[0].split("{{")[0]
            if block.strip():
                if " if " not in block and not block.strip().startswith("if "):
                    return False, "assumptions_policy_violation"
                if "http" in block or "published_at" in block:
                    return False, "assumptions_policy_violation"

    # Grounded sections (citation policy + section limits): optional; skip if no sections (backward compat)
    sections = post.get("sections")
    if isinstance(sections, list) and sections:
        from desk.grounded_schema import get_section_limits
        from desk.grounded_validators import validate_grounded_sections

        max_lines, max_chars = get_limits(desk_type)
        max_lines_sec, max_chars_sec = get_section_limits(max_lines, max_chars, num_sections=len(sections))
        ok, reason = validate_grounded_sections(
            {"sections": sections, "meta": post.get("meta") or {}},
            max_lines_per_section=max_lines_sec,
            max_chars_per_section=max_chars_sec,
        )
        if not ok:
            return False, reason

    # Evidence policy: optional; skip if packs not provided (backward compat)
    p = packs
    if p is None:
        meta = post.get("meta") if isinstance(post.get("meta"), dict) else {}
        p = meta.get("evidence_packs") or meta.get("evidence_pack")
    if p is not None:
        ok, reason = validate_evidence_policy(text, p)
        if not ok:
            return False, reason

    return True, ""


def validate_text(
    desk_type: str,
    text: str,
    prev_texts: list[str] | None = None,
) -> tuple[bool, str]:
    """Validate text for a desk type. Wraps validate() with a minimal post dict."""
    post = {"type": desk_type, "text": text}
    return validate(post, prev_texts=prev_texts)
