# gni.observability — Minimal Observability V1

Local-only metrics layer for the GNI V1 pipeline. No dashboard, no remote
sink, no daemon. A single command reads the day's pipeline artefacts and
writes one JSON file plus a human-readable summary.

## Command

```bash
python -m gni.observability.run_report
python -m gni.observability.run_report --day 20260505
python -m gni.observability.run_report --root /path/to/repo --quiet
```

Exit code is `0` even when some inputs are missing — missing artefacts
degrade silently to zero counts, and the `inputs_seen` block in both the
JSON file and stdout shows which files were actually loaded.

## Inputs

All paths are resolved under the repo root for the given UTC day.

| Role | Path |
|---|---|
| Headlines (ingestion) | `gni/data/raw/headlines_<DAY>_UTC.json` |
| Ingestion manifest    | `gni/data/raw/manifest_<DAY>_UTC.json` |
| Drafts                | `gni/data/drafts/drafts_<DAY>.json` |
| Queue                 | `gni/data/queue/queue_<DAY>.json` |
| Published             | `gni/data/published/published_<DAY>.json` |
| Drafting log          | `gni/logs/drafting_<DAY>.log` |
| Queue log             | `gni/logs/queue_<DAY>.log` |

The manifest and log files are **supplementary** — used only to enrich
`duplicate_count`. Missing them does not affect any other metric.

## Output

Atomically written to:

```
gni/data/metrics/metrics_<DAY>.json
```

JSON shape:

```json
{
  "day_utc": "20260505",
  "generated_at": "2026-05-05T12:34:56Z",
  "schema_version": 1,
  "metrics": {
    "headlines_collected": 0,
    "drafts_created": 0,
    "drafts_validated": 0,
    "drafts_failed_guard": 0,
    "queue_ready_to_publish": 0,
    "queue_needs_review": 0,
    "published_count": 0,
    "failed_publish_count": 0,
    "duplicate_count": 0,
    "critical_pending_count": 0
  },
  "breakdown": {
    "drafts": { "created": 0, "validated": 0, "failed_guard": 0,
                "needs_review": 0, "needs_editorial_build": 0 },
    "queue":  { "total": 0,
                "by_status": { "validated": 0, "needs_review": 0,
                               "failed_guard": 0, "needs_editorial_build": 0,
                               "approved": 0, "rejected": 0,
                               "ready_to_publish": 0, "published": 0 },
                "critical_pending": 0, "failed_publish": 0 },
    "duplicates": { "ingestion_manifest": 0, "drafting_log": 0, "queue_log": 0 }
  },
  "inputs_seen": {
    "headlines": false, "drafts": false, "queue": false, "published": false,
    "manifest": false, "drafting_log": false, "queue_log": false
  }
}
```

## Metric definitions

| Metric | Source | Rule |
|---|---|---|
| `headlines_collected`     | headlines JSON | length of `items[]` |
| `drafts_created`          | drafts JSON    | length of `drafts[]` |
| `drafts_validated`        | drafts JSON    | items where `draft_status == "validated"` |
| `drafts_failed_guard`     | drafts JSON    | items where `draft_status == "failed_guard"` |
| `queue_ready_to_publish`  | queue JSON     | items where `status == "ready_to_publish"` |
| `queue_needs_review`      | queue JSON     | items where `status == "needs_review"` |
| `published_count`         | published JSON | length of `published[]` (or bare list) |
| `failed_publish_count`    | queue JSON     | `status == "ready_to_publish"` AND `"publish_failed" in review_notes` (currently stuck) |
| `critical_pending_count`  | queue JSON     | `status == "needs_review"` AND `priority == "critical"` |
| `duplicate_count`         | manifest + logs | sum of `manifest.items_duplicates` + last `duplicates=N` from drafting log + last `dups=N` from queue log |

## Acceptance checklist

- [x] Runs even if every input file is missing — produces a zero-filled
  report and `inputs_seen` of all-`false`.
- [x] Empty / corrupt JSON files do not crash the run; they are treated
  as missing.
- [x] Output is JSON, atomically written via `tempfile + os.replace`.
- [x] Counts match the source files (verified via the V1 E2E suite at
  `gni/tests/e2e/`).
- [x] Stdout summary is human-readable, table-aligned, and includes the
  `inputs_seen` block so a missing artefact is obvious.
- [x] Single command: `python -m gni.observability.run_report`.
- [x] No external services. No network egress. No third-party packages.

## Files

- `metrics.py` — pure extractors. No I/O beyond `Path.read_text`. All
  functions tolerant of `None` / missing / corrupt inputs.
- `run_report.py` — orchestrator: path resolution, atomic write, stdout
  rendering. The only side-effecting layer.
- `__init__.py` — module marker.
