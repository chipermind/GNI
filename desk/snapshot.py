"""
Build Desk 24H snapshot from raw collector/pipeline output.
Deterministic. No LLM, no network, stdlib only.

---
COLLECTOR OUTPUT (used to craft Telegram posts)
---
Raw input structure at assembly/dispatch time:

  From apps/collector/normalize.py normalized_record():
    title, url, published_at, summary, source_name, raw_payload
    raw_payload (RSS): id, link, title, summary, published, updated, author, published_parsed

  From apps/collector/telegram_ingest.py _message_to_record():
    title, summary, url, source_name, source_type, published_at, raw_payload
    raw_payload: chat_id, message_id, date, text

  Stored in Item (apps/api/db/models.py), enriched by scoring: risk, priority, template.
  At dispatch (apps/worker/tasks.py _process_single_item): Item + Draft.data → render() → messages → deliver_message().
  Draft.data = LLM payload (tema, leitura_rapida, por_que_importa, checklist_osint, insight_central for Template A;
  setor, linha_1, em_destaque, insight for Template B).
---
"""
from datetime import datetime, timezone
from typing import Any


# Raw -> normalized key mapping (from REAL pipeline):
#   ts: raw["ts"] | raw["created_at"] | raw["published_at"] | now
#   window_type: desk_type (passed in)
#   markets.*: raw["futures_us"], raw["dxy"], raw["brent"], raw["wti"], raw["btc"], raw["vix"], raw["sentiment_guess"]
#   sentiment_guess: raw-provided only; do NOT compute from prices/news
#   intel[].title: item["title"]
#   intel[].source: item["source_name"] | item["source"]
#   intel[].impact: item["impact"] | item["risk"]
#   intel[].confidence: item["confidence"] | item["priority"] (float if numeric)
#   intel[].category: item["category"] | item["section"] | item["tags"] only if in {geo, cyber, ai, macro}
#   flow.*: raw["etf"], raw["funding"], raw["liquidations"], raw["oi"], raw["notes"]; notes normalized to str (join if list)
#   deltas: compute_deltas(curr, prev) -> markets/flow numeric {prev,curr,delta}; intel new_titles
#
# Example normalized intel item: {"title": "Headline", "source": "RSS", "impact": "high", "confidence": 0.8, "category": "geo"}
ALLOWED_CATEGORIES = frozenset({"geo", "cyber", "ai", "macro"})


def _ts_iso(val: Any) -> str:
    """Convert datetime or ISO string to ISO8601 UTC string."""
    if val is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        return val.isoformat()
    if isinstance(val, str) and val.strip():
        return val.strip()
    return datetime.now(timezone.utc).isoformat()


def _resolve_category(item: dict[str, Any]) -> str | None:
    """Return category only if raw indicates it (category/section/tags) and value in ALLOWED_CATEGORIES."""
    for key in ("category", "section"):
        val = item.get(key)
        if val is not None:
            s = str(val).strip().lower()
            if s in ALLOWED_CATEGORIES:
                return s
    tags = item.get("tags")
    if isinstance(tags, list):
        for t in tags:
            if t is not None:
                s = str(t).strip().lower()
                if s in ALLOWED_CATEGORIES:
                    return s
    if isinstance(tags, str):
        s = tags.strip().lower()
        if s in ALLOWED_CATEGORIES:
            return s
    return None


def _to_intel_item(item: dict[str, Any]) -> dict[str, Any]:
    """Extract intel fields from a single raw item. Only use keys present in raw."""
    title_val = item.get("title")
    title = str(title_val).strip() if title_val is not None and str(title_val).strip() else ""
    source = item.get("source_name") or item.get("source")
    source = str(source) if source is not None else None
    impact = item.get("impact") or item.get("risk")
    impact = str(impact) if impact is not None else None
    conf_raw = item.get("confidence") if "confidence" in item else item.get("priority") if "priority" in item else None
    confidence = float(conf_raw) if conf_raw is not None and isinstance(conf_raw, (int, float)) else None
    cat = _resolve_category(item)
    return {
        "title": title,
        "source": source,
        "impact": impact,
        "confidence": confidence,
        "category": cat,
    }


_MARKETS_KEYS = ("futures_us", "dxy", "brent", "wti", "btc", "vix", "sentiment_guess")


