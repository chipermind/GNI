# GNI V1 Telegram Publisher

Reads the current-day editorial queue, publishes ONLY items whose
`status == "ready_to_publish"` to Telegram, applies safe-mode rules, and
persists publication metadata. **No AI, no infinite retries, no secrets in
logs.**

## File tree

```
gni/
├── publisher/
│   ├── formatter.py             # payload -> Telegram-ready plain text
│   ├── telegram_publisher.py    # stdlib HTTP client + topic resolver
│   ├── run_publish.py           # orchestrator (lock + atomic + safe mode)
│   └── README.md                # this file
├── data/
│   ├── queue/queue_YYYYMMDD.json    # input (editorial queue V1)
│   └── published/
│       └── published_YYYYMMDD.json  # append-only audit log of successes
└── logs/
    └── publish_YYYYMMDD.log         # rotating, 5 MB × 5
```

> `gni/publisher/` also hosts pre-existing modules (`guards.py`, `send.py`,
> `splitter.py`, package `__init__.py`). The new V1 publisher does not touch
> them.

## Safe-mode rules

| Rule                                                             | Behavior                                  |
|------------------------------------------------------------------|-------------------------------------------|
| `status != "ready_to_publish"`                                   | skipped (not counted as failure)          |
| `priority == "critical"` and `manual_approval` not truthy        | skipped, counted as `blocked_critical`    |
| `failed_guard` / `needs_review` / `needs_editorial_build`        | never publishes (status filter)           |
| Empty `payload` or unsupported `template`                        | skipped, counted as `failed`              |
| Topic env var unset for routed category                          | skipped, counted as `topic_unset`         |
| Telegram API non-2xx after 3 attempts                            | item kept at `ready_to_publish`, error in `review_notes` |
| `TELEGRAM_DRY_RUN=1`                                             | format + log preview only, no HTTP call   |

Critical items only publish when an operator (or upstream tooling) sets
`manual_approval: true` on the queue item. By default critical items remain
in `needs_review` (set by the queue manager); even if forced to
`ready_to_publish`, the publisher refuses without `manual_approval`.

## Topic routing

```
priority in {critical, high}      -> TELEGRAM_TOPIC_ALERTS
category == "geopolitics"         -> TELEGRAM_TOPIC_GEOPOLITICS
category == "cyber"               -> TELEGRAM_TOPIC_CYBER
category == "ai"                  -> TELEGRAM_TOPIC_AI
category in {markets,macro,crypto}-> TELEGRAM_TOPIC_MARKETS
else                              -> TELEGRAM_TOPIC_COMMUNITY
```

If the resolved env var is unset, the item is **not** published and is left
at `ready_to_publish` with `topic_unset` recorded in `review_notes`.

## Environment variables

```bash
# Required (production)
export TELEGRAM_BOT_TOKEN="123456:AA..."        # NEVER logged
export TELEGRAM_CHAT_ID="-1001234567890"        # supergroup id

# Topic thread IDs (integer; one per category)
export TELEGRAM_TOPIC_ALERTS="2"
export TELEGRAM_TOPIC_GEOPOLITICS="3"
export TELEGRAM_TOPIC_CYBER="4"
export TELEGRAM_TOPIC_AI="5"
export TELEGRAM_TOPIC_MARKETS="6"
export TELEGRAM_TOPIC_COMMUNITY="7"

# Optional (V1 testing): format + log only, no HTTP call
export TELEGRAM_DRY_RUN="1"
```

A complete `.env.example` is at the repo root (or paste the block above).

## Manual run

```bash
cd /Users/lucascabral/Desktop/projects/backup_lucas/GNI
source .venv/bin/activate

# DRY-RUN first (no HTTP, no Telegram side effects):
TELEGRAM_DRY_RUN=1 python -m gni.publisher.run_publish

# Real publish (after env exported):
python -m gni.publisher.run_publish
```

A JSON run-summary is printed to stdout on success.

## Cron command (chained after editorial queue)

