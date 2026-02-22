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

from desk.templates import load_template
from desk.types import get_limits, parse_desk_type

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_TEMPERATURE = float(os.environ.get("OLLAMA_TEMPERATURE", "0.3"))
OLLAMA_MAX_TOKENS = int(os.environ.get("OLLAMA_MAX_TOKENS", "600"))
OLLAMA_TIMEOUT_SECONDS = int(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "30"))


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
    context_json = json.dumps(context, ensure_ascii=False, indent=2)
    return f"""You must output ONLY valid JSON. No markdown, no code fences, no extra text.
Output schema:
{{"text": "<filled report>", "tags": ["tag1", "tag2"], "reasons": ["reason1"], "confidence": 0.0-1.0}}

RULES:
- DO NOT fabricate data. Use only SNAPSHOT and CONTEXT below. If data is missing, omit or write neutral placeholders (e.g. "Sem dados").
- Fill all placeholders in TEMPLATE into "text". Replace {{{{PLACEHOLDER}}}} with content from SNAPSHOT/CONTEXT.
- "text" MUST respect: max_lines <= {max_lines}, max_chars <= {max_chars}.

TEMPLATE:
---
{template}
---

SNAPSHOT:
{snapshot_json}

CONTEXT:
{context_json}

Output ONLY the JSON object."""


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


def compose(prompt: str) -> dict[str, Any]:
    """
    POST to Ollama /api/generate and return the response as a dict.
    Non-streaming. Raises on HTTP/JSON errors.
    """
    url = f"{OLLAMA_BASE_URL}/api/generate"
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": OLLAMA_TEMPERATURE,
            "num_predict": OLLAMA_MAX_TOKENS,
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
