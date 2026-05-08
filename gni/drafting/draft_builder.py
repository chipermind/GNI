"""Build structured draft payloads from normalized headlines.

V1 templates auto-built from a single headline: FLASH, ALERTA, RADAR.
BRIEFING and FECHAMENTO are not auto-built; their drafts are stamped
``draft_status = "needs_editorial_build"`` for operator pickup.

A pre-router relevance classifier runs first
(:mod:`gni.classifier.relevance`); items it marks ``"ignore"`` short-circuit
the build with ``draft_status = "ignored"`` and never reach the router or
guards.

This module is import-safe (no I/O at import time). The orchestrator in
``run_drafting.py`` is the only side-effecting layer.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from gni.classifier import classify_relevance
from gni.editorial.router import route_content
from gni.publisher.guards import EditorialResult, get_editorial_validator

# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

CONFIDENCE_REVIEW_THRESHOLD = 0.65

EDITORIAL_BUILD_TEMPLATES = {"BRIEFING", "FECHAMENTO"}
AUTO_BUILD_TEMPLATES = {"FLASH", "ALERTA", "RADAR"}

# Classifier↔router compatibility table.
# A classifier decision restricts the set of router templates that count as
# a "match". Anything outside this set forces draft_status="needs_review"
# and adds the "classifier_router_mismatch" risk flag.
CLASSIFIER_TEMPLATE_ALLOWLIST: dict[str, set[str]] = {
    "alerta":   {"FLASH", "ALERTA"},
    "briefing": {"RADAR", "BRIEFING", "FECHAMENTO"},
}

# ---------------------------------------------------------------------------
# Priority
# ---------------------------------------------------------------------------

PRIORITY_EMOJI = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🔵",
    "info": "🟢",
}

HIGH_PRIO_CATEGORIES = {"geopolitics", "markets", "cyber"}

CRITICAL_TITLE_KEYWORDS = (
    "breaking", "urgente", "urgent", "guerra", "war",
    "ataque", "attack", "crash", "hack", "breach", "exploit",
    "killed", "morto", "dead", "emergency", "emergência",
    "default", "collapse", "colapso", "outage", "ransomware",
    "zero-day", "0day",
)


def compute_priority(headline: dict[str, Any]) -> str:
    """V1 priority rules (spec):

    - title contains urgency keywords -> "critical"
    - category in HIGH_PRIO_CATEGORIES and tier == "tier1" -> "high"
    - otherwise -> "medium"
    """
    title = (headline.get("title") or "").lower()
    if any(k in title for k in CRITICAL_TITLE_KEYWORDS):
        return "critical"
    category = (headline.get("category") or "").lower()
    tier = (headline.get("tier") or "").lower()
    if category in HIGH_PRIO_CATEGORIES and tier == "tier1":
        return "high"
    return "medium"


def priority_emoji(priority: str) -> str:
    return PRIORITY_EMOJI.get(priority, "🟡")


# ---------------------------------------------------------------------------
# Routing text + dispatch
# ---------------------------------------------------------------------------


def routing_text(headline: dict[str, Any]) -> str:
    parts = [
        headline.get("title", ""),
        headline.get("raw_text", ""),
        headline.get("category", ""),
        headline.get("source_name", ""),
    ]
    return " ".join(p for p in parts if p)


def route_headline(headline: dict[str, Any]) -> dict:
    """Wraps router.route_content(text). Returns {template, confidence}."""
    return route_content(routing_text(headline))


# ---------------------------------------------------------------------------
# Payload builders (per template)
# ---------------------------------------------------------------------------


def _emoji_title(emoji: str, title: str) -> str:
    title = (title or "").strip()
    return f"{emoji} {title}".strip()


def build_flash_payload(headline: dict[str, Any], priority: str) -> dict:
    emoji = priority_emoji(priority)
    title = (headline.get("title") or "").strip()
    return {
        "template": "FLASH",
        "text": f"{emoji} {title} — [impact placeholder]",
    }


def build_alerta_payload(headline: dict[str, Any], priority: str) -> dict:
    emoji = priority_emoji(priority)
    title = (headline.get("title") or "").strip()
    source_name = headline.get("source_name") or ""
    category = headline.get("category") or ""
    url = headline.get("url") or ""
    return {
        "template": "ALERTA",
        "title": _emoji_title(emoji, title),
        "priority": priority,
        "summary": title,
        "key_points": [
            f"Source: {source_name}",
            f"Category: {category}",
        ],
        "source": url,
        "impact": "Impact requires operator review.",
    }


def build_radar_payload(headline: dict[str, Any], priority: str) -> dict:
    emoji = priority_emoji(priority)
    title = (headline.get("title") or "").strip()
    raw_text = (headline.get("raw_text") or "").strip() or title
    url = headline.get("url") or ""
    return {
        "template": "RADAR",
        "title": _emoji_title(emoji, title),
        "signal": title,
        "context": raw_text,
        "probability": "medium",
        "source": url,
        "implication": "Implication requires operator review.",
    }


def build_payload(template: str, headline: dict[str, Any], priority: str) -> dict | None:
    """Returns the structured payload for an auto-build template, or None
    when the template is BRIEFING / FECHAMENTO (editorial build required).
    """
    if template == "FLASH":
        return build_flash_payload(headline, priority)
    if template == "ALERTA":
        return build_alerta_payload(headline, priority)
    if template == "RADAR":
        return build_radar_payload(headline, priority)
    return None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_payload(payload: dict) -> tuple[bool, list[dict]]:
    """Run the editorial validator. Returns (ok, [{code, field, match}, ...])."""
    validator = get_editorial_validator()
    result: EditorialResult = validator.validate(payload)
    errors = [
        {"code": v.code, "field": v.field, "match": v.match}
        for v in result.violations
    ]
    return result.ok, errors


# ---------------------------------------------------------------------------
# Top-level draft assembly
# ---------------------------------------------------------------------------


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _classifier_input(headline: dict[str, Any]) -> dict:
    """Project a normalized headline onto the classifier input schema."""
    return {
        "headline":     headline.get("title") or "",
        "source_name":  headline.get("source_name") or "",
        "category":     headline.get("category") or "",
        "tier":         headline.get("tier") or "",
        "published_at": headline.get("published_at") or "",
    }


def build_draft(headline: dict[str, Any]) -> dict:
    """Assemble a draft record for one headline.

    Always returns a draft record. ``draft_status`` is one of:
      - "validated"               (auto-built + guard passed)
      - "failed_guard"            (auto-built + guard failed; payload kept)
      - "needs_review"            (classifier or router confidence < 0.65)
      - "needs_editorial_build"   (template requires multi-headline desk build)
      - "ignored"                 (classifier decided ``ignore``; never routed)

    Always carries a classifier metadata block:
      ``classifier_decision``, ``classifier_confidence``,
      ``classifier_reasons``, ``classifier_risk_flags``.
    """
    hash_key = headline.get("hash_key") or headline.get("id") or ""
    priority = compute_priority(headline)

    # ---- Step 1: classify (always runs, never throws) -------------------
    cls = classify_relevance(_classifier_input(headline))

    draft: dict[str, Any] = {
        "draft_id": f"draft_{hash_key}",
        "headline_hash_key": hash_key,
        "template": "",
        "route_confidence": 0.0,
        "draft_status": "",
        "priority": priority,
        "payload": {},
        "guard_errors": [],
        "classifier_decision":   cls.get("decision", "briefing"),
        "classifier_confidence": float(cls.get("confidence", 0.0)),
        "classifier_reasons":    list(cls.get("reasons") or []),
        "classifier_risk_flags": list(cls.get("risk_flags") or []),
        "router_template": "",
        "classifier_router_match": True,
        "mismatch_reason": "",
        "source_item": headline,
        "created_at": _now_utc_iso(),
    }

    # ---- Rule: classifier "ignore" → no route, no guards ----------------
    if draft["classifier_decision"] == "ignore":
        draft["draft_status"] = "ignored"
        return draft

    # ---- Step 2: route --------------------------------------------------
    routing = route_headline(headline)
    raw_template = routing.get("template", "RADAR")
    route_confidence = float(routing.get("confidence", 0.0))

    # Soft override: classifier "alerta" + router-default RADAR → ALERTA.
    # Router-confident FLASH / ALERTA outputs are preserved untouched.
    # Classifier "briefing" never overrides the router (it merely allows
    # BRIEFING/RADAR fall-through naturally).
    if draft["classifier_decision"] == "alerta" and raw_template == "RADAR":
        template = "ALERTA"
    else:
        template = raw_template

    draft["template"] = template
    draft["router_template"] = raw_template
    draft["route_confidence"] = route_confidence

    # ---- Step 3: classifier↔router compatibility check ------------------
    # If the router picked a template outside the classifier's allowlist,
    # do NOT auto-validate. Force needs_review and surface the conflict.
    allow = CLASSIFIER_TEMPLATE_ALLOWLIST.get(draft["classifier_decision"])
    if allow is not None and template not in allow:
        draft["classifier_router_match"] = False
        draft["mismatch_reason"] = (
            f"classifier={draft['classifier_decision']} "
            f"router={template}"
        )
        flags = draft["classifier_risk_flags"]
        if "classifier_router_mismatch" not in flags:
            flags.append("classifier_router_mismatch")
        draft["draft_status"] = "needs_review"
        if template not in EDITORIAL_BUILD_TEMPLATES:
            payload = build_payload(template, headline, priority)
            if payload is not None:
                draft["payload"] = payload
        return draft

    # ---- Rule: low classifier confidence → needs_review ----------------
    if draft["classifier_confidence"] < CONFIDENCE_REVIEW_THRESHOLD:
        draft["draft_status"] = "needs_review"
        if template not in EDITORIAL_BUILD_TEMPLATES:
            payload = build_payload(template, headline, priority)
            if payload is not None:
                draft["payload"] = payload
        return draft

    # ---- Existing path: editorial-build template -----------------------
    if template in EDITORIAL_BUILD_TEMPLATES:
        draft["draft_status"] = "needs_editorial_build"
        return draft

    # ---- Existing path: low router confidence → needs_review -----------
    if route_confidence < CONFIDENCE_REVIEW_THRESHOLD:
        draft["draft_status"] = "needs_review"
        payload = build_payload(template, headline, priority)
        if payload is not None:
            draft["payload"] = payload
        return draft

    # ---- Existing path: build + guard ----------------------------------
    payload = build_payload(template, headline, priority)
    if payload is None:
        draft["draft_status"] = "needs_editorial_build"
        return draft

    draft["payload"] = payload
    ok, errors = validate_payload(payload)
    if ok:
        draft["draft_status"] = "validated"
    else:
        draft["draft_status"] = "failed_guard"
        draft["guard_errors"] = errors
    return draft
