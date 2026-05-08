# gni.classifier — Relevance Classifier V1

Pre-router signal/noise filter. Pure Python, rule-based, deterministic, **no
external API**. Intended insertion point: after ingestion normalization,
before the editorial router.

## Public API

```python
from gni.classifier import classify_relevance

result = classify_relevance({
    "headline":     "Russia missile strike hits Ukraine power grid",
    "source_name":  "Reuters",
    "category":     "geopolitics",
    "tier":         "tier1",
    "published_at": "2026-05-05T12:00:00Z",
})
# →
# {
#   "decision":   "alerta",
#   "confidence": 0.94,
#   "reasons":    ["urgency_hit:strike,missile", "tier1_source"],
#   "risk_flags": []
# }
```

## Output schema

| Field | Type | Notes |
|---|---|---|
| `decision`    | `str`        | one of `"ignore"`, `"alerta"`, `"briefing"` |
| `confidence`  | `float`      | `[0.0, 1.0]`, rounded to 2 decimals |
| `reasons`     | `list[str]`  | ordered rule tags (`urgency_hit:…`, `tier1_source`, `low_value:…`) |
| `risk_flags`  | `list[str]`  | operator caveats (`urgency_on_non_tier1`, `conflicting_low_value:…`, `empty_headline`, `uncertain_classification`) |

## Decision rules (priority order)

| # | Condition | Decision | Confidence band |
|---|---|---|---|
| 0 | empty / non-text headline | `briefing` | 0.30 + `empty_headline` flag |
| 1 | urgency keyword + tier-1  | `alerta`   | 0.90–0.99 |
| 2 | urgency keyword + non-tier-1 | `alerta` | 0.75–0.85 + `urgency_on_non_tier1` flag |
| 3 | scheduled-summary pattern | `briefing` | 0.80–0.90 |
| 4 | low-value + no impact     | `ignore`   | 0.80–0.90 |
| 5 | impact keyword + tier-1   | `alerta`   | 0.70–0.85 |
| 6 | impact keyword + non-tier-1 | `briefing` | 0.60–0.75 |
| 7 | no keyword + non-tier-1   | `ignore`   | 0.70 |
| 8 | no keyword + tier-1       | `briefing` (safe) | 0.50 + `uncertain_classification` |

## Spec rule coverage

| Spec rule | Implemented by | Branch |
|---|---|---|
| 1. Never silently ignore high-impact keywords | urgency keyword always → `alerta` | rules 1, 2 |
| 2. If uncertain, choose `briefing`, not `ignore` | tier-1 + no keyword → `briefing`; empty → `briefing` | rules 0, 8 |
| 3. Tier-1 + urgency → `alerta`                  | direct branch | rule 1 |
| 4. Tier-1 + impact → `alerta` or `briefing`     | tier-1 → `alerta`; non-tier-1 → `briefing` | rules 5, 6 |
| 5. Low-tier + no impact → `ignore`              | direct branch | rule 7 |
| 6. Low-value headlines → `ignore`               | low-value pattern + no impact keyword | rule 4 |
| 7. Scheduled macro summaries → `briefing`       | scheduled-summary pattern | rule 3 |

**Note on rule 6 (duplicate detection):** A single-headline classifier cannot
detect duplicates. Cross-day duplicate suppression remains the responsibility
of `gni/ingestion/dedup.py` (7-day state). This module handles the
*low-value* half of the rule; the *duplicate* half belongs upstream.

## Lexicons

Defined in `relevance.py` as frozen tuples; matched against the lowercased
headline using Unicode-aware non-word boundaries.

| Group | Count | Languages |
|---|---|---|
| `URGENCY`           | 20 | EN + PT-BR |
| `MARKET_IMPACT`     | 19 | EN + PT-BR |
| `GEOPOLITICS`       | 14 | EN + PT-BR |
| `CYBER`             |  9 | EN + PT-BR |
| `AI_DOMAIN`         | 13 | EN + PT-BR |
| `SCHEDULED_SUMMARY` | 16 | EN + PT-BR |
| `LOW_VALUE`         | 13 | EN + PT-BR |

`TIER1_VALUES = {"tier1", "tier-1", "1", "t1"}` (case-insensitive).

## Determinism guarantees

- Lexicons are tuples — iteration order is fixed.
- All regex patterns are compiled once at module import.
- `_hits()` returns matches in lexicon order, deduped.
- Confidence is computed from integer hit-counts via stable arithmetic and
  rounded to 2 decimals.
- Output dict key order is the same on every call.
- No clocks, no randomness, no I/O.

Same input ⇒ byte-identical output, run-to-run.

## Validation checklist

- [x] Pure function — no I/O, no global state mutation, no network.
- [x] Deterministic — pre-compiled regex tables, sorted lexicons, no clocks.
- [x] No external API / no model load.
- [x] No critical keyword silently ignored — urgency hits always produce
      `alerta`, including on non-tier-1 sources (with `urgency_on_non_tier1`
      risk flag).
- [x] `confidence` always present and in `[0.0, 1.0]`.
- [x] `reasons` always present; lists the rule tags that fired.
- [x] Safe fallback to `briefing` when uncertain (rules 0 and 8).
- [x] Unit-testable: all paths reachable with synthetic input dicts.
- [x] Tolerates missing / non-string fields (`headline=None`, missing `tier`).
- [x] Handles EN and PT-BR side by side in a single lexicon table.

## Known limitations (operator should be aware)

- Two-letter geopolitics tokens (`us`, `eua`) can produce false positives on
  unrelated EN/PT pronoun usage. Operator review of the `briefing` queue
  catches these; `alerta` requires tier-1 + impact match, reducing FP risk.
- AI-domain `model` and `chip` are broad; non-AI usage (e.g., "fashion
  model", "potato chip") may match. Acceptable for a briefing-class
  classifier; tier-1 + `model` → `alerta` is rare in practice.
- The classifier reads only the headline string. Body / `raw_text` is not
  inspected in V1. A noisy-title-but-strong-body item may be ignored.
- Duplicate detection is **not** in scope for this classifier; rely on
  `gni/ingestion/dedup.py` (intra-day + 7-day cross-day state).
