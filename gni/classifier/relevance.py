"""GNI V1 relevance classifier — rule-based, deterministic, no external APIs.

Classifies a normalized headline into one of three editorial decisions:

  * ``alerta``   — high-impact / urgent · downstream auto-alerts path
  * ``briefing`` — context-worthy / scheduled · operator briefing path
  * ``ignore``   — low-value · drop before drafting

Design constraints (per spec):
  * Pure function. No I/O. No mutation of input.
  * Deterministic: identical input ⇒ identical output, byte-for-byte.
  * Safe default: when uncertain, choose ``briefing`` (never silent ``ignore``).
  * High-impact (urgency) keywords NEVER produce ``ignore``.

Rule order applied (highest first):
  1. Empty / non-text headline               → briefing (0.30) + risk flag
  2. Urgency keyword present                  → alerta   (rule 1, 3)
  3. Scheduled-summary pattern present        → briefing (rule 7)
  4. Low-value pattern + no impact keyword    → ignore   (rule 6)
  5. Impact keyword + tier-1 source           → alerta   (rule 4)
  6. Impact keyword + non-tier-1 source       → briefing (rule 4 soft)
  7. No keyword + non-tier-1 source           → ignore   (rule 5)
  8. Otherwise (tier-1, no keyword)           → briefing (rule 2 fallback)
"""
from __future__ import annotations

import re
from typing import Any

__all__ = ["classify_relevance"]


# ---------------------------------------------------------------------------
# Lexicons (frozen tuples → deterministic iteration order)
# ---------------------------------------------------------------------------

URGENCY: tuple[str, ...] = (
    # EN
    "attack", "strike", "explosion", "sanctions", "default", "hack",
    "breach", "outage", "war", "missile", "emergency",
    # PT-BR
    "ataque", "explosão", "sanções", "calote", "invasão", "vazamento",
    "guerra", "míssil", "emergência",
)

MARKET_IMPACT: tuple[str, ...] = (
    # EN
    "fed", "rates", "inflation", "cpi", "jobs", "oil", "dollar", "treasury",
    "bitcoin", "ethereum", "stocks", "futures",
    # PT-BR
    "juros", "inflação", "ipca", "petróleo", "dólar", "bolsa", "futuros",
)

GEOPOLITICS: tuple[str, ...] = (
    # EN
    "us", "china", "russia", "ukraine", "iran", "israel", "nato",
    "taiwan", "red sea",
    # PT-BR
    "eua", "rússia", "ucrânia", "irã", "otan",
)

CYBER: tuple[str, ...] = (
    # EN
    "ransomware", "malware", "breach", "exploit", "vulnerability", "cve",
    "data leak",
    # PT-BR
    "vulnerabilidade", "ataque cibernético",
)

AI_DOMAIN: tuple[str, ...] = (
    # EN
    "openai", "anthropic", "google ai", "nvidia", "model", "chip",
    "regulation", "lawsuit",
    # PT-BR
    "inteligência artificial", "modelo", "regulação", "processo",
)

# Scheduled summary / brief patterns → briefing (rule 7)
SCHEDULED_SUMMARY: tuple[str, ...] = (
    "weekly recap", "daily wrap", "market wrap", "summary", "outlook",
    "preview", "in brief", "what to watch", "morning brief", "evening brief",
    "fechamento", "abertura", "balanço semanal", "resumo do dia",
    "panorama", "agenda",
)

# Low-value patterns → ignore when no impact keyword present (rule 6)
LOW_VALUE: tuple[str, ...] = (
    "opinion:", "column:", "editorial:", "sponsored", "promoted",
    "top 5", "top 10", "best of", "watch:", "video:",
    "opinião:", "patrocinado", "vídeo:",
)

# Source-tier values that count as tier-1 (case-insensitive).
TIER1_VALUES: frozenset[str] = frozenset({"tier1", "tier-1", "1", "t1"})


# ---------------------------------------------------------------------------
# Matching helpers (compiled once at import → deterministic, fast)
# ---------------------------------------------------------------------------


def _norm_text(s: Any) -> str:
    """Lowercase + collapse whitespace; safe for non-string input."""
    if not isinstance(s, str):
        return ""
    return " ".join(s.strip().lower().split())


def _word_boundary_pattern(kw: str) -> re.Pattern[str]:
    """Match ``kw`` with non-word boundaries on both sides (Unicode-aware)."""
    escaped = re.escape(kw.lower())
    return re.compile(rf"(?<!\w){escaped}(?!\w)", re.UNICODE)


def _compile_table(table: tuple[str, ...]) -> list[tuple[re.Pattern[str], str]]:
    return [(_word_boundary_pattern(k), k) for k in table]


_URGENCY_RE = _compile_table(URGENCY)
_MARKET_RE = _compile_table(MARKET_IMPACT)
_GEO_RE = _compile_table(GEOPOLITICS)
_CYBER_RE = _compile_table(CYBER)
_AI_RE = _compile_table(AI_DOMAIN)
_SCHED_RE = _compile_table(SCHEDULED_SUMMARY)
_LOWVAL_RE = _compile_table(LOW_VALUE)


def _hits(text: str, table: list[tuple[re.Pattern[str], str]]) -> list[str]:
    """Return matched keywords in lexicon order, deduped, original-cased."""
    found: list[str] = []
    seen: set[str] = set()
    for pat, kw in table:
        if kw in seen:
            continue
        if pat.search(text):
            found.append(kw)
            seen.add(kw)
    return found


