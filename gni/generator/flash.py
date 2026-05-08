"""FLASH content generator (V1).

Deterministic, sub-millisecond, no-hallucination 1-line FLASH builder.

Input:
    {
        "headline":    "<canonical headline title>",
        "category":    "<feed category, e.g. markets, geopolitics, cyber>",
        "source_name": "<feed display name>",
        "tier":        "<tier1 | tier2 | tier3>",
        "url":         "<canonical url; may be empty>"
    }

Output:
    {"template": "FLASH", "text": "<emoji> <subject> — <event> — <impact>"}

Contract:
    - Total ``text`` length <= 200 chars.
    - Single sentence (no internal periods).
    - Structure: ``<emoji> <subject> — <event> — <impact>`` when a subject can
      be extracted, or the 2-segment fallback ``<emoji> <headline> — <impact>``
      when the headline is opaque.
    - The emoji is one of {🔴, 🟠, 🟡} — all members of the priority emoji
      whitelist enforced by ``gni.publisher.guards.EditorialValidator``
      (``forbidden_lexicon.json::emoji_whitelist.priority``).
    - The impact segment is a deterministic *category tag*, never an invented
      fact (e.g. ``markets`` → ``"market reaction"``).
    - ALL-CAPS words outside the acronym whitelist are down-cased.
    - On any forbidden-lexicon hit (opinion / hype / speculation / promo /
      filler / first-second person / urgency adjectives / clickbait), the
      builder collapses to the safest fallback: ``<emoji> <headline> — <impact>``.

Design notes:
    Pure stdlib + ``re``. No LLM, no network, no I/O. Safe to call inline
    from ``gni.drafting.draft_builder.build_flash_payload``.
"""
from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Constraints (mirror ``gni/templates/forbidden_lexicon.json``)
# ---------------------------------------------------------------------------

MAX_TEXT_CHARS = 200
SEPARATOR = " — "

PRIORITY_EMOJI_CRITICAL = "🔴"
PRIORITY_EMOJI_HIGH = "🟠"
PRIORITY_EMOJI_MEDIUM = "🟡"

# Acronyms allowed to remain in ALL-CAPS form. Source-of-truth lives in
# ``gni/templates/forbidden_lexicon.json::all_caps_word.acronym_whitelist``;
# duplicated here to avoid an I/O dependency on the lexicon at draft-build
# time. The publisher guard layer re-validates against the lexicon.
ACRONYM_WHITELIST: frozenset[str] = frozenset({
    "EUA", "ONU", "OTAN", "OMC", "OMS", "FMI", "BCE", "BCB", "FED", "BOJ",
    "BOE", "OPEP", "BRICS", "G7", "G20", "UE", "PIB", "IPCA", "IGP-M", "CDI",
    "CDS", "ETF", "VIX", "GDP", "CPI", "ISM", "PMI", "FX", "USD", "EUR", "BRL",
    "JPY", "CNY", "GBP", "CVE", "RCE", "DDoS", "APT", "C2", "OSINT", "SOC",
    "MITRE", "OSPF", "BGP", "TLS", "IAM", "AWS", "GCP", "MFA", "IDS", "IPS",
    "API", "SDK", "LLM", "RAG", "GPU", "CPU", "ASIC", "TPU", "ChatGPT",
    "OpenAI", "MSFT", "AAPL", "NVDA", "META", "TSLA",
    # Common geopolitical short codes used in EN-language feeds.
    "US", "EU", "UK", "UN", "NATO", "OPEC", "IMF", "WHO", "WTO",
})

# Category → category-impact tag. These are *labels*, not invented facts:
# they map a known feed category to a high-level domain of impact.
_CATEGORY_IMPACT_TAGS: dict[str, str] = {
    "markets":     "market reaction",
    "macro":       "macro impact",
    "crypto":      "crypto market reaction",
    "geopolitics": "geopolitical implications",
    "cyber":       "security implications",
    "ai":          "AI sector implications",
    "tech":        "tech sector implications",
    "energy":      "energy market reaction",
    "health":      "public-health implications",
    "social":      "public reaction",
}
DEFAULT_IMPACT_TAG = "watch for cross-asset reaction"