def normalize_markets(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Extract markets dict from raw. Keys: futures_us, dxy, brent, wti, btc, vix, sentiment_guess.
    sentiment_guess: raw-provided only; do NOT compute from prices/news.
    """
    return {k: raw.get(k) if k in raw else None for k in _MARKETS_KEYS}


_FLOW_KEYS = ("etf", "funding", "liquidations", "oi", "notes")


def _notes_to_str(val: Any) -> str | None:
    """Normalize notes: list of strings -> joined with newlines; str -> as-is. Else None."""
    if val is None:
        return None
    if isinstance(val, list):
        parts = [str(x).strip() for x in val if x is not None and str(x).strip()]
        return "\n".join(parts) if parts else None
    if isinstance(val, str) and val.strip():
        return val.strip()
    return None


def normalize_flow(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Extract flow dict from raw. Keys: etf, funding, liquidations, oi, notes.
    notes: normalized to str (list joined with newlines, str as-is). Else None.
    """
    out: dict[str, Any] = {}
    for k in _FLOW_KEYS:
        if k not in raw:
            out[k] = None
        elif k == "notes":
            out[k] = _notes_to_str(raw[k])
        else:
            out[k] = raw[k]
    return out


def _is_numeric(val: Any) -> bool:
    return isinstance(val, (int, float))


def _numeric_deltas(
    curr: dict[str, Any], prev: dict[str, Any] | None, keys: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    """For each key: if both curr and prev have numeric values, return {prev, curr, delta}."""
    out: dict[str, dict[str, Any]] = {}
    if prev is None:
        return out
    for k in keys:
        cv = curr.get(k)
        pv = prev.get(k)
        if _is_numeric(cv) and _is_numeric(pv):
            delta = float(cv) - float(pv)
            out[k] = {"prev": pv, "curr": cv, "delta": delta}
    return out


def compute_deltas(
    curr_snapshot: dict[str, Any],
    prev_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Compute deltas between curr and prev snapshot.
    markets/flow: numeric keys -> {prev, curr, delta} if both numbers.
    intel: new_titles = titles in curr not in prev (by title string).
    Safe when prev is None.
    """
    deltas: dict[str, Any] = {}
    prev = prev_snapshot
    curr = curr_snapshot

    curr_markets = curr.get("markets") or {}
    prev_markets = (prev.get("markets") or {}) if prev else {}
    deltas["markets"] = _numeric_deltas(curr_markets, prev_markets, _MARKETS_KEYS)

    curr_flow = curr.get("flow") or {}
    prev_flow = (prev.get("flow") or {}) if prev else {}
    flow_keys = ("etf", "funding", "liquidations", "oi")  # notes is str, skip
    deltas["flow"] = _numeric_deltas(curr_flow, prev_flow, flow_keys)

    prev_intel = prev.get("intel") or [] if prev else []
    curr_intel = curr.get("intel") or []
    prev_titles = {str(i.get("title") or "").strip() for i in prev_intel if isinstance(i, dict)}
    curr_titles = {str(i.get("title") or "").strip() for i in curr_intel if isinstance(i, dict)}
    new_titles = sorted(curr_titles - prev_titles)
    deltas["intel"] = {"new_titles": new_titles}

    return deltas


def normalize_intel(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Detect intel-like items in raw (items, news_items, alerts, bullets) and normalize.
    Output: list of {title, source, impact, confidence, category}.
    Category only if raw indicates (tags/section/category) and in {geo, cyber, ai, macro}.
    No inference; missing fields -> None.
    """
    out: list[dict[str, Any]] = []
    for key in ("items", "news_items", "alerts", "bullets"):
        items = raw.get(key)
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and it.get("title") is not None:
                    out.append(_to_intel_item(it))
    if not out and isinstance(raw, dict) and raw.get("title") is not None:
        out.append(_to_intel_item(raw))
    return out


def build_snapshot(
    desk_type: str,
    raw: dict[str, Any],
    prev_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build normalized snapshot from raw collector output.
    Only maps keys present in raw; missing -> None or empty.
    """
    # ts: raw["ts"] | raw["created_at"] | raw["published_at"] | now
    ts = _ts_iso(
        raw.get("ts") or raw.get("created_at") or raw.get("published_at")
    )

    # intel: from raw via normalize_intel
    intel = normalize_intel(raw)

    markets = normalize_markets(raw)
    flow = normalize_flow(raw)

    snapshot: dict[str, Any] = {
        "ts": ts,
        "window_type": desk_type,
        "markets": markets,
        "intel": intel,
        "flow": flow,
    }
    snapshot["deltas"] = compute_deltas(snapshot, prev_snapshot)

    return snapshot
