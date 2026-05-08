"""BRIEFING content generator (V1).

Deterministic, no-hallucination, guard-compatible BRIEFING payload builder.

Input
-----
::

    {
        "headline":    "<canonical headline title>",
        "category":    "<feed category, e.g. markets, geopolitics, cyber>",
        "source_name": "<feed display name>",
        "tier":        "<tier1 | tier2 | tier3>",
        "raw_text":    "<RSS summary; may be empty>",
        "url":         "<canonical url; may be empty>"
    }

Output (BRIEFING contract)::

    {
        "template":              "BRIEFING",
        "title":                 "<emoji> <subject> — <event> — <impact-tag>",
        "summary":               "<2-3 sentence factual recap>",
        "key_points":            ["fact 1", ..., "fact 5"],     # <= 5
        "context":               "<what led to the event>",
        "analysis":              "<structured factual framing>",
        "source":                "<url>",
        "impact":                "<forward implication, safe>",
        "needs_editorial_build": False                          # True on fallback
    }

Design choices
--------------
- Pure stdlib + ``re``. No LLM, no network, no I/O. Reuses helpers from
  :mod:`gni.generator.flash` and :mod:`gni.generator.alerta`.
- All analytical / contextual / impact strings are *deterministic labels*
  drawn from a fixed per-category map. Nothing is invented.
- "structured reasoning, no speculation unless clearly marked": ``analysis``
  is a factual present-tense framing per category. The single allowed
  forward-looking sentence is the ``impact`` field, which is also drawn
  from a fixed per-category map (no projection of new facts).
- "if insufficient info → mark needs_editorial_build": when the input
  cannot safely support a BRIEFING (empty / sub-8-char / severe-lexicon
  / no raw_text and short headline), the function returns a placeholder
  payload with ``needs_editorial_build: True`` so the editorial queue
  routes the item to the operator-build path (existing behavior preserved
  by ``draft_builder.py:303-305``).
"""
from __future__ import annotations

import re
from typing import Any

from gni.generator.alerta import (
    HIGH_PRIO_CATEGORIES,
    PRIORITY_TO_EMOJI,
    TITLE_MAX_CHARS,
    TITLE_MIN_CHARS,
    URGENCY_KEYWORDS,
    _DEFAULT_TITLE_IMPACT_TAG,
    _build_title,
    _compute_priority,
    _emoji_for_priority,
    _enforce_title_length,
    _strip_title_forbidden_chars,
)
from gni.generator.flash import (
    SEPARATOR,
    _enforce_single_sentence,
    _normalize_caps,
    _strip_quotes,
    _strip_trailing_punct,
    _violates_lexicon,
)

# ---------------------------------------------------------------------------
# Length budgets (mirror BRIEFING_LONG body expectations + guard limits)
# ---------------------------------------------------------------------------

SUMMARY_MIN_SENTENCES = 2
SUMMARY_MAX_SENTENCES = 3
SUMMARY_MAX_CHARS = 600
KEY_POINTS_MAX = 5
CONTEXT_MAX_CHARS = 400
ANALYSIS_MAX_CHARS = 400
IMPACT_MAX_CHARS = 280

_INTERNAL_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")

# ---------------------------------------------------------------------------
# Deterministic per-category label maps (no invented facts)
# ---------------------------------------------------------------------------

# What led to the event (factual present-tense framing). Used when raw_text
# does not give us a usable second/third sentence we can quote verbatim.
_CONTEXT_LABELS: dict[str, str] = {
    "markets":     "Cross-asset positioning has been under active review by market participants.",
    "macro":       "Macro environment has remained under close watch by central-bank observers.",
    "crypto":      "On-chain flows and exchange balances have been under continued surveillance.",
    "geopolitics": "Regional dynamics have been under elevated diplomatic and analytical attention.",
    "cyber":       "Threat-actor activity has been under active monitoring by defender teams.",
    "ai":          "AI sector has been under continued regulatory and competitive scrutiny.",
    "tech":        "Tech sector has been under continued competitive and product-cycle review.",
    "energy":      "Energy market has been under continued supply-demand reassessment.",
    "health":      "Health sector has been under continued regulatory and public-health review.",
    "social":      "Public discourse has been under continued sentiment and policy tracking.",
}
_DEFAULT_CONTEXT_LABEL = (
    "Sector context has been under continued analytical attention from desk operators."
)

