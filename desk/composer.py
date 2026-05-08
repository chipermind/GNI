"""
Desk composer: call Ollama /api/generate and return structured JSON.
Uses standard library only. Additive; not integrated into pipeline.
"""
import json
import os
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from desk.grounded_schema import ALLOWED_SECTION_NAMES, safe_filler_section, validate_grounded_output
from desk.grounded_validators import validate_citation_policy
from desk.templates import load_template
from desk.types import get_limits, parse_desk_type

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_TEMPERATURE = float(os.environ.get("OLLAMA_TEMPERATURE", "0.3"))
OLLAMA_MAX_TOKENS = int(os.environ.get("OLLAMA_MAX_TOKENS", "600"))
OLLAMA_TIMEOUT_SECONDS = int(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "120"))
DESK24H_EVIDENCE_CONFIDENCE_MIN = float(os.environ.get("DESK24H_EVIDENCE_CONFIDENCE_MIN", "0.65"))


def evidence_ok(pack: dict, min_conf: float | None = None) -> bool:
    """
    True if pack has >=1 item with evidence_snippets and confidence >= min_conf.
    Leitura/Insight may only appear when evidence_ok is True.
    """
    if min_conf is None:
        min_conf = DESK24H_EVIDENCE_CONFIDENCE_MIN
    items = pack.get("items") if isinstance(pack, dict) else None
    if not isinstance(items, list):
        return False
    for it in items:
        if not isinstance(it, dict):
            continue
        snippets = it.get("evidence_snippets")
        if not snippets or not isinstance(snippets, list):
            continue
        conf = it.get("confidence")
        if conf is None:
            continue
        if isinstance(conf, (int, float)) and float(conf) >= min_conf:
            return True
    return False


def build_prompt(
    desk_type: str,
    template: str,
    snapshot: dict,
    context: dict,
) -> str:
    """
    Build a strict prompt that forces JSON output.
    Output schema: {"text": "...", "tags": ["..."], "reasons": ["..."], "confidence": 0.0-1.0}

    Example excerpt (PANORAMA_0900, max_lines=60, max_chars=4000):
    ---
    You must output ONLY valid JSON. No markdown, no code fences...
    Output schema: {"text": "<filled report>", "tags": [...], "reasons": [...], "confidence": 0.0-1.0}
    RULES:
    - DO NOT fabricate data...
    - Fill all placeholders in TEMPLATE into "text"...
    - "text" MUST respect: max_lines <= 60, max_chars <= 4000.
    TEMPLATE:
    ---
    📋 PANORAMA | 09:00
    {{HEADLINE}}
    ...
    SNAPSHOT:
    {"markets": {...}, "intel": [...]}
    CONTEXT:
    {"last_posts": [...], "ts": "..."}
    Output ONLY the JSON object.
    ---
    """
    dt = parse_desk_type(desk_type)
    max_lines, max_chars = get_limits(dt)
    snapshot_json = json.dumps(snapshot, ensure_ascii=False, indent=2)

    # Build context without evidence for the JSON dump (avoid duplication)
    ctx_for_json = {k: v for k, v in context.items() if k not in ("evidence_packs", "evidence_pack")}
    context_json = json.dumps(ctx_for_json, ensure_ascii=False, indent=2)

    min_conf = DESK24H_EVIDENCE_CONFIDENCE_MIN
    evidence_rules = ""
    evidence_section = ""
    packs = context.get("evidence_packs") or context.get("evidence_pack")
    if isinstance(packs, dict):
        packs = [packs]
    if isinstance(packs, list) and packs:
        evidence_section = "\nEVIDENCE PACKS (per section - Geopolitics/Cyber/Flows/AI):\n"
        evidence_section += json.dumps(packs, ensure_ascii=False, indent=2)
        evidence_rules = f"""
EVIDENCE RULES (Leitura and Insight):
- For each section, check evidence_ok: pack must have >=1 item with evidence_snippets and confidence >= {min_conf}.
- If evidence_ok is FALSE for that section: Leitura MUST be "Sem sinal confirmado (TBD)" and Insight MUST be "—".
- Do NOT add new facts outside evidence snippets. Use only data from EVIDENCE PACKS and SNAPSHOT.
"""

    return f"""You must output ONLY valid JSON. No markdown, no code fences, no extra text.
Output schema:
{{"text": "<filled report>", "tags": ["tag1", "tag2"], "reasons": ["reason1"], "confidence": 0.0-1.0}}

RULES:
- DO NOT fabricate data. Use only SNAPSHOT and CONTEXT below. If data is missing, omit or write neutral placeholders (e.g. "Sem dados").
- Fill all placeholders in TEMPLATE into "text". Replace {{{{PLACEHOLDER}}}} with content from SNAPSHOT/CONTEXT.
- "text" MUST respect: max_lines <= {max_lines}, max_chars <= {max_chars}.
- ASSUMPTIONS_BLOCK (if present): Must be generic conditional patterns only. Start with "If ..." and use only snapshot numeric trends or generic market relationships. NO new facts, NO new events, NO named sources.{evidence_rules}

TEMPLATE:
---
{template}
---

SNAPSHOT:
{snapshot_json}
{evidence_section}

CONTEXT:
{context_json}

Output ONLY the JSON object."""


