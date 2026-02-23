"""
Citation policy validators for grounded Desk24H output.
Prevent hallucinations by enforcing citations programmatically.
"""
from typing import Any

_SAFE_PHRASES = ("sem sinal confirmado", "monitoring", "tbd", "—", "-")


def _concat_text(section: dict) -> str:
    """Concatenate summary, leitura, insight, secondary_effects for policy check."""
    parts = []
    for k in ("summary", "leitura", "insight", "secondary_effects"):
        v = section.get(k)
        if v is not None and isinstance(v, str):
            parts.append(v.strip())
    return " ".join(parts).lower()


def _strip_safe(text: str) -> str:
    """Remove safe phrases and whitespace; return remaining content."""
    s = text.lower().strip()
    for p in _SAFE_PHRASES:
        s = s.replace(p, " ")
    return " ".join(s.split()).strip()


def validate_citation_policy(
    section: dict,
    *,
    used_sources: set[str] | None = None,
) -> tuple[bool, str]:
    """
    Enforce citation policy on a single section.
    - If citations empty: summary/leitura/insight/secondary_effects must contain ONLY safe phrases.
    - If citations non-empty: each must be valid URL (startswith http).
    - Optional: if used_sources provided, citations should be subset (not enforced if absent).
    Returns (ok, reason).
    """
    if not isinstance(section, dict):
        return False, "section must be a dict"
    citations = section.get("citations")
    if not isinstance(citations, list):
        return False, "citations must be a list"

    if len(citations) == 0:
        concat = _concat_text(section)
        if not concat:
            return True, ""
        remaining = _strip_safe(concat)
        if remaining:
            return False, "missing_citations_with_claims"

    for i, c in enumerate(citations):
        if not isinstance(c, str):
            return False, f"citations[{i}] must be string"
        c = c.strip()
        if not c.startswith("http"):
            return False, "citations must be valid URLs (startswith http)"

    return True, ""


def validate_grounded_sections(
    obj: dict,
    *,
    used_sources: set[str] | None = None,
    max_lines_per_section: int | None = None,
    max_chars_per_section: int | None = None,
) -> tuple[bool, str]:
    """
    Validate citation policy and section size limits for all sections in grounded output.
    obj: {sections: [...], meta?: {used_sources?: int, ...}}
    If max_lines_per_section and max_chars_per_section are provided, each section's rendered
    block must not exceed them (validator checks only; composer applies truncation).
    """
    sections = obj.get("sections")
    if not isinstance(sections, list):
        return True, ""

    from desk.grounded_schema import render_section

    for i, sec in enumerate(sections):
        if not isinstance(sec, dict):
            continue
        ok, reason = validate_citation_policy(sec, used_sources=used_sources)
        if not ok:
            return False, f"sections[{i}]: {reason}"

        if max_lines_per_section is not None and max_chars_per_section is not None:
            rendered = render_section(sec)
            lines = rendered.splitlines()
            if len(lines) > max_lines_per_section:
                return False, f"sections[{i}]: section_lines_exceeded ({len(lines)} > {max_lines_per_section})"
            if len(rendered) > max_chars_per_section:
                return False, f"sections[{i}]: section_chars_exceeded ({len(rendered)} > {max_chars_per_section})"

    return True, ""