# Forbidden lexicon (subset relevant to a one-line FLASH ``text`` field).
# These mirror ``gni/templates/forbidden_lexicon.json`` rules tagged
# ``opinion``, ``speculation``, ``hype``, ``filler``, ``first_second_person``,
# ``promo_cta``, ``urgency_adjectives``, ``clickbait_headline``. The publisher
# guard layer is the source-of-truth and runs again before publish.
_FORBIDDEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    # opinion
    re.compile(
        r"\b(?:acreditamos|entendemos|nossa\s+vis[ãa]o|devemos|"
        r"vale\s+(?:a\s+pena|destacar|ressaltar|mencionar))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:é\s+importante\s+(?:notar|destacar|ressaltar)|"
        r"na\s+nossa\s+opini[ãa]o|interessante\s+notar)\b",
        re.IGNORECASE,
    ),
    # speculation
    re.compile(
        r"\b(?:poderia|poderiam|talvez|parece|sugere|aparentemente|"
        r"provavelmente|eventualmente|possivelmente)\b",
        re.IGNORECASE,
    ),
    # hype
    re.compile(
        r"\b(?:massivas?|sem\s+precedentes|crucial|hist[óo]rica|"
        r"chocantes?|dram[áa]tica|estrond[íi]osa|bomb[áa]stica)\b",
        re.IGNORECASE,
    ),
    # filler
    re.compile(
        r"\b(?:em\s+meio\s+a|como\s+sabido|obviamente|sem\s+d[úu]vida|"
        r"é\s+claro\s+que|vale\s+lembrar)\b",
        re.IGNORECASE,
    ),
    # first/second person
    re.compile(
        r"\b(?:eu|n[óo]s|voc[êe]|voc[êe]s|nossa|meu|tu|te)\b",
        re.IGNORECASE,
    ),
    # promo / CTA
    re.compile(
        r"\b(?:clique\s+(?:aqui|abaixo)|inscreva|compartilha|saiba\s+mais|"
        r"link\s+na\s+bio|siga\s+(?:canal|grupo))\b",
        re.IGNORECASE,
    ),
    # urgency adjectives in body (FLASH itself is urgent — but the body must
    # not editorialize with these words).
    re.compile(r"\b(?:urgente|cr[íi]tico|grave)\b", re.IGNORECASE),
    # multi-fact joiners (lighter than the lexicon's ``;`` rule, which is
    # too aggressive for a single-line transform; the publisher guard still
    # enforces the full set)
    re.compile(r"\be\s+tamb[ée]m\b", re.IGNORECASE),
    re.compile(r"\bal[ée]m\s+disso\b", re.IGNORECASE),
    # clickbait phrases
    re.compile(
        r"(?:voc[êe]\s+n[ãa]o\s+vai\s+acreditar|o\s+que\s+est[áa]\s+por\s+tr[áa]s|surpresa)",
        re.IGNORECASE,
    ),
)

