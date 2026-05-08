"""ALERTA content generator (V1).

Deterministic, no-hallucination, guard-compatible ALERTA payload builder.

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

Output (matches the ALERTA contract enforced by
``gni.publisher.guards.EditorialValidator``)::

    {
        "template":   "ALERTA",
        "title":      "<emoji> <subject> — <event> — <impact-tag>",  # 40-90 chars
        "priority":   "critical | high | medium | low | info",
        "summary":    "<1-sentence factual recap>",
        "key_points": ["fact bullet 1", "fact bullet 2", "fact bullet 3"],
        "source":     "<url>",
        "impact":     "<1-sentence operational impact>"
    }

Design choices
--------------
Pure stdlib + ``re``. No LLM, no network. Designed for sub-millisecond per
call so it can be invoked inline from
``gni.drafting.draft_builder.build_alerta_payload``.

Reuses helpers from :mod:`gni.generator.flash` for emoji selection,
ALL-CAPS normalization, forbidden-lexicon scan and subject/event split.

Safety
------
- "use only headline + raw_text": every word in ``summary`` comes from the
  input headline (or its first raw_text sentence). Title segments come from
  the input subject/event plus a deterministic category-tag label. ``impact``
  is one of a fixed set of operational sentences (label, not a fact).
- On low-confidence input (empty / sub-8-char / severe-lexicon-hit headline),
  the generator returns a TEMPLATE-DRAFT payload with operator-review
  placeholders so the operator has a starting point.
"""
from __future__ import annotations

import re
from typing import Any

from gni.generator.flash import (
    PRIORITY_EMOJI_CRITICAL,
    PRIORITY_EMOJI_HIGH,
    PRIORITY_EMOJI_MEDIUM,
    SEPARATOR,
    _enforce_single_sentence,
    _normalize_caps,
    _split_subject_event,
    _strip_quotes,
    _strip_trailing_punct,
    _violates_lexicon,
)

# ---------------------------------------------------------------------------
# Constraints (mirror gni/templates/forbidden_lexicon.json + headline_pattern)
# ---------------------------------------------------------------------------

TITLE_MIN_CHARS = 40
TITLE_MAX_CHARS = 90
SUMMARY_MAX_CHARS = 280
IMPACT_MAX_CHARS = 280
KEY_POINTS_MAX = 3

PRIORITY_EMOJI_LOW = "🔵"
PRIORITY_EMOJI_INFO = "🟢"

PRIORITY_TO_EMOJI: dict[str, str] = {
    "critical": PRIORITY_EMOJI_CRITICAL,
    "high":     PRIORITY_EMOJI_HIGH,
    "medium":   PRIORITY_EMOJI_MEDIUM,
    "low":      PRIORITY_EMOJI_LOW,
    "info":     PRIORITY_EMOJI_INFO,
}

# Mirrors gni/drafting/draft_builder.py: HIGH_PRIO_CATEGORIES + URGENCY KEYWORDS.
HIGH_PRIO_CATEGORIES: frozenset[str] = frozenset(
    {"geopolitics", "markets", "cyber"}
)
URGENCY_KEYWORDS: tuple[str, ...] = (
    "breaking", "urgente", "urgent", "guerra", "war",
    "ataque", "attack", "crash", "hack", "breach", "exploit",
    "killed", "morto", "dead", "emergency", "emergência",
    "default", "collapse", "colapso", "outage", "ransomware",
    "zero-day", "0day",
)

# Extended category-impact tags used in the ALERTA *title* (longer than the
# FLASH tags so titles reach the 40-char minimum naturally).
_TITLE_IMPACT_TAGS: dict[str, str] = {
    "markets":     "market reaction across affected assets",
    "macro":       "macro impact and central-bank outlook",
    "crypto":      "crypto market reaction and on-chain flows",
    "geopolitics": "geopolitical and policy implications",
    "cyber":       "security implications and exposure review",
    "ai":          "AI sector and policy implications",
    "tech":        "tech sector competitive implications",
    "energy":      "energy market reaction across commodities",
    "health":      "public-health policy implications",
    "social":      "public reaction and policy follow-up",
}
_DEFAULT_TITLE_IMPACT_TAG = "cross-asset reaction watch"