```cron
6,21,36,51 * * * * cd /Users/lucascabral/Desktop/projects/backup_lucas/GNI && /Users/lucascabral/Desktop/projects/backup_lucas/GNI/.venv/bin/python -m gni.publisher.run_publish >> gni/logs/publish_cron.out 2>&1
```

Pipeline cadence (every 15 min):
- `:00 :15 :30 :45` → ingestion
- `:02 :17 :32 :47` → drafting
- `:04 :19 :34 :49` → editorial queue
- `:06 :21 :36 :51` → publish

The cron environment must export `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
and the `TELEGRAM_TOPIC_*` vars (use a wrapper script or set them in the
crontab itself; do **not** commit them).

## Failure handling

- HTTP 429 (rate-limited): one retry honoring `retry_after` (≤ 30 s).
- HTTP 5xx: linear backoff, max 3 attempts total.
- HTTP 4xx: no retry — error preserved in `review_notes`.
- Network exception: caught, retried up to 3 attempts.
- After 3 failed attempts: item stays at `ready_to_publish`, the cron
  picks it up next cycle.

The error message stored in `review_notes` is sanitized — bot-token URLs
are redacted via `_redact_token_url` before logging.

## Output: `published_YYYYMMDD.json`

Append-only list of successful publishes:

```json
[
  {
    "queue_id": "q_draft_<hash>",
    "draft_id": "draft_<hash>",
    "headline_hash_key": "<sha256>",
    "template": "ALERTA",
    "priority": "high",
    "category": "markets",
    "topic_env": "TELEGRAM_TOPIC_ALERTS",
    "topic_id": "2",
    "message_id": 12345,
    "published_at": "2026-05-05T13:21:02Z",
    "text_preview": "<first 200 chars>"
  }
]
```

## Output: queue file in-place update

Each successfully published item gains:

```json
{
  "status": "published",
  "updated_at": "2026-05-05T13:21:02Z",
  "publication": {
    "message_id": 12345,
    "topic_env": "TELEGRAM_TOPIC_ALERTS",
    "topic_id": "2",
    "chat_id": "-1001234567890",
    "published_at": "2026-05-05T13:21:02Z",
    "http_status": 200,
    "attempts": 1
  }
}
```

## Validation checklist

- [ ] `TELEGRAM_DRY_RUN=1 python -m gni.publisher.run_publish` exits `0` and prints `dry_run > 0` for items that would publish
- [ ] No item with `status != "ready_to_publish"` is sent to Telegram (verify via dry-run summary `considered` vs `queue_total`)
- [ ] `priority == "critical"` without `manual_approval` is skipped; counter `blocked_critical > 0`
- [ ] `failed_guard` / `needs_review` / `needs_editorial_build` items are never sent
- [ ] Successful publish sets `status = "published"` and writes `publication.{message_id, topic_id, topic_env, chat_id, published_at, http_status, attempts}`
- [ ] `published_<DAY>.json` contains an entry per successful publish
- [ ] Failed publish keeps `status == "ready_to_publish"` and appends an entry to `review_notes`
- [ ] Logs contain no `TELEGRAM_BOT_TOKEN` value (grep `logs/publish_*.log` for `bot[0-9]` returns empty)
- [ ] Topic routing: priority high/critical → ALERTS env; category markets → MARKETS env; etc.
- [ ] Topic env var unset → item kept at `ready_to_publish`, counter `topic_unset > 0`
- [ ] Bounded retries: 3 attempts max per item; never an infinite loop
- [ ] Concurrent run blocked by lock; second exits `1` with log `another publish run holds the lock`
- [ ] Queue file remains valid JSON after the run (atomic write)
- [ ] Telegram disable_web_page_preview is enabled (no link-preview spam)

## Out of scope (V2)

- Inline keyboards, photos, polls, threaded replies
- Operator approval CLI / web UI
- Cross-day re-publish recovery (item that fails 3× across many cycles)
- Long-message splitter (currently hard-capped at 3500 chars)
- Markdown / HTML parse modes (V1 sends `text` plain)
