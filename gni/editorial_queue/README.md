# GNI V1 Editorial Queue

Reads the current-day drafts JSON, materializes editorial-queue items per V1
routing rules, deduplicates against the existing queue, and persists the
merged queue. **Does not publish. Does not call any AI API.**

## File tree

```
gni/
├── editorial_queue/
│   ├── __init__.py
│   ├── queue_manager.py     # load_drafts, build_queue_items, update_status,
│   │                        # save_queue, summarize_queue
│   ├── run_queue.py         # orchestrator (lock + atomic write + summary)
│   └── README.md            # this file
├── data/
│   ├── drafts/drafts_YYYYMMDD.json   # input (from drafting V1)
│   └── queue/queue_YYYYMMDD.json     # output (created on first run)
└── logs/
    └── queue_YYYYMMDD.log             # rotating, 5 MB × 5
```

## Statuses

| Status                    | Meaning                                                        |
|---------------------------|----------------------------------------------------------------|
| `validated`               | Carried in from drafts (rare — most flow elsewhere on entry).  |
| `needs_review`            | Operator must approve / reject / edit.                         |
| `failed_guard`            | Editorial guards rejected the payload; preserved for review.   |
| `needs_editorial_build`   | BRIEFING / FECHAMENTO; needs human assembly.                   |
| `approved`                | Operator approved; awaits move to `ready_to_publish`.          |
| `rejected`                | Operator rejected (terminal).                                  |
| `ready_to_publish`        | Cleared for the publisher (V2).                                |
| `published`               | Set by publisher V2 (terminal).                                |

## V1 routing rules (`map_draft_status_to_queue_status`)

| Draft status              | Priority                  | Queue status            |
|---------------------------|---------------------------|-------------------------|
| `validated`               | `medium` / `low` / `info` | `ready_to_publish`      |
| `validated`               | `critical` / `high`       | `needs_review`          |
| `failed_guard`            | (any)                     | `failed_guard`          |
| `needs_editorial_build`   | (any)                     | `needs_editorial_build` |
| `needs_review`            | (any)                     | `needs_review`          |
| (unknown)                 | (any)                     | `needs_review` (defensive) |

## Queue item shape

```json
{
  "queue_id": "q_<draft_id>",
  "draft_id": "draft_<hash_key>",
  "headline_hash_key": "<sha256>",
  "template": "ALERTA",
  "priority": "high",
  "status": "needs_review",
  "payload": { /* template-specific */ },
  "source_item": { /* full headline */ },
  "created_at": "2026-05-05T13:15:02Z",
  "updated_at": "2026-05-05T13:15:02Z",
  "review_notes": ""
}
```

## Public functions

```python
from gni.editorial_queue import queue_manager

queue_manager.load_drafts(path)                  # -> list[dict]
queue_manager.load_queue(path)                   # -> list[dict]
queue_manager.build_queue_items(drafts, existing_draft_ids)  # -> (items, dups)
queue_manager.update_status(item, new_status, note=None)     # -> item (mutates)
queue_manager.save_queue(path, items, day_utc=None)          # atomic write
queue_manager.summarize_queue(items)             # -> {status: count, ...}
```

`update_status` rejects:
- unknown statuses (raises `ValueError`)
- transitions out of terminal `published` / `rejected` (raises `ValueError`)

## Manual run

```bash
cd /Users/lucascabral/Desktop/projects/backup_lucas/GNI
source .venv/bin/activate
python -m gni.editorial_queue.run_queue
```

A JSON run-summary is printed to stdout on success.

## Cron command (chained after drafting, +2 min)

```cron
4,19,34,49 * * * * cd /Users/lucascabral/Desktop/projects/backup_lucas/GNI && /Users/lucascabral/Desktop/projects/backup_lucas/GNI/.venv/bin/python -m gni.editorial_queue.run_queue >> gni/logs/queue_cron.out 2>&1
```

Pipeline timing (offsets from the hour):
- `:00 :15 :30 :45` → ingestion
- `:02 :17 :32 :47` → drafting
- `:04 :19 :34 :49` → editorial queue

## Validation checklist

- [ ] `python -m gni.editorial_queue.run_queue` exits `0` after at least one drafting run
- [ ] `gni/data/queue/queue_<DAY>.json` is valid JSON with `count >= 1`
- [ ] Every queue item carries: `queue_id`, `draft_id`, `headline_hash_key`, `template`, `priority`, `status`, `payload`, `source_item`, `created_at`, `updated_at`, `review_notes`
- [ ] `status` is one of the 8 ALLOWED_STATUSES
- [ ] `validated` + `priority in {medium, low, info}` → `ready_to_publish`
- [ ] `validated` + `priority == "critical"` → `needs_review` (no auto-publish)
- [ ] `validated` + `priority == "high"` → `needs_review` (no auto-publish)
- [ ] `failed_guard` draft → `failed_guard` queue item, payload preserved
- [ ] `needs_editorial_build` draft → `needs_editorial_build` queue item
- [ ] Second run on same drafts file → `queue_duplicates > 0`, queue file unchanged in size
- [ ] Concurrent run → second exits `1` with log `another queue run holds the lock`
- [ ] `update_status(item, "published")` succeeds; subsequent transition raises `ValueError`
- [ ] `update_status(item, "rejected")` succeeds; subsequent transition raises `ValueError`
- [ ] `update_status(item, "bogus")` raises `ValueError`
- [ ] No Telegram, no AI API call (network silence verifiable)