_TRAILING_PUNCT_RE = re.compile(r"[\s\.\!\?…]+$")
_QUOTE_CHARS_RE = re.compile(r"[\"'…]")
_INTERNAL_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?]\s+")
# Leading proper-noun prefix: 1–3 Title-Cased OR ALL-CAPS tokens, followed by
# at least one space. Hyphens allowed inside a token (e.g. "Anglo-American").
_LEADING_SUBJECT_RE = re.compile(
    r"^("
    r"[A-Z][A-Za-zÁÉÍÓÚÂÊÔÃÕÇ\-]+"
    r"(?:\s+[A-Z][A-Za-zÁÉÍÓÚÂÊÔÃÕÇ\-]+){0,2}"
    r")\s+"
)
# ALL-CAPS word, length >= 5, used to enforce the acronym whitelist on the
# rendered ``text``. Mirrors ``forbidden_lexicon.json::all_caps_word.pattern``.
_ALLCAPS_WORD_RE = re.compile(
    r"\b[A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9\-]{4,}\b"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pick_emoji(category: str, tier: str) -> str:
    """Pick a priority-bucket emoji from category + tier.

    The full priority computation in ``gni.drafting.draft_builder.compute_priority``
    also inspects title keywords; the FLASH input schema does not pass priority,
    so this is a category/tier-only heuristic. All three emojis are members of
    ``forbidden_lexicon.json::emoji_whitelist.priority``.
    """
    cat = (category or "").strip().lower()
    t = (tier or "").strip().lower()
    if cat in {"geopolitics", "cyber"} and t == "tier1":
        return PRIORITY_EMOJI_CRITICAL
    if cat in {"markets", "macro", "crypto", "ai"} and t == "tier1":
        return PRIORITY_EMOJI_HIGH
    return PRIORITY_EMOJI_MEDIUM


def _impact_tag(category: str) -> str:
    return _CATEGORY_IMPACT_TAGS.get(
        (category or "").strip().lower(), DEFAULT_IMPACT_TAG
    )


def _strip_trailing_punct(s: str) -> str:
    return _TRAILING_PUNCT_RE.sub("", s or "").strip()


def _strip_quotes(s: str) -> str:
    return _QUOTE_CHARS_RE.sub("", s or "")


def _normalize_caps(text: str) -> str:
    """Down-case ALL-CAPS words that aren't in ``ACRONYM_WHITELIST``.

    Keeps acronyms verbatim; converts ``"BREAKING"`` → ``"Breaking"``.
    """
    def repl(m: re.Match[str]) -> str:
        word = m.group(0)
        if word in ACRONYM_WHITELIST:
            return word
        return word.capitalize()
    return _ALLCAPS_WORD_RE.sub(repl, text)


def _enforce_single_sentence(s: str) -> str:
    """Keep only the first sentence if the headline contains internal
    sentence boundaries (``. ``, ``! ``, ``? ``)."""
    if not s:
        return s
    parts = _INTERNAL_SENTENCE_BOUNDARY_RE.split(s, maxsplit=1)
    return parts[0].strip() if parts else s


def _split_subject_event(headline: str) -> tuple[str, str]:
    """Best-effort (subject, event) split.

    Tries, in order:
      1. ``" — "`` em-dash with spaces (already FLASH-shaped)
      2. ``": "`` colon (e.g. ``"Iran: oil prices spike"``)
      3. ``" - "`` hyphen with spaces
      4. early comma (within first 30 chars; e.g. ``"US, China escalate..."``)
      5. leading proper-noun prefix (1–3 Title-Cased / ALL-CAPS tokens) when
         the remainder has at least 3 words

    Returns ``("", headline)`` on no split, signaling caller to use the
    2-segment fallback.
    """
    h = (headline or "").strip()
    if not h:
        return "", ""
    # 1–3: explicit separators.
    for sep in (" — ", ": ", " - "):
        if sep in h:
            left, right = h.split(sep, 1)
            left = _strip_trailing_punct(left)
            right = _strip_trailing_punct(right)
            if left and right and len(right.split()) >= 2:
                return left, right
    # 4: early comma.
    if "," in h[:30]:
        left, right = h.split(",", 1)
        left = _strip_trailing_punct(left)
        right = _strip_trailing_punct(right)
        if left and right and len(right.split()) >= 2:
            return left, right
    # 5: leading proper-noun prefix.
    m = _LEADING_SUBJECT_RE.match(h)
    if m:
        subj = _strip_trailing_punct(m.group(1))
        rest = h[m.end():].strip()
        if subj and len(rest.split()) >= 3:
            return subj, _strip_trailing_punct(rest)
    return "", h


def _violates_lexicon(text: str) -> bool:
    return any(p.search(text) for p in _FORBIDDEN_PATTERNS)


def _truncate_to_max(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return _strip_trailing_punct(cut)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_flash(item: dict[str, Any]) -> dict[str, str]:
    """Build a FLASH payload from a normalized headline item.

    Returns ``{"template": "FLASH", "text": "..."}``. Always returns a payload;
    on degenerate input (empty headline) ``text`` is the empty string and the
    downstream guard layer will mark the draft ``failed_guard``.

    No exceptions are raised: this function is safe to call inline inside
    ``gni.drafting.run_drafting``'s per-headline isolation block.
    """
    headline = (item or {}).get("headline") or ""
    category = (item or {}).get("category") or ""
    tier = (item or {}).get("tier") or ""

    headline = _strip_quotes(headline)
    headline = _enforce_single_sentence(headline)
    headline = _strip_trailing_punct(headline)
    if not headline:
        return {"template": "FLASH", "text": ""}

    emoji = _pick_emoji(category, tier)
    impact = _impact_tag(category)

    subject, event = _split_subject_event(headline)
    if subject and event:
        text = f"{emoji} {subject}{SEPARATOR}{event}{SEPARATOR}{impact}"
    else:
        # Fallback: rewrite headline as a 2-segment FLASH.
        text = f"{emoji} {headline}{SEPARATOR}{impact}"

    text = _normalize_caps(text)

    # Hard safety: if the parsed event introduced any forbidden-lexicon hit,
    # collapse to the bare 2-segment fallback (still containing the original
    # headline, which is the safest faithful rephrase).
    if _violates_lexicon(text):
        safe_headline = _normalize_caps(headline)
        text = f"{emoji} {safe_headline}{SEPARATOR}{impact}"

    text = _truncate_to_max(text)
    return {"template": "FLASH", "text": _strip_trailing_punct(text)}
