# GNI V1 Drafting Layer

Reads ingested headlines, classifies each for relevance, routes survivors to
an editorial template, generates a structured draft payload, validates it
with `EditorialValidator` (`gni/publisher/guards.py`), and persists drafts as
JSON. **Does not publish. Does not call any AI API.**

## Pipeline

```
headlines_YYYYMMDD_UTC.json
    │
    ▼
gni.classifier.classify_relevance        ← rule-based (gni/classifier/relevance.py)
    │
    ├── decision == "ignore"   → draft_status="ignored"   (no router, no guards)
    ├── decision == "alerta"   ─┐
    └── decision == "briefing" ─┴→ gni.editorial.router.route_content
                                     │
                                     ▼
                                 draft_builder (build_payload)
                                     │
                                     ▼
                                 gni.publisher.guards.EditorialValidator
                                     │
                                     ▼
                                drafts_YYYYMMDD.json
```

## File tree

```
gni/
├── drafting/
│   ├── __init__.py
│   ├── draft_builder.py      # routing + payload builders + validation wrapper
│   ├── run_drafting.py       # orchestrator (lock + atomic write + summary)
│   └── README.md             # this file
├── data/
│   ├── raw/headlines_YYYYMMDD_UTC.json   # input (from ingestion V1)
│   └── drafts/drafts_YYYYMMDD.json       # output (created on first run)
└── logs/
    └── drafting_YYYYMMDD.log              # rotating, 5 MB × 5
```

## Input

Reads `gni/data/raw/headlines_YYYYMMDD_UTC.json` (current UTC day). Falls
back to `headlines_YYYYMMDD.json` (no `_UTC`) and finally to the most recent
`headlines_*.json` if the current day's file is absent.

## Output

`gni/data/drafts/drafts_YYYYMMDD.json` — wrapper:

```json
{
  "schema_version": 1,
  "day_utc": "20260505",
  "last_updated_at": "2026-05-05T13:15:02Z",
  "count": 17,
  "drafts": [ /* draft records */ ]
}
```

Each draft record:

```json
{
  "draft_id": "draft_<hash_key>",
  "headline_hash_key": "<sha256>",
  "template": "ALERTA",
  "route_confidence": 0.80,
  "draft_status": "validated",
  "priority": "high",
  "payload": { /* template-specific */ },
  "guard_errors": [],
  "classifier_decision": "alerta",
  "classifier_confidence": 0.94,
  "classifier_reasons": ["urgency_hit:strike,missile", "tier1_source"],
  "classifier_risk_flags": [],
  "source_item": { /* the full headline */ },
  "created_at": "2026-05-05T13:15:02Z"
}
```

`draft_status` values:

| Status                    | Meaning                                                      |
|---------------------------|--------------------------------------------------------------|
| `validated`               | Auto-built (FLASH/ALERTA/RADAR) and editorial guards passed. |
| `failed_guard`            | Auto-built but editorial guards rejected; payload kept.      |
| `needs_review`            | Classifier OR router confidence below 0.65 — operator review.|
| `needs_editorial_build`   | Template is BRIEFING / FECHAMENTO; not built from one item.  |
| `ignored`                 | Classifier returned `ignore` — record saved, never routed.   |

## Classifier integration

`gni.classifier.classify_relevance` is called for **every** headline before
routing. Its decision drives the drafting branch:

| Classifier decision | Drafting behavior                                           |
|---------------------|-------------------------------------------------------------|
| `ignore`            | Save record with `draft_status="ignored"`. No router. No guards. No payload. |
| `alerta`            | Run router. If router falls through to `RADAR`, soft-override to `ALERTA`. Otherwise preserve router output (e.g. `FLASH`). |
| `briefing`          | Run router. No template override (router may pick any of FLASH / ALERTA / RADAR; BRIEFING falls through to `needs_editorial_build`). |

Independent of decision, **classifier confidence below 0.65** sets
`draft_status="needs_review"` (a candidate payload is still built when the
template is auto-buildable, so the operator has something to edit).

The four `classifier_*` fields are always present on every draft record,
including `ignored` ones. See `gni/classifier/README.md` for lexicons,
rule order, and confidence bands.

## Routing

`gni/editorial/router.py::route_content(text)` — heuristic V1:

| Trigger                                | Template | Confidence |
|----------------------------------------|----------|------------|
| Urgency keyword (war, breach, crash…)  | FLASH    | 0.90       |
| High-priority keyword (Fed, CPI, CVE…) | ALERTA   | 0.80       |
| Signal keyword (rally, plunge…)        | RADAR    | 0.70       |
| Fallback                               | RADAR    | 0.50       |