def build_grounded_prompt(
    desk_type: str,
    template: str,
    evidence_packs: dict | list,
    limits: dict[str, int],
) -> str:
    """
    Build prompt for strict grounded JSON output.
    Schema: {sections:[...], meta:{used_sources, blocked_claims}}
    """
    packs = evidence_packs if isinstance(evidence_packs, list) else [evidence_packs]
    packs_json = json.dumps(packs, ensure_ascii=False, indent=2)
    max_lines = limits.get("max_lines", 60)
    max_chars = limits.get("max_chars", 4000)
    return f"""Return ONLY JSON. No markdown, no code fences, no extra text.

RULES:
- Use only evidence_snippets for factual claims. Do not invent.
- If citations empty for a section, allowed text is ONLY: "Sem sinal confirmado" / "Monitoring" / "TBD" / "—"
- If data missing, use TBD filler.
- max_lines <= {max_lines}, max_chars total <= {max_chars}

OUTPUT SCHEMA (exact structure):
{{
  "sections": [
    {{
      "name": "Geopolitics",
      "summary": "—",
      "leitura": "Sem sinal confirmado",
      "insight": "—",
      "strategic_implication": "Monitoring",
      "risk_level": "Neutral",
      "time_horizon": "72h",
      "secondary_effects": "TBD",
      "citations": []
    }}
  ],
  "meta": {{"used_sources": 0, "blocked_claims": 0}}
}}

TEMPLATE:
---
{template}
---

EVIDENCE PACKS:
{packs_json}

Output ONLY the JSON object."""


def _sanitize_grounded_sections(sections: list, pack: dict) -> tuple[list, int]:
    """
    Replace citation-violating sections with safe_filler_section; use safe filler when no valid evidence.
    Returns (sanitized_sections, blocked_claims_increment).
    """
    blocked = 0
    if not evidence_ok(pack):
        # No valid evidence: always use safe filler, citations empty
        filled = [safe_filler_section(sec.get("name", "Macro") if isinstance(sec, dict) else "Macro") for sec in sections] if sections else [safe_filler_section(n) for n in ALLOWED_SECTION_NAMES]
        return filled, len(sections)
    out: list = []
    for i, sec in enumerate(sections):
        name = sec.get("name", "Macro") if isinstance(sec, dict) else "Macro"
        if name not in ALLOWED_SECTION_NAMES:
            name = "Macro"
        if not isinstance(sec, dict):
            out.append(safe_filler_section(name))
            blocked += 1
        else:
            ok, _ = validate_citation_policy(sec)
            if not ok:
                out.append(safe_filler_section(name))
                blocked += 1
            else:
                out.append(sec)
    return out, blocked


