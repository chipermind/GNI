"""
Strict grounded JSON output schema for Desk24H LLM.
Stdlib only. No inference; reject unknown keys.
"""
from typing import Any

# --- Allowed values ---

ALLOWED_SECTION_NAMES = frozenset({"Geopolitics", "Cyber", "Flows", "AI", "Macro"})

RISK_LEVEL = frozenset({"Low", "Moderate", "High", "Neutral"})
TIME_HORIZON = frozenset({"24h", "72h", "7d", "TBD"})
STRATEGIC_IMPLICATION = frozenset({"Monitoring", "Action", "TBD"})

REQUIRED_SECTION_KEYS = frozenset({
    "name",
    "summary",
    "leitura",
    "insight",
    "strategic_implication",
    "risk_level",
    "time_horizon",
    "secondary_effects",
    "citations",
})


def validate_grounded_output(obj: Any) -> tuple[bool, str]:
    """
    Validate LLM output shape. Strict: reject unknown top-level or section keys.
    Returns (ok, reason).
    """
    if not isinstance(obj, dict):
        return False, "output must be a dict"
    allowed_top = frozenset({"sections", "meta"})
    for k in obj:
        if k not in allowed_top:
            return False, f"unknown top-level key: {k}"
    sections = obj.get("sections")
    if not isinstance(sections, list):
        return False, "sections must be a list"
    meta = obj.get("meta")
    if meta is not None and not isinstance(meta, dict):
        return False, "meta must be a dict"
    for i, sec in enumerate(sections):
        if not isinstance(sec, dict):
            return False, f"sections[{i}] must be a dict"
        for k in sec:
            if k not in REQUIRED_SECTION_KEYS:
                return False, f"sections[{i}] unknown key: {k}"
        for req in REQUIRED_SECTION_KEYS:
            if req not in sec:
                return False, f"sections[{i}] missing required key: {req}"
        name = sec.get("name")
        if name not in ALLOWED_SECTION_NAMES:
            return False, f"sections[{i}] invalid name: {name}"
        if sec.get("strategic_implication") not in STRATEGIC_IMPLICATION:
            return False, f"sections[{i}] invalid strategic_implication"
        if sec.get("risk_level") not in RISK_LEVEL:
            return False, f"sections[{i}] invalid risk_level"
        if sec.get("time_horizon") not in TIME_HORIZON:
            return False, f"sections[{i}] invalid time_horizon"
        citations = sec.get("citations")
        if not isinstance(citations, list):
            return False, f"sections[{i}] citations must be a list"
        for j, c in enumerate(citations):
            if not isinstance(c, str):
                return False, f"sections[{i}].citations[{j}] must be string"
    return True, ""


ELLIPSIS = "…"


def render_section(section: dict) -> str:
    """
    Deterministic Telegram-friendly render of a grounded section.
    Format: ## Name, Summary, Leitura, Insight, Risk|Horizon|Implication, Secondary, [citations].
    """
    if not isinstance(section, dict):
        return ""
    name = section.get("name", "")
    summary = str(section.get("summary", "—")).strip()
    leitura = str(section.get("leitura", "")).strip()
    insight = str(section.get("insight", "")).strip()
    risk = str(section.get("risk_level", "Neutral")).strip()
    horizon = str(section.get("time_horizon", "72h")).strip()
    impl = str(section.get("strategic_implication", "Monitoring")).strip()
    secondary = str(section.get("secondary_effects", "TBD")).strip()
    citations = section.get("citations") or []

    lines = [
        f"## {name}",
        f"Summary: {summary}",
        f"Leitura: {leitura}",
        f"Insight: {insight}",
        f"Risk: {risk} | Horizon: {horizon} | Implication: {impl}",
        f"Secondary: {secondary}",
    ]
    for c in citations:
        if isinstance(c, str) and c.strip():
            lines.append(c.strip())
    return "\n".join(lines)


def enforce_section_limits(text: str, max_lines: int, max_chars: int) -> str:
    """
    Safe truncation to respect max_lines and max_chars.
    Truncates with ellipsis; never cuts mid-word for char limit.
    """
    if not text or max_lines <= 0 or max_chars <= 0:
        return text or ""
    ellipsis = ELLIPSIS
    lines = text.splitlines()
    if len(lines) > max_lines:
        return "\n".join(lines[:max_lines]) + ellipsis
    if len(text) > max_chars:
        cutoff = max_chars - len(ellipsis)
        if cutoff <= 0:
            return ellipsis
        return text[:cutoff].rstrip() + ellipsis
    return text


def get_section_limits(max_lines: int, max_chars: int, num_sections: int = 5) -> tuple[int, int]:
    """Derive per-section limits from overall limits. Returns (max_lines_per_section, max_chars_per_section)."""
    n = max(1, num_sections)
    return (max_lines // n, max_chars // n)


def safe_filler_section(name: str) -> dict:
    """
    Return a safe filler section when evidence is missing.
    All values are placeholders; no facts.
    """
    if name not in ALLOWED_SECTION_NAMES:
        name = "Macro"  # fallback
    return {
        "name": name,
        "summary": "—",
        "leitura": "Sem sinal confirmado",
        "insight": "—",
        "strategic_implication": "Monitoring",
        "risk_level": "Neutral",
        "time_horizon": "72h",
        "secondary_effects": "TBD",
        "citations": [],
    }
