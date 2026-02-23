"""
Anti-Hallucination Evidence Pack contract for Desk24H.
TypedDict/dataclass definitions and strict validation.
Standard library only.
"""
from typing import TypedDict, Any

# --- Contract types ---


class EvidenceSource(TypedDict, total=False):
    """Source of evidence (name, url, published_at required)."""
    name: str
    url: str
    published_at: str


class EvidenceItem(TypedDict, total=False):
    """Evidence item with title, source, snippets, tags, confidence."""
    title: str
    source: EvidenceSource
    evidence_snippets: list[str]
    tags: list[str]
    confidence: float


class EvidencePack(TypedDict, total=False):
    """Pack of evidence items for a topic."""
    topic: str
    items: list[EvidenceItem]


# --- Validation ---


def validate_source(source: Any) -> tuple[bool, str]:
    """Validate EvidenceSource. Returns (ok, reason)."""
    if not isinstance(source, dict):
        return False, "source must be a dict"
    name = source.get("name")
    if not name or not isinstance(name, str) or not str(name).strip():
        return False, "source.name required and non-empty"
    url = source.get("url")
    if not url or not isinstance(url, str) or not str(url).strip().startswith("http"):
        return False, "source.url required and must start with http"
    pub = source.get("published_at")
    if not pub or not isinstance(pub, str) or not str(pub).strip():
        return False, "source.published_at required and non-empty (ISO string)"
    return True, ""


def validate_item(item: Any) -> tuple[bool, str]:
    """Validate EvidenceItem. Returns (ok, reason)."""
    if not isinstance(item, dict):
        return False, "item must be a dict"
    title = item.get("title")
    if not title or not isinstance(title, str) or not str(title).strip():
        return False, "item.title required and non-empty"
    source = item.get("source")
    ok, reason = validate_source(source)
    if not ok:
        return False, f"item.source invalid: {reason}"
    snippets = item.get("evidence_snippets")
    if not isinstance(snippets, list):
        return False, "item.evidence_snippets must be a list"
    if len(snippets) < 1:
        return False, "item.evidence_snippets must have at least 1 snippet"
    for i, s in enumerate(snippets):
        if not isinstance(s, str):
            return False, f"item.evidence_snippets[{i}] must be string"
        if len(s) > 300:
            return False, f"item.evidence_snippets[{i}] max 300 chars, got {len(s)}"
    return True, ""


def validate_pack(pack: Any) -> tuple[bool, str]:
    """Validate EvidencePack. Returns (ok, reason)."""
    if not isinstance(pack, dict):
        return False, "pack must be a dict"
    topic = pack.get("topic")
    if not topic or not isinstance(topic, str) or not str(topic).strip():
        return False, "pack.topic required and non-empty"
    items = pack.get("items")
    if not isinstance(items, list):
        return False, "pack.items must be a list"
    for i, it in enumerate(items):
        ok, reason = validate_item(it)
        if not ok:
            return False, f"pack.items[{i}] invalid: {reason}"
    return True, ""


def filter_valid_items(items: list[dict]) -> tuple[list[dict], list[str]]:
    """Filter items to valid only. Returns (valid_items, reject_reasons)."""
    valid: list[dict] = []
    reject_reasons: list[str] = []
    for i, item in enumerate(items):
        ok, reason = validate_item(item)
        if ok:
            valid.append(item)
        else:
            reject_reasons.append(f"[{i}] {reason}")
    return valid, reject_reasons