# Structured reasoning per category (no speculation; present-tense framing).
_ANALYSIS_LABELS: dict[str, str] = {
    "markets":     "Cross-asset response is the operative dimension; rate-path and FX correlation remain the primary read.",
    "macro":       "Central-bank reaction function is the operative dimension; rate path and forward guidance are the primary read.",
    "crypto":      "On-chain exposure and exchange flows are the operative dimensions; spot-derivative basis is the secondary read.",
    "geopolitics": "Regional escalation potential is the operative dimension; policy response sequencing is the primary read.",
    "cyber":       "Exposure surface is the operative dimension; patch posture and detection coverage drive the read.",
    "ai":          "Competitive positioning is the operative dimension; regulatory follow-up is the secondary read.",
    "tech":        "Product cycle and competitive response are the operative dimensions; supply-chain exposure is the secondary read.",
    "energy":      "Supply-demand balance is the operative dimension; commodity-price reaction across regions is the read.",
    "health":      "Regulatory response is the operative dimension; public-communication risk is the secondary read.",
    "social":      "Sentiment trajectory is the operative dimension; policy follow-up is the secondary read.",
}
_DEFAULT_ANALYSIS_LABEL = (
    "Cross-domain response is the operative dimension; downstream policy and market follow-up remain the primary read."
)

# Forward-looking implication (safe, non-speculative — phrased as monitoring
# guidance rather than a prediction).
_IMPACT_LABELS: dict[str, str] = {
    "markets":     "Operators should monitor cross-asset response and downstream price action over the next sessions.",
    "macro":       "Operators should monitor central-bank commentary and rate-path expectations into the next meeting.",
    "crypto":      "Operators should monitor on-chain flows, exchange balances and derivatives basis in the coming sessions.",
    "geopolitics": "Operators should track regional escalation and coordinated policy response in the coming days.",
    "cyber":       "Operators should review exposure, patch posture and detection coverage across affected systems.",
    "ai":          "Operators should track follow-on releases and the regulatory response over the next cycle.",
    "tech":        "Operators should track competitive response and product-cycle implications across peers.",
    "energy":      "Operators should monitor commodity price action and supply-side response in the coming sessions.",
    "health":      "Operators should monitor regulatory response and public-communication exposure across stakeholders.",
    "social":      "Operators should monitor public sentiment trajectory and downstream policy follow-up.",
}
_DEFAULT_IMPACT_LABEL = (
    "Operators should monitor cross-asset reaction and downstream policy follow-up across affected sectors."
)

# Severe-at-start patterns that prevent any safe BRIEFING generation.
_SEVERE_HEADLINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^você\s+não\s+vai\s+acreditar", re.IGNORECASE),
    re.compile(r"^o\s+que\s+está\s+por\s+trás",  re.IGNORECASE),
    re.compile(r"^acreditamos\b",                re.IGNORECASE),
    re.compile(r"^entendemos\b",                 re.IGNORECASE),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _label_for(mapping: dict[str, str], category: str, default: str) -> str:
    return mapping.get((category or "").strip().lower(), default)


def _split_sentences(text: str) -> list[str]:
    if not text:
        return []
    parts = _INTERNAL_SENTENCE_BOUNDARY_RE.split(text.strip())
    return [p.strip() for p in parts if p and p.strip()]


