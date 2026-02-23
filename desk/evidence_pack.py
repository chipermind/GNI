"""
Evidence Pack generator for Desk24H.
Builds EvidencePack from raw collector/pipeline output BEFORE LLM writes sections.
No network, no LLM. Deterministic. Stdlib only.
"""
from datetime import datetime, timezone
from typing import Any

from desk.evidence import validate_pack

# Raw key mapping (best effort from collector/pipeline):
#   items, news_items, alerts, bullets -> candidate lists
#   Per item:
#     title         <- item["title"]
#     url           <- item["url"] | item["link"] | item["raw_payload"]["link"]
#     published_at  <- item["published_at"] | item["raw_payload"]["published"] | item["raw_payload"]["date"]
#     source_name   <- item["source_name"] | item["source"]
#     summary       <- item["summary"] | item["raw_payload"]["summary"] | item["raw_payload"]["text"]
#     tags          <- item["tags"]
#     confidence    <- item["confidence"] | item["priority"]


def _to_iso(pub: Any) -> str | None:
    """Convert published_at to ISO string. None if missing or invalid."""
    if pub is None:
        return None
    if isinstance(pub, datetime):
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        return pub.isoformat()
    if isinstance(pub, str) and pub.strip():
        return pub.strip()
    return None


def _truncate(s: str, max_len: int) -> str:
    """Truncate to max_len chars. No invention."""
    if len(s) <= max_len:
        return s
    return s[:max_len]


def _extract_snippets(item: dict[str, Any]) -> list[str]:
    """Extract up to 2 snippets, each <= 300 chars. From summary/text. Do not invent."""
    out: list[str] = []
    raw = item.get("raw_payload") or {}
    candidates: list[str] = []
    for key in ("summary", "text", "body", "content"):
        val = item.get(key) or raw.get(key)
        if val is not None and isinstance(val, str) and val.strip():
            candidates.append(val.strip())
    if not candidates:
        return []
    full = " ".join(" ".join(s.split()) for s in candidates)[:600]
    if not full:
        return []
    # First 300 chars as snippet 1
    s1 = _truncate(full[:300], 300)
    if s1:
        out.append(s1)
    rest = full[300:].strip()
    if rest and len(out) < 2:
        s2 = _truncate(rest[:300], 300)
        if s2:
            out.append(s2)
    return out


def _candidate_to_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """
    Build EvidenceItem from raw candidate. Returns None if url, published_at,
    or evidence_snippets missing (hard reject).
    """
    # url: item["url"] | item["link"] | raw_payload["link"]
    url = item.get("url") or item.get("link")
    if not url and isinstance(item.get("raw_payload"), dict):
        url = item["raw_payload"].get("link")
    url = str(url).strip() if url is not None else ""
    if not url or not url.startswith("http"):
        return None

    # published_at: item["published_at"] | raw_payload["published"] | raw_payload["date"]
    pub = item.get("published_at")
    if pub is None and isinstance(item.get("raw_payload"), dict):
        pub = item["raw_payload"].get("published") or item["raw_payload"].get("date")
    pub_iso = _to_iso(pub)
    if not pub_iso:
        return None

    # evidence_snippets: from summary/text
    snippets = _extract_snippets(item)
    if not snippets:
        return None

    # title
    title = item.get("title")
    if not title or not isinstance(title, str) or not str(title).strip():
        return None
    title = str(title).strip()

    # source name
    name = item.get("source_name") or item.get("source") or "Unknown"
    if not isinstance(name, str):
        name = str(name) if name is not None else "Unknown"

    # tags
    tags = item.get("tags")
    if isinstance(tags, list):
        tags = [str(t) for t in tags if t is not None and str(t).strip()]
    elif isinstance(tags, str) and tags.strip():
        tags = [tags.strip()]
    else:
        tags = []

    # confidence
    conf = item.get("confidence")
    if conf is None:
        conf = item.get("priority")
    confidence = float(conf) if conf is not None and isinstance(conf, (int, float)) else None

    # Ensure snippets <= 300 each
    snippets = [_truncate(s, 300) for s in snippets[:2] if s]
    if not snippets:
        return None

    return {
        "title": title,
        "source": {"name": name, "url": url, "published_at": pub_iso},
        "evidence_snippets": snippets,
        "tags": tags,
        "confidence": confidence,
    }


def build_evidence_pack(topic: str, raw: dict) -> dict:
    """
    Build EvidencePack from raw collector/pipeline output.
    Only uses raw keys (no network, no LLM). Rejects items missing url,
    published_at, or evidence_snippets. Does not invent data.
    """
    items: list[dict] = []
    if not isinstance(raw, dict) or not topic or not str(topic).strip():
        return {"topic": str(topic or "").strip() or "unknown", "items": []}

    # Extract candidates from known raw keys
    for key in ("items", "news_items", "alerts", "bullets", "news"):
        lst = raw.get(key)
        if not isinstance(lst, list):
            continue
        for it in lst:
            if isinstance(it, dict):
                ev = _candidate_to_item(it)
                if ev is not None:
                    items.append(ev)

    pack: dict[str, Any] = {"topic": str(topic).strip(), "items": items}

    # Final validation
    ok, _ = validate_pack(pack)
    if not ok:
        pack["items"] = []

    return pack