def compose_grounded(desk_type: str, evidence_packs: dict, context: dict) -> dict[str, Any]:
    """
    Call Ollama with grounded prompt; parse strict JSON.
    On invalid JSON: fallback to safe filler sections + meta blocked_claims.
    Returns {sections, meta, text?, ...} for downstream.
    """
    template = load_template(desk_type)
    limits = get_limits(parse_desk_type(desk_type))
    limits_dict = {"max_lines": limits[0], "max_chars": limits[1]}
    prompt = build_grounded_prompt(desk_type, template, evidence_packs, limits_dict)

    raw = compose(prompt, temperature=0.3, num_predict=1200)
    response_text = raw.get("response") or ""
    parsed = extract_json_object(response_text)

    if not isinstance(parsed, dict):
        return _fallback_grounded(desk_type, raw)

    ok, reason = validate_grounded_output(parsed)
    if not ok:
        return _fallback_grounded(desk_type, raw, blocked_claims=1)

    sections = parsed.get("sections", [])
    meta = dict(parsed.get("meta", {}), **{
        "model": raw.get("model") or OLLAMA_MODEL,
        "ts": datetime.now(timezone.utc).isoformat(),
    })
    pack = evidence_packs if isinstance(evidence_packs, dict) and "items" in evidence_packs else (evidence_packs[0] if isinstance(evidence_packs, list) and evidence_packs else {})
    sections, blocked = _sanitize_grounded_sections(sections, pack)
    meta["blocked_claims"] = meta.get("blocked_claims", 0) + blocked

    return {
        "type": desk_type,
        "sections": sections,
        "meta": meta,
        "text": _sections_to_text(sections),
        "tags": [],
        "reasons": [],
        "confidence": 0.8,
    }


def _fallback_grounded(desk_type: str, raw: dict, blocked_claims: int = 1) -> dict[str, Any]:
    """Fallback when JSON invalid: safe filler sections + meta blocked_claims."""
    sections = [safe_filler_section(n) for n in ALLOWED_SECTION_NAMES]
    ts = datetime.now(timezone.utc).isoformat()
    return {
        "type": desk_type,
        "sections": sections,
        "meta": {
            "used_sources": 0,
            "blocked_claims": blocked_claims,
            "model": raw.get("model") or OLLAMA_MODEL,
            "fallback": True,
            "ts": ts,
        },
        "text": _sections_to_text(sections),
        "tags": [],
        "reasons": ["fallback_invalid_json"],
        "confidence": 0.2,
    }


def _sections_to_text(sections: list) -> str:
    """Render sections to plain text (simple concatenation)."""
    lines = []
    for s in sections:
        if isinstance(s, dict):
            name = s.get("name", "")
            summary = s.get("summary", "—")
            leitura = s.get("leitura", "")
            insight = s.get("insight", "")
            lines.append(f"## {name}\nSummary: {summary}\nLeitura: {leitura}\nInsight: {insight}")
    return "\n\n".join(lines) if lines else "—"


def _strip_unfilled_placeholders(text: str) -> str:
    """Replace any {{...}} with — so validator does not fail on unfilled_placeholders."""
    import re
    return re.sub(r"\{\{[^}]*\}\}", "—", text or "")


def apply_limits(desk_type: str, text: str) -> str:
    """
    Truncate text to respect max_lines and max_chars from desk/types.
    Truncation ends cleanly with "…".
    """
    if not text:
        return text
    dt = parse_desk_type(desk_type)
    max_lines, max_chars = get_limits(dt)
    ellipsis = "…"
    lines = text.splitlines()
    if len(lines) > max_lines:
        text = "\n".join(lines[:max_lines]) + ellipsis
    if len(text) > max_chars:
        text = text[: max_chars - len(ellipsis)] + ellipsis
    return text


def extract_json_object(text: str) -> dict | None:
    """
    Find first "{" and last "}", attempt json.loads on that slice.
    Returns None on failure or if no object found.
    """
    if not text or not text.strip():
        return None
    s = text.strip()
    first = s.find("{")
    last = s.rfind("}")
    if first == -1 or last == -1 or first >= last:
        return None
    try:
        return json.loads(s[first : last + 1])
    except json.JSONDecodeError:
        return None


