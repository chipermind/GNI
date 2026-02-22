"""
Hard-rule validator for composed desk posts.
"""
import hashlib
import re
from datetime import datetime
from difflib import SequenceMatcher

from desk.types import CONDITIONAL_TYPES, DeskType, get_limits, parse_desk_type

SIMILARITY_THRESHOLD = 0.92

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
) -> tuple[bool, str]:
    """
    Validate post against size limits from desk/types.
    post: {type: str, text: str, meta?: dict}
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

    if prev_texts:
        fp = fingerprint(text)
        for prev in prev_texts:
            if fingerprint(prev or "") == fp:
                return False, "repeat_exact"
        for prev in prev_texts:
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

    return True, ""


def validate_text(
    desk_type: str,
    text: str,
    prev_texts: list[str] | None = None,
) -> tuple[bool, str]:
    """Validate text for a desk type. Wraps validate() with a minimal post dict."""
    post = {"type": desk_type, "text": text}
    return validate(post, prev_texts=prev_texts)