def _truncate_at_word(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return _strip_trailing_punct(cut) + "."


def _is_low_confidence(headline: str, raw_text: str) -> bool:
    """Insufficient-info gate.

    A BRIEFING is long-form. We require either:
      - a headline >= 16 chars, OR
      - a non-empty raw_text plus a headline >= 8 chars.
    Severe-lexicon hits at headline start always disqualify.
    """
    h = (headline or "").strip()
    rt = (raw_text or "").strip()
    if len(h) < 8:
        return True
    if any(p.search(h) for p in _SEVERE_HEADLINE_PATTERNS):
        return True
    if len(h) < 16 and not rt:
        return True
    return False


def _build_summary(headline: str, raw_text: str) -> str:
    """Build a 2-3 sentence factual recap.

    Sentence 1: normalized headline (terminated with ``.``).
    Sentence 2/3 (optional): first 1-2 lexicon-clean sentences of raw_text
    that are not redundant with the headline.
    """
    h = _strip_trailing_punct(_strip_quotes(headline or "")).strip()
    if not h:
        return ""

    sentences: list[str] = [_normalize_caps(h) + "."]
    used_lower: set[str] = {h.lower()}

    for s in _split_sentences(raw_text or ""):
        if len(sentences) >= SUMMARY_MAX_SENTENCES:
            break
        clean = _strip_trailing_punct(_strip_quotes(s)).strip()
        if not clean:
            continue
        low = clean.lower()
        if low in used_lower:
            continue
        if _violates_lexicon(clean):
            continue
        candidate = _normalize_caps(clean) + "."
        joined_len = sum(len(x) for x in sentences) + len(sentences) + len(candidate)
        if joined_len > SUMMARY_MAX_CHARS:
            break
        sentences.append(candidate)
        used_lower.add(low)

    summary = " ".join(sentences)
    if len(summary) > SUMMARY_MAX_CHARS:
        summary = _truncate_at_word(summary, SUMMARY_MAX_CHARS)
    if _violates_lexicon(summary):
        summary = _normalize_caps(h) + "."
    return summary


def _build_key_points(
    headline: str, raw_text: str, source_name: str, category: str, tier: str
) -> list[str]:
    """Up to 5 single-fact bullets, all derived from input.

    Strategy:
      1. Pull up to 3 lexicon-clean, non-redundant sentences from raw_text.
      2. Append metadata-label bullets (Source / Category / Tier) until the
         5-bullet ceiling is reached.
    All bullets are single-fact (no ``;``, no "e também" / "além disso").
    """
    bullets: list[str] = []
    seen_lower: set[str] = set()

    h = (headline or "").strip().lower()
    if h:
        seen_lower.add(h)

    for s in _split_sentences(raw_text or ""):
        if len(bullets) >= 3:
            break
        clean = _strip_trailing_punct(_strip_quotes(s)).strip()
        if not clean:
            continue
        low = clean.lower()
        if low in seen_lower:
            continue
        if _violates_lexicon(clean):
            continue
        bullets.append(_normalize_caps(clean))
        seen_lower.add(low)

    # Metadata-label bullets fill the remaining slots.
    meta: list[str] = []
    if source_name:
        meta.append(f"Source: {source_name}")
    if category:
        meta.append(f"Category: {category}")
    if tier:
        meta.append(f"Tier: {tier}")
    for m in meta:
        if len(bullets) >= KEY_POINTS_MAX:
            break
        if _violates_lexicon(m):
            continue
        bullets.append(m)

    return bullets[:KEY_POINTS_MAX]


def _build_context(raw_text: str, category: str) -> str:
    """What led to the event.

    Prefer a raw_text sentence (sentence #2 onward) that adds factual
    background; fall back to the deterministic per-category context label.
    """
    sentences = _split_sentences(raw_text or "")
    # Skip the first raw_text sentence — it often duplicates the headline.
    for s in sentences[1:]:
        clean = _strip_trailing_punct(_strip_quotes(s)).strip()
        if clean and not _violates_lexicon(clean) and len(clean) >= 24:
            ctx = _normalize_caps(clean) + "."
            if len(ctx) <= CONTEXT_MAX_CHARS:
                return ctx
            return _truncate_at_word(ctx, CONTEXT_MAX_CHARS)
    return _label_for(_CONTEXT_LABELS, category, _DEFAULT_CONTEXT_LABEL)


def _build_analysis(category: str) -> str:
    label = _label_for(_ANALYSIS_LABELS, category, _DEFAULT_ANALYSIS_LABEL)
    if len(label) > ANALYSIS_MAX_CHARS:
        return _truncate_at_word(label, ANALYSIS_MAX_CHARS)
    return label


def _build_impact(category: str) -> str:
    label = _label_for(_IMPACT_LABELS, category, _DEFAULT_IMPACT_LABEL)
    if len(label) > IMPACT_MAX_CHARS:
        return _truncate_at_word(label, IMPACT_MAX_CHARS)
    return label


def _fallback_payload(item: dict[str, Any]) -> dict[str, Any]:
    """Insufficient-info path. Returns a placeholder BRIEFING payload with
    ``needs_editorial_build: True`` so the queue routes the item to the
    operator-build flow.
    """
    headline = (item or {}).get("headline") or ""
    category = (item or {}).get("category") or ""
    source_name = (item or {}).get("source_name") or ""
    tier = (item or {}).get("tier") or ""
    url = (item or {}).get("url") or ""

    priority = _compute_priority(headline, category, tier)
    emoji = _emoji_for_priority(priority)

    h_clean = _normalize_caps(
        _strip_title_forbidden_chars(_strip_trailing_punct(headline))
    ).strip() or "pending headline"
    anchor = source_name or category or "GNI"
    title = f"{emoji} {h_clean} — {anchor} — operator review"
    if len(title) < TITLE_MIN_CHARS:
        title = f"{title} — {_DEFAULT_TITLE_IMPACT_TAG}"
    title = _enforce_title_length(title)

    key_points: list[str] = []
    if source_name:
        key_points.append(f"Source: {source_name}")
    if category:
        key_points.append(f"Category: {category}")
    if tier:
        key_points.append(f"Tier: {tier}")

    return {
        "template":              "BRIEFING",
        "title":                 title,
        "summary":               "Summary requires operator review.",
        "key_points":            key_points[:KEY_POINTS_MAX],
        "context":               "Context requires operator review.",
        "analysis":              "Analysis requires operator review.",
        "source":                url,
        "impact":                "Impact requires operator review.",
        "needs_editorial_build": True,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_briefing(item: dict[str, Any]) -> dict[str, Any]:
    """Build a BRIEFING payload from a normalized headline item.

    Always returns a payload (never raises). On insufficient input the
    payload is the operator-review fallback with
    ``needs_editorial_build: True``.

    Output shape conforms to the BRIEFING contract documented at the top
    of this module and is byte-compatible with downstream guard checks
    (``EditorialValidator`` + ``validate_for_format(text, "BRIEFING_LONG")``).
    """
    if not isinstance(item, dict):
        return _fallback_payload({})

    headline = (item.get("headline") or "").strip()
    raw_text = (item.get("raw_text") or "").strip()
    category = (item.get("category") or "").strip()
    source_name = (item.get("source_name") or "").strip()
    tier = (item.get("tier") or "").strip()
    url = (item.get("url") or "").strip()

    headline = _strip_quotes(_enforce_single_sentence(headline))
    headline = _strip_trailing_punct(headline)

    if _is_low_confidence(headline, raw_text):
        return _fallback_payload({
            "headline":    headline,
            "category":    category,
            "source_name": source_name,
            "tier":        tier,
            "url":         url,
        })

    priority = _compute_priority(headline, category, tier)
    emoji = _emoji_for_priority(priority)

    title = _build_title(emoji, headline, category, source_name)
    summary = _build_summary(headline, raw_text)
    key_points = _build_key_points(headline, raw_text, source_name, category, tier)
    context = _build_context(raw_text, category)
    analysis = _build_analysis(category)
    impact = _build_impact(category)

    # Defensive lexicon scan on every text field. If something slips through,
    # replace with the operator-review placeholder so the downstream guards
    # mark ``failed_guard`` rather than the generator silently emitting bad
    # text.
    if _violates_lexicon(summary):
        summary = "Summary requires operator review."
    if _violates_lexicon(context):
        context = "Context requires operator review."
    if _violates_lexicon(analysis):
        analysis = "Analysis requires operator review."
    if _violates_lexicon(impact):
        impact = "Impact requires operator review."

    return {
        "template":              "BRIEFING",
        "title":                 title,
        "summary":               summary,
        "key_points":            key_points,
        "context":               context,
        "analysis":              analysis,
        "source":                url,
        "impact":                impact,
        "needs_editorial_build": False,
    }


__all__ = [
    "generate_briefing",
    "SUMMARY_MIN_SENTENCES",
    "SUMMARY_MAX_SENTENCES",
    "SUMMARY_MAX_CHARS",
    "KEY_POINTS_MAX",
    "CONTEXT_MAX_CHARS",
    "ANALYSIS_MAX_CHARS",
    "IMPACT_MAX_CHARS",
]