def minimal_text_from_template(template: str) -> str:
    """
    Remove lines containing {{PLACEHOLDER}}; keep only fixed headings/bullets.
    Does not invent numbers or claims.
    """
    lines = []
    for line in template.splitlines():
        if "{{" in line and "}}" in line:
            continue
        stripped = line.strip()
        if stripped:
            lines.append(line.rstrip())
    return "\n".join(lines) if lines else ""


def _build_fallback_post(
    desk_type: str,
    template: str,
    raw: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    """Build fallback post when JSON is invalid."""
    ts = datetime.now(timezone.utc).isoformat()
    meta: dict[str, Any] = {
        "model": raw.get("model") or OLLAMA_MODEL,
        "temperature": OLLAMA_TEMPERATURE,
        "max_tokens": OLLAMA_MAX_TOKENS,
        "ts": ts,
        "fallback": True,
    }
    if mode == "nopost":
        return {
            "type": desk_type,
            "text": "",
            "tags": [],
            "reasons": ["nopost_invalid_json"],
            "confidence": 0.0,
            "meta": meta,
        }
    return {
        "type": desk_type,
        "text": apply_limits(desk_type, minimal_text_from_template(template)),
        "tags": [],
        "reasons": ["fallback_invalid_json"],
        "confidence": 0.2,
        "meta": meta,
    }


def compose_post(desk_type: str, snapshot: dict, context: dict) -> dict[str, Any]:
    """
    Load template, build prompt, call Ollama, parse JSON response.
    Returns dict with type, text, tags, reasons, confidence, meta.
    On invalid JSON: fallback per COMPOSER_FALLBACK_MODE (minimal | nopost).
    """
    template = load_template(desk_type)
    prompt = build_prompt(desk_type, template, snapshot, context)
    raw = compose(prompt)
    response_text = raw.get("response") or ""
    parsed = extract_json_object(response_text)

    if not isinstance(parsed, dict):
        return _build_fallback_post(desk_type, template, raw, COMPOSER_FALLBACK_MODE)

    text = parsed.get("text")
    text = str(text) if text is not None else ""
    text = _strip_unfilled_placeholders(text)
    text = apply_limits(desk_type, text)
    tags = parsed.get("tags")
    tags = [str(t) for t in tags] if isinstance(tags, list) else []
    reasons = parsed.get("reasons")
    reasons = [str(r) for r in reasons] if isinstance(reasons, list) else []
    confidence = parsed.get("confidence")
    if isinstance(confidence, (int, float)):
        confidence = max(0.0, min(1.0, float(confidence)))
    else:
        confidence = 0.0

    ts = datetime.now(timezone.utc).isoformat()
    return {
        "type": desk_type,
        "text": text,
        "tags": tags,
        "reasons": reasons,
        "confidence": confidence,
        "meta": {
            "model": raw.get("model") or OLLAMA_MODEL,
            "temperature": OLLAMA_TEMPERATURE,
            "max_tokens": OLLAMA_MAX_TOKENS,
            "ts": ts,
        },
    }


def compose(
    prompt: str,
    *,
    temperature: float | None = None,
    num_predict: int | None = None,
) -> dict[str, Any]:
    """
    POST to Ollama /api/generate and return the response as a dict.
    Non-streaming. Raises on HTTP/JSON errors.
    """
    temp = temperature if temperature is not None else OLLAMA_TEMPERATURE
    pred = num_predict if num_predict is not None else OLLAMA_MAX_TOKENS
    url = f"{OLLAMA_BASE_URL}/api/generate"
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temp,
            "num_predict": pred,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            req,
            timeout=OLLAMA_TIMEOUT_SECONDS,
            context=ssl.create_default_context(),
        ) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(f"Ollama HTTP {e.code}: {body or str(e)}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Ollama unreachable: {e.reason}") from e
    except OSError as e:
        raise RuntimeError(f"Ollama request failed: {e}") from e

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Ollama invalid JSON: {e}") from e