# Operational impact sentences for the *impact field*. Vocabulary is
# deliberately distinct from the title-impact tag so the redundancy guard
# (``token_set_ratio < 0.85`` between ``summary``/``key_points``/``impact``)
# is satisfied in the steady state.
_IMPACT_SENTENCES: dict[str, str] = {
    "markets":     "Operators should monitor cross-asset response and downstream price action.",
    "macro":       "Operators should monitor central-bank commentary and rate path.",
    "crypto":      "Operators should monitor on-chain flows and exchange exposure.",
    "geopolitics": "Operators should track regional escalation risk and policy response.",
    "cyber":       "Operators should review exposure, patch posture, and incident detection.",
    "ai":          "Operators should track follow-on releases and regulatory response.",
    "tech":        "Operators should track competitive response and product-cycle implications.",
    "energy":      "Operators should monitor commodity price action and supply response.",
    "health":      "Operators should monitor regulatory response and public-communication risk.",
    "social":      "Operators should monitor public sentiment and policy follow-up.",
}
_DEFAULT_IMPACT_SENTENCE = (
    "Operators should monitor cross-asset reaction and downstream follow-up."
)

# headline_pattern.forbidden_chars + forbidden_endings (lexicon source).
_TITLE_FORBIDDEN_CHARS_RE = re.compile(r"[\"'…]")
_TITLE_FORBIDDEN_TRAIL_RE = re.compile(r"[\.\!\?]+$")

# Severe-at-start patterns that prevent any safe ALERTA generation. Hitting
# one routes to the fallback (template-draft) payload so operators can
# regenerate with a cleaner source. Subset of the full forbidden lexicon.
_SEVERE_HEADLINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^você\s+não\s+vai\s+acreditar", re.IGNORECASE),
    re.compile(r"^o\s+que\s+está\s+por\s+trás",  re.IGNORECASE),
    re.compile(r"^acreditamos\b",                re.IGNORECASE),
    re.compile(r"^entendemos\b",                 re.IGNORECASE),
)

_INTERNAL_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?]\s+")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compute_priority(headline: str, category: str, tier: str) -> str:
    """Mirror of ``gni.drafting.draft_builder.compute_priority``."""
    title_lc = (headline or "").lower()
    if any(k in title_lc for k in URGENCY_KEYWORDS):
        return "critical"
    cat = (category or "").strip().lower()
    t = (tier or "").strip().lower()
    if cat in HIGH_PRIO_CATEGORIES and t == "tier1":
        return "high"
    return "medium"


def _emoji_for_priority(priority: str) -> str:
    return PRIORITY_TO_EMOJI.get(priority, PRIORITY_EMOJI_MEDIUM)


def _title_impact_tag(category: str) -> str:
    return _TITLE_IMPACT_TAGS.get(
        (category or "").strip().lower(), _DEFAULT_TITLE_IMPACT_TAG
    )


def _impact_sentence(category: str) -> str:
    return _IMPACT_SENTENCES.get(
        (category or "").strip().lower(), _DEFAULT_IMPACT_SENTENCE
    )


def _strip_title_forbidden_chars(s: str) -> str:
    return _TITLE_FORBIDDEN_CHARS_RE.sub("", s or "")


def _enforce_title_length(title: str) -> str:
    """Trim trailing forbidden punctuation and cap at TITLE_MAX_CHARS at a
    word boundary. Padding to TITLE_MIN_CHARS is the caller's responsibility
    (via choice of impact tag / additional segment)."""
    if not title:
        return title
    title = _TITLE_FORBIDDEN_TRAIL_RE.sub("", title).rstrip()
    if len(title) <= TITLE_MAX_CHARS:
        return title
    cut = title[:TITLE_MAX_CHARS]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return _TITLE_FORBIDDEN_TRAIL_RE.sub("", cut).rstrip()


def _build_title(
    emoji: str, headline: str, category: str, source_name: str
) -> str:
    """Build a 3- or 4-segment title within the 40-90 char window.

    Strategy:
      1. ``<emoji> <subj> — <event> — <impact_tag>`` using the EXTENDED
         title-impact tag so most cases land >= 40 chars naturally.
      2. If still too short, append ``" — <source_name> report"``.
      3. If still too short, append ``" — <default_tag>"``.
      4. If too long, truncate at the last word boundary <= 90 chars.
    """
    impact_tag = _title_impact_tag(category)
    subj, event = _split_subject_event(headline)
    if subj and event:
        body = f"{subj}{SEPARATOR}{event}{SEPARATOR}{impact_tag}"
    else:
        anchor = source_name or category or "GNI"
        body = f"{headline}{SEPARATOR}{anchor}{SEPARATOR}{impact_tag}"

    title = f"{emoji} {body}"
    title = _strip_title_forbidden_chars(title)
    title = _normalize_caps(title)

    if len(title) < TITLE_MIN_CHARS and source_name:
        title = f"{title} — {source_name} report"
    if len(title) < TITLE_MIN_CHARS:
        title = f"{title} — {_DEFAULT_TITLE_IMPACT_TAG}"

    return _enforce_title_length(title)