def _is_tier1(tier: Any) -> bool:
    if not isinstance(tier, str):
        return False
    return tier.strip().lower() in TIER1_VALUES


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_relevance(item: dict) -> dict:
    """Classify a normalized headline as ``alerta`` / ``briefing`` / ``ignore``.

    Pure function. Does not mutate ``item``. Same input ⇒ same output.

    Parameters
    ----------
    item : dict
        Required keys (all string-typed; missing/None tolerated):
          - ``headline``     : the headline text to classify
          - ``source_name``  : free-form source identifier
          - ``category``     : feed category (advisory only)
          - ``tier``         : "tier1" / "tier2" / etc. (drives rules 3–5)
          - ``published_at`` : ISO-8601 string (advisory only)

    Returns
    -------
    dict with keys:
      - ``decision``    : one of {"ignore", "alerta", "briefing"}
      - ``confidence``  : float in [0.0, 1.0], rounded to 2 decimals
      - ``reasons``     : ordered list[str] of triggered rule tags
      - ``risk_flags``  : ordered list[str] of caveats for the operator
    """
    headline = _norm_text(item.get("headline"))
    tier1 = _is_tier1(item.get("tier"))

    reasons: list[str] = []
    risk_flags: list[str] = []

    # ----- Rule 0: empty / non-text headline → safe briefing -----
    if not headline:
        return {
            "decision": "briefing",
            "confidence": 0.30,
            "reasons": ["empty_or_non_text_headline"],
            "risk_flags": ["empty_headline"],
        }

    urgency_hits = _hits(headline, _URGENCY_RE)
    market_hits = _hits(headline, _MARKET_RE)
    geo_hits = _hits(headline, _GEO_RE)
    cyber_hits = _hits(headline, _CYBER_RE)
    ai_hits = _hits(headline, _AI_RE)
    sched_hits = _hits(headline, _SCHED_RE)
    lowval_hits = _hits(headline, _LOWVAL_RE)

    impact_hits = market_hits + geo_hits + cyber_hits + ai_hits
    has_impact = bool(impact_hits)

    # ----- Rule 1+3: urgency overrides everything → alerta (never ignore) -----
    if urgency_hits:
        if tier1:
            confidence = min(0.99, 0.90 + 0.02 * len(urgency_hits))
            reasons.append("urgency_hit:" + ",".join(urgency_hits))
            reasons.append("tier1_source")
        else:
            confidence = min(0.85, 0.75 + 0.02 * len(urgency_hits))
            reasons.append("urgency_hit:" + ",".join(urgency_hits))
            reasons.append("non_tier1_source")
            risk_flags.append("urgency_on_non_tier1")
        if lowval_hits:
            risk_flags.append("conflicting_low_value:" + ",".join(lowval_hits))
        return {
            "decision": "alerta",
            "confidence": round(confidence, 2),
            "reasons": reasons,
            "risk_flags": risk_flags,
        }

    # ----- Rule 7: scheduled macro summary → briefing -----
    if sched_hits:
        confidence = min(0.90, 0.80 + 0.02 * len(sched_hits))
        reasons.append("scheduled_summary:" + ",".join(sched_hits))
        if has_impact:
            reasons.append("impact_hit:" + ",".join(impact_hits[:5]))
        return {
            "decision": "briefing",
            "confidence": round(confidence, 2),
            "reasons": reasons,
            "risk_flags": risk_flags,
        }

    # ----- Rule 6: low-value pattern + no impact → ignore -----
    if lowval_hits and not has_impact:
        confidence = min(0.90, 0.80 + 0.02 * len(lowval_hits))
        reasons.append("low_value:" + ",".join(lowval_hits))
        reasons.append("no_impact_keyword")
        return {
            "decision": "ignore",
            "confidence": round(confidence, 2),
            "reasons": reasons,
            "risk_flags": risk_flags,
        }

    # ----- Rule 4: impact + tier-1 → alerta -----
    if has_impact and tier1:
        confidence = min(0.85, 0.65 + 0.05 * len(impact_hits))
        reasons.append("impact_hit:" + ",".join(impact_hits[:5]))
        reasons.append("tier1_source")
        if lowval_hits:
            risk_flags.append("conflicting_low_value:" + ",".join(lowval_hits))
        return {
            "decision": "alerta",
            "confidence": round(confidence, 2),
            "reasons": reasons,
            "risk_flags": risk_flags,
        }

    # ----- Rule 4 (soft): impact + non-tier-1 → briefing -----
    if has_impact:
        confidence = min(0.75, 0.55 + 0.05 * len(impact_hits))
        reasons.append("impact_hit:" + ",".join(impact_hits[:5]))
        reasons.append("non_tier1_source")
        if lowval_hits:
            risk_flags.append("conflicting_low_value:" + ",".join(lowval_hits))
        return {
            "decision": "briefing",
            "confidence": round(confidence, 2),
            "reasons": reasons,
            "risk_flags": risk_flags,
        }

    # ----- Rule 5: no keyword + non-tier-1 → ignore -----
    if not tier1:
        reasons.append("no_keyword_match")
        reasons.append("non_tier1_source")
        return {
            "decision": "ignore",
            "confidence": 0.70,
            "reasons": reasons,
            "risk_flags": risk_flags,
        }

    # ----- Rule 2 fallback: tier-1 with no keyword → safe briefing -----
    reasons.append("no_keyword_match")
    reasons.append("tier1_source_fallback")
    risk_flags.append("uncertain_classification")
    return {
        "decision": "briefing",
        "confidence": 0.50,
        "reasons": reasons,
        "risk_flags": risk_flags,
    }