## Human approval gate (CLI)

`approve.py` is a local CLI for operator approval. No web panel, no
database — it edits the same `queue_YYYYMMDD.json` file under the same
`gni/editorial_queue/.lock` used by `run_queue.py`, so it cannot race the
periodic queue rebuild.

### Commands

```bash
# 1. List items needing review (today's queue by default)
python -m gni.editorial_queue.approve list

# 2. Approve a queue item
python -m gni.editorial_queue.approve approve \
  --queue-id q_draft_<hash> \
  --operator "lucas"

# 3. Reject a queue item (reason required)
python -m gni.editorial_queue.approve reject \
  --queue-id q_draft_<hash> \
  --operator "lucas" \
  --reason "off-scope, duplicate of q_draft_xyz"
```

All three accept `--day YYYYMMDD` to target a non-current-day queue file.

### Effects of `approve`

```json
{
  "status": "ready_to_publish",
  "approved_by": "<operator>",
  "approved_at": "2026-05-05T13:21:02Z",
  "manual_approval": true,            // only set when priority == "critical"
  "review_notes": "...\napproved_by=<operator> at=<ts>",
  "updated_at": "2026-05-05T13:21:02Z"
}
```

`manual_approval` is the flag the publisher (`gni/publisher/run_publish.py`)
requires before sending a `priority == "critical"` item. The CLI sets it
automatically on critical approvals; operator does not need to do it
manually.

### Effects of `reject`

```json
{
  "status": "rejected",
  "rejected_by": "<operator>",
  "rejected_at": "2026-05-05T13:21:02Z",
  "rejection_reason": "<reason>",
  "review_notes": "...\nrejected_by=<operator> at=<ts> reason=<reason>",
  "updated_at": "2026-05-05T13:21:02Z"
}
```

`rejected` is a terminal status — `update_status` will refuse any further
transition, so a rejected item can never publish.

### Audit trail

Every approve/reject appends one event to
`gni/data/queue/approvals_YYYYMMDD.json`:

```json
[
  {
    "event": "approve",
    "queue_id": "q_draft_<hash>",
    "operator": "lucas",
    "at": "2026-05-05T13:21:02Z",
    "prev_status": "needs_review",
    "new_status": "ready_to_publish",
    "priority": "high",
    "manual_approval": false
  },
  {
    "event": "reject",
    "queue_id": "q_draft_<other>",
    "operator": "lucas",
    "at": "2026-05-05T13:25:00Z",
    "prev_status": "needs_review",
    "new_status": "rejected",
    "priority": "critical",
    "reason": "off-scope, duplicate of q_draft_xyz"
  }
]
```

### Approval rules (cross-reference)

| Priority   | Default queue status | Publisher behavior without approval | After `approve`     |
|------------|----------------------|-------------------------------------|---------------------|
| `critical` | `needs_review`       | blocked (`blocked_critical`)        | `ready_to_publish` + `manual_approval=true` |
| `high`     | `needs_review`       | blocked by `status` filter          | `ready_to_publish` |
| `medium`   | `ready_to_publish`   | sent on next publish run            | (no-op; already ready) |
| `low`      | `ready_to_publish`   | sent on next publish run            | (no-op) |
| `info`     | `ready_to_publish`   | sent on next publish run            | (no-op) |

### CLI exit codes

- `0` — success
- `1` — queue file / queue_id not found, or another process holds the lock
- `2` — invalid arguments or illegal transition (e.g. approving an already-`rejected` item)

### CLI validation checklist

- [ ] `python -m gni.editorial_queue.approve list` shows all `needs_review` items with priority + template + title preview
- [ ] `* = approval required` marker appears next to `high` / `critical` rows
- [ ] `approve --queue-id <id> --operator lucas` flips `needs_review` → `ready_to_publish`, sets `approved_by` + `approved_at`
- [ ] Approving a `critical` item sets `manual_approval = true` (publisher gate)
- [ ] `reject --queue-id <id> --operator lucas --reason "..."` flips status → `rejected` and stores `rejection_reason`
- [ ] Rejected item is terminal — second approve/reject returns exit `2` with `illegal transition`
- [ ] Missing `--reason` on `reject` returns exit `2`
- [ ] Empty `--operator` returns exit `2`
- [ ] Unknown `--queue-id` returns exit `1`
- [ ] `gni/data/queue/approvals_<DAY>.json` gains one event per CLI invocation
- [ ] Concurrent CLI vs cron `run_queue` — second to acquire the lock waits / fails cleanly; queue file remains valid JSON
- [ ] After approve, next `python -m gni.publisher.run_publish` actually sends the item

## Out of scope (V1)

- Publisher (handled in `gni/publisher/`, separate module)
- AI / LLM payload editing (V2)
- Web UI / approval dashboard (V2 — CLI is the V1 contract)
- Cross-day queue carry-over (intra-day file only)
