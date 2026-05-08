# GNI V1 Ingestion Pipeline

RSS-only, cron-driven, JSON output. One process per run. Filesystem is the
only state store (no Redis/DB in V1).

## File tree

```
gni/
├── ingestion/
│   ├── __init__.py
│   ├── sources.json          # source list (tier/category/enabled)
│   ├── collector.py          # fetch + per-source isolation
│   ├── normalizer.py         # canonical schema + SHA-256 hash_key
│   ├── dedup.py              # intra-day + cross-day (state) dedup
│   ├── run_ingestion.py      # orchestrator (lock + atomic write + manifest)
│   └── README.md             # this file
├── data/raw/                 # outputs (created on first run)
│   ├── headlines_YYYYMMDD_UTC.json
│   ├── dlq_YYYYMMDD_UTC.json
│   └── manifest_YYYYMMDD_UTC.json
├── data/state/
│   └── seen_hashes.json      # 7-day cross-day dedup state + zero-streak counters
└── logs/
    └── ingestion_YYYYMMDD_UTC.log
```

## Dependencies

- Python ≥ 3.10
- `feedparser` (RSS parsing)
- stdlib only otherwise (`urllib`, `fcntl`, `tempfile`, `hashlib`, `json`,
  `logging.handlers`)

## Setup (one-time, production)

```bash
# 1. clone / cd to repo root
cd /Users/lucascabral/Desktop/projects/backup_lucas/GNI

# 2. create venv
python3 -m venv .venv

# 3. activate + install
source .venv/bin/activate
pip install --upgrade pip
pip install feedparser
# (or, if requirements.txt is canonical for this repo:)
# pip install -r requirements.txt

# 4. smoke test
python -m gni.ingestion.run_ingestion
```

## Cron command (using the venv interpreter)

Pin the cron job to the venv `python` so `feedparser` is always resolvable —
do not rely on system `python3`:

```cron
*/15 * * * * cd /Users/lucascabral/Desktop/projects/backup_lucas/GNI && /Users/lucascabral/Desktop/projects/backup_lucas/GNI/.venv/bin/python -m gni.ingestion.run_ingestion >> gni/logs/cron.out 2>&1
```

Install with:

```bash
( crontab -l 2>/dev/null; echo '*/15 * * * * cd /Users/lucascabral/Desktop/projects/backup_lucas/GNI && /Users/lucascabral/Desktop/projects/backup_lucas/GNI/.venv/bin/python -m gni.ingestion.run_ingestion >> gni/logs/cron.out 2>&1' ) | crontab -
```

## Manual run

```bash
cd /Users/lucascabral/Desktop/projects/backup_lucas/GNI
source .venv/bin/activate
python -m gni.ingestion.run_ingestion
```

Exit codes:

- `0` — run completed (per-source failures are isolated, not fatal)
- `1` — lock held by another run, or hard failure

## Output schema (canonical headline)

```json
{
  "id": "<sha256>",
  "schema_version": 1,
  "source_name": "Reuters Markets",
  "source_type": "rss",
  "category": "macro",
  "tier": "tier1",
  "title": "...",
  "url": "https://...",
  "url_synthetic": false,
  "published_at": "2026-05-05T13:14:00Z",
  "collected_at": "2026-05-05T13:15:02Z",
  "raw_text": "...",
  "hash_key": "<sha256>"
}
```

`hash_key = SHA-256(source_name | canonical_url | normalized_title)`.
URL canonicalization strips `utm_*`, `fbclid`, `gclid`, `mc_cid`, `mc_eid`,
`ref`, `ref_src`, `igshid`, lower-cases scheme/host, removes trailing
slashes, sorts query.

### URL fallback

When the upstream feed entry has no `<link>`, the normalizer synthesizes a
deterministic pseudo URL:

```
source://{source_name_no_spaces}/{sha256(normalized_title)}
```

The item still validates and gets a stable `hash_key`. `url_synthetic = true`
flags it for downstream consumers.

### `published_at` sanity

- missing / unparseable                          → falls back to `collected_at`
- year < 2000                                    → falls back to `collected_at`
- more than 1 h in the future of `collected_at`  → clamped to `collected_at`
- otherwise                                      → kept as parsed UTC ISO Z

### Cross-day dedup state

`gni/data/state/seen_hashes.json` retains every ingested
`{hash_key, url, first_seen}` for 7 days. The orchestrator pre-seeds the
dedup sets from this state, so an item from `23:58 UTC` cannot reappear at
`00:02 UTC` the next day. Pruned on every run; written atomically.

### Silent-feed detection

If a source returns HTTP 200 but `0` entries for ≥ 3 consecutive runs, the
orchestrator emits a `source_warning` log line and lists the source in the
manifest field `silent_feed_warnings`. Per-source `zero_streak` counter is
persisted in `seen_hashes.json` and reset to `0` on any non-empty run.

## P0 mitigations (reference)

| ID     | Risk                          | Mitigation                                             |
|--------|-------------------------------|--------------------------------------------------------|
| P0-01  | Cron overlap → JSON corrupt   | `fcntl.flock` in `run_ingestion.py` (`.lock` file)     |
| P0-02  | One slow source traps run     | Per-source `try/except` + 10s timeout in `collector`   |
| P0-03  | Malformed item reaches output | `REQUIRED_FIELDS` check + DLQ in `normalizer`          |
| P0-04  | Append-rewrite race           | `tempfile + os.replace` atomic write                   |
| P0-05  | Cross-day duplicate           | `gni/data/state/seen_hashes.json` (7-day retention)    |

## Validation checklist

- [ ] `python3 -m gni.ingestion.run_ingestion` exits 0 on first run
- [ ] `gni/data/raw/headlines_<DAY>_UTC.json` exists, valid JSON, non-empty `items`
- [ ] `gni/data/raw/manifest_<DAY>_UTC.json` exists, `sources_ok >= 1`
- [ ] Every item has `source_name`, `title`, `url`, `collected_at`, `hash_key`
- [ ] `published_at` is ISO 8601 UTC (`Z` suffix) when present
- [ ] Second run within same minute → no duplicate items in day-file (`items_duplicates > 0`)
- [ ] Concurrent run → second invocation logs "another ingestion run holds the lock"; exits 1
- [ ] Disabled / placeholder URL sources skipped (visible in manifest)
- [ ] Failed source isolated: other sources still produce items
- [ ] `dlq_<DAY>_UTC.json` only created when ≥1 item rejected; rejected entries carry `reason`
- [ ] `gni/logs/ingestion_<DAY>_UTC.log` rotates at 5 MB (5 backups)
- [ ] Item without `<link>` is kept (not DLQ'd); has `url_synthetic: true` and a `source://...` URL
- [ ] `gni/data/state/seen_hashes.json` exists after first run; entries older than 7 days are pruned
- [ ] Cross-day duplicate (same hash_key submitted next day) suppressed via state; `items_duplicates > 0`
- [ ] Future / pre-2000 `published_at` clamped to `collected_at`
- [ ] Source returning 0 entries 3 runs in a row → `silent_feed_warnings` lists source name; log carries `source_warning`

## Known V1 limitations (deferred to V2)

- Telegram / X / API adapters (RSS only in V1)
- Priority scoring / source corroboration
- DB persistence (filesystem JSON only)
- HTTP 429 rate-limit handling beyond the single retry in `fetch_rss`