def _build_summary(headline: str, raw_text: str) -> str:
    """Single-sentence factual recap. Adds a second sentence from raw_text
    only if it is non-redundant, lexicon-clean, and fits the budget."""
    h = _strip_trailing_punct(_strip_quotes(headline or "")).strip()
    if not h:
        return ""
    h_norm = _normalize_caps(h)
    summary = h_norm + "."

    rt = (raw_text or "").strip()
    if rt:
        first = _INTERNAL_SENTENCE_BOUNDARY_RE.split(rt, maxsplit=1)[0].strip()
        first = _strip_trailing_punct(_strip_quotes(first))
        if (
            first
            and first.lower() != h.lower()
            and not _violates_lexicon(first)
            and len(summary) + len(first) + 2 <= SUMMARY_MAX_CHARS
        ):
            first = _normalize_caps(first)
            summary = f"{summary} {first}."

    if len(summary) > SUMMARY_MAX_CHARS:
        cut = summary[:SUMMARY_MAX_CHARS]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        summary = _strip_trailing_punct(cut) + "."

    if _violates_lexicon(summary):
        # Defensive: fall back to bare normalized headline.
        summary = h_norm + "."

    return summary


def _build_key_points(
    source_name: str, category: str, tier: str
) -> list[str]:
    """Up to 3 single-fact label bullets, all derived from input metadata.

    Each bullet is a single fact (no ``;``, no ``e também``, no ``além disso``).
    """
    bullets: list[str] = []
    if source_name:
        bullets.append(f"Source: {source_name}")
    if category:
        bullets.append(f"Category: {category}")
    if tier:
        bullets.append(f"Tier: {tier}")
    bullets = [b for b in bullets if not _violates_lexicon(b)]
    return bullets[:KEY_POINTS_MAX]


def _build_impact(category: str) -> str:
    sentence = _impact_sentence(category)
    if len(sentence) > IMPACT_MAX_CHARS:
        cut = sentence[:IMPACT_MAX_CHARS]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        sentence = _strip_trailing_punct(cut) + "."
    return sentence


def _is_low_confidence(headline: str) -> bool:
    """True when the input headline is too thin or too dirty to safely
    generate. Triggers the fallback (template-draft) payload."""
    h = (headline or "").strip()
    if len(h) < 8:
        return True
    return any(p.search(h) for p in _SEVERE_HEADLINE_PATTERNS)


def _fallback_payload(item: dict[str, Any]) -> dict[str, Any]:
    """Template-draft fallback. Same shape as a real generated ALERTA, but
    content fields the generator could not safely produce are replaced with
    operator-review placeholders. The operator can edit the payload in place
    via the existing queue tooling.
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

    return {
        "template":   "ALERTA",
        "title":      title,
        "priority":   priority,
        "summary":    "Summary requires operator review.",
        "key_points": _build_key_points(source_name, category, tier),
        "source":     url,
        "impact":     "Impact requires operator review.",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_alerta(item: dict[str, Any]) -> dict[str, Any]:
    """Build an ALERTA payload from a normalized headline item.

    Always returns a payload (never raises). On low-confidence input, falls
    back to a template-draft payload so the operator has a starting point.

    Output shape matches the contract enforced by
    ``gni.publisher.guards.EditorialValidator`` for the ALERTA template.
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

    if _is_low_confidence(headline):
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
    key_points = _build_key_points(source_name, category, tier)
    impact = _build_impact(category)

    # Defensive: if any content field still trips the forbidden lexicon
    # (shouldn't, but cheap to guard), drop the offending field to a
    # placeholder so guards mark failed_guard rather than the generator
    # silently emitting bad text.
    if _violates_lexicon(summary):
        summary = "Summary requires operator review."
    if _violates_lexicon(impact):
        impact = "Impact requires operator review."

    return {
        "template":   "ALERTA",
        "title":      title,
        "priority":   priority,
        "summary":    summary,
        "key_points": key_points,
        "source":     url,
        "impact":     impact,
    }