Routing text = `title + raw_text + category + source_name`. Confidence
< 0.65 → `needs_review`.

## Priority

| Rule                                                            | Priority   | Emoji |
|-----------------------------------------------------------------|------------|-------|
| Title contains urgency keyword                                  | `critical` | 🔴    |
| `category in {geopolitics, markets, cyber}` and `tier == tier1` | `high`     | 🟠    |
| Otherwise                                                       | `medium`   | 🟡    |
| (reserved)                                                      | `low`      | 🔵    |
| (reserved)                                                      | `info`     | 🟢    |

## Manual run

```bash
cd /Users/lucascabral/Desktop/projects/backup_lucas/GNI
python3 -m gni.drafting.run_drafting
```

A JSON run-summary is printed to stdout on success.

## Cron command

Run 2 minutes after each ingestion cycle (every 15 min, +2 min offset):

```cron
2,17,32,47 * * * * cd /Users/lucascabral/Desktop/projects/backup_lucas/GNI && /usr/bin/env python3 -m gni.drafting.run_drafting >> gni/logs/drafting_cron.out 2>&1
```

Install with:

```bash
( crontab -l 2>/dev/null; echo '2,17,32,47 * * * * cd /Users/lucascabral/Desktop/projects/backup_lucas/GNI && /usr/bin/env python3 -m gni.drafting.run_drafting >> gni/logs/drafting_cron.out 2>&1' ) | crontab -
```

## Write safety

Same pattern as ingestion V1:

- `fcntl.flock(LOCK_EX | LOCK_NB)` on `gni/drafting/.lock` → no overlap
- `tempfile.mkstemp` → `f.flush()` + `os.fsync()` → `os.replace()` → atomic
- Existing drafts read inside try/except; corrupt file logs and is overwritten

## Run summary fields

The orchestrator emits a JSON line and a `run done …` log line containing:

| Field | Meaning |
|---|---|
| `items_read`                  | total headlines pulled from the headlines file |
| `items_classified`            | headlines that reached `build_draft()` (i.e. non-duplicate, valid hash_key) |
| `items_ignored`                | classifier `decision == "ignore"` |
| `items_alerta`                | classifier `decision == "alerta"` |
| `items_briefing`              | classifier `decision == "briefing"` |
| `low_confidence_count`        | items with `classifier_confidence < 0.65` |
| `drafts_created`              | new draft records written this run |
| `drafts_validated`            | `draft_status == "validated"` |
| `drafts_failed_guard`         | `draft_status == "failed_guard"` |
| `drafts_needs_review`         | `draft_status == "needs_review"` |
| `drafts_needs_editorial_build`| `draft_status == "needs_editorial_build"` |
| `drafts_ignored`              | `draft_status == "ignored"` (parallel to `items_ignored`) |
| `duplicate_drafts`            | headlines skipped because already in the day-file |

## Validation checklist

- [ ] `python3 -m gni.drafting.run_drafting` exits `0` after at least one ingestion run
- [ ] `gni/data/drafts/drafts_<DAY>.json` is valid JSON with `count >= 1`
- [ ] Every draft has `draft_id`, `headline_hash_key`, `template`, `route_confidence`, `draft_status`, `priority`, `payload`, `guard_errors`, `classifier_decision`, `classifier_confidence`, `classifier_reasons`, `classifier_risk_flags`, `source_item`, `created_at`
- [ ] `draft_status` is one of: `validated`, `failed_guard`, `needs_review`, `needs_editorial_build`, `ignored`
- [ ] Every draft carries the four `classifier_*` fields, including `ignored` ones
- [ ] `ignored` drafts have `payload == {}` and `guard_errors == []` (router and guards skipped)
- [ ] BRIEFING / FECHAMENTO drafts have `draft_status == "needs_editorial_build"` and empty `payload`
- [ ] Failed-guard drafts retain `payload` and carry `guard_errors` (list of `{code, field, match}`)
- [ ] Second run on same headlines → `duplicate_drafts > 0`, draft count unchanged
- [ ] Concurrent run → second exits `1` with log `another drafting run holds the lock`
- [ ] Counters reconcile: `drafts_created == validated + failed_guard + needs_review + needs_editorial_build + ignored`
- [ ] Counters reconcile: `items_classified == items_ignored + items_alerta + items_briefing`
- [ ] No Telegram call, no AI API call (pure local IO)
- [ ] Log file `gni/logs/drafting_<DAY>.log` written and rotates at 5 MB

## Out of scope (V1)

- Telegram / WhatsApp publishing (V2)
- AI / LLM payload synthesis (V2)
- Multi-headline BRIEFING / FECHAMENTO assembly (manual desk job)
- Cross-day draft dedup (intra-day only)
