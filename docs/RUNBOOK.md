# GNI Bot Creator — Runbook

Operational procedures for deploy, pause/resume, DLQ handling, backup/restore, and common failures.

## Deploy

### First-time setup (VM)

```bash
# On VM
cd /opt/gni-bot-creator
cp .env.example .env
# Edit .env: POSTGRES_*, API_KEY, TELEGRAM_*, MAKE_WEBHOOK_URL, etc.

# Bootstrap everything (starts services, systemd, verification)
bash scripts/bootstrap_vm_all.sh
```

### Deploy from local machine

```bash
# Sync repo to VM, build, up
bash scripts/deploy_vm.sh
```

### Manual deploy

```bash
cd /opt/gni-bot-creator
docker compose up -d
docker compose ps   # verify all healthy
curl http://localhost:8000/health
```

### Enable monitoring (Prometheus)

```bash
docker compose --profile monitoring up -d
# Prometheus: http://localhost:9090 — scrapes API http://api:8000/metrics every 30s
```

**API endpoints:**
- **`/health`** and **`/health/ready`** — Readiness: DB, Redis, Ollama must be up. Returns **503** with `"status": "fail"` if any critical dependency is down.
- **`/health/live`** — Liveness only (always 200).
- **`/metrics`** — Prometheus format: `items_ingested_total`, `drafts_generated_total`, `publications_success_total`, `publications_failed_total`, `llm_latency_seconds`, `queue_depth{stage="new|scored|drafted"}`.

**Structured logging:** Set `LOG_JSON=1` for JSON log lines with `request_id`/`correlation_id` (from header or generated). When publishing, worker logs include `item_id`, `template`, `channel`.

---

## Pause / Resume

Pause stops new publications; ingest and pipeline continue (items queue up).

### Pause publishing

```bash
curl -X POST -H "X-API-Key: $API_KEY" http://localhost:8000/control/pause
```

### Resume publishing

```bash
curl -X POST -H "X-API-Key: $API_KEY" http://localhost:8000/control/resume
```

### Check status

```bash
curl -H "X-API-Key: $API_KEY" http://localhost:8000/control/status
# Returns: settings.pause_all_publish, stats, dependencies (db/redis/ollama), last_failures
```

---

## DLQ Handling

Items that fail after `MAX_PIPELINE_ATTEMPTS` (default 3) go to the Dead Letter Queue.

### List DLQ entries

```bash
curl -H "X-API-Key: $API_KEY" http://localhost:8000/dlq
```

### Retry (reset item, delete DLQ entry)

```bash
curl -X POST -H "X-API-Key: $API_KEY" http://localhost:8000/dlq/{dlq_id}/retry
# Item goes back to stage (drafted for publish failures; pipeline will retry)
```

### Drop (delete DLQ entry, mark item failed)

```bash
curl -X POST -H "X-API-Key: $API_KEY" http://localhost:8000/dlq/{dlq_id}/drop
```

---

## Backup / Restore

### Where backups live

- **Path on VM**: `/opt/gni-bot-creator/backups/` (bind mount `./backups` from repo root).
- **Filenames**: `gni_YYYYMMDD_HHMMSS.sql` (plain `pg_dump -Fp`).
- **Retention**: Last **N** dumps kept (env `BACKUP_RETENTION`, default **7**). Older files are deleted by the backup script.

Create the directory before first run: `mkdir -p /opt/gni-bot-creator/backups`.

### Backup Postgres (one-shot)

From repo root on the VM:

```bash
# Preferred: run the script (it starts the backup container)
/opt/gni-bot-creator/scripts/backup_postgres.sh
```

Or run the backup service directly:

```bash
cd /opt/gni-bot-creator
docker compose --profile backup run --rm backup
```

The script prints the backup path and size; retention is applied after each run.

### Cron (VM, daily at 02:30)

Add a cron job on the VM so backups run automatically:

```bash
# Edit crontab
crontab -e

# Add line (daily at 02:30, keep last 7 backups)
30 2 * * * /opt/gni-bot-creator/scripts/backup_postgres.sh
```

Optional: override retention with env, e.g. `BACKUP_RETENTION=14` in the cron line or in `.env` (the backup service reads `BACKUP_RETENTION`).

### Restore from dump

1. **Choose a backup file** (e.g. list them):

   ```bash
   ls -la /opt/gni-bot-creator/backups/
   ```

2. **Stop the stack** (so nothing writes to DB during restore):

   ```bash
   cd /opt/gni-bot-creator
   docker compose down
   ```

3. **Start only Postgres**, then restore:

   ```bash
   docker compose up -d postgres
   sleep 5

   # Replace with your backup filename
   docker compose exec postgres psql -U gni -d gni -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
   docker compose exec -T postgres psql -U gni -d gni < ./backups/gni_20250130_020000.sql
   ```

4. **Bring the rest of the stack up**:

   ```bash
   docker compose up -d
   ```

---

## Common Failures

### Ollama down

**Symptom**: Worker logs LLM errors; `GET /control/status` shows `ollama: unreachable`.

**Fix**:
```bash
docker compose ps ollama
docker compose restart ollama
# Wait for model: ollama pull llama3.2 (if needed)
```

### Telegram failure

**Symptom**: Ingest or publish fails; Telethon session expired or API errors.

**Fix**:
- Ingest: Re-login `python -m apps.collector.telegram_login`
- Publish: Check `TELEGRAM_BOT_TOKEN`, `TELEGRAM_TARGET_CHAT_ID`
- Session path: Ensure `./data/telethon` is writable and session file exists

### Make webhook down

**Symptom**: `make_publish_failure`, `make_dead_letter` in events_log; items go to DLQ.

**Fix**:
- Verify `MAKE_WEBHOOK_URL` is correct: `python scripts/test_make_webhook.py`
- Check Make scenario is running and webhook is active
- Retry from DLQ after fixing: `POST /dlq/{id}/retry`

### Postgres / Redis unreachable

**Symptom**: API 503; `db: unreachable` or `redis: unreachable` in `/health/ready`.

**Fix**:
```bash
docker compose ps postgres redis
docker compose restart postgres redis
# Wait for health
docker compose up -d
```

### Collector not ingesting

**Symptom**: No new items; `items_ingested_total` not increasing.

**Fix**:
- Check `data/sources.yaml` exists and has valid URLs
- For Telegram: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELETHON_SESSION_PATH`
- Logs: `docker compose logs -f collector`

---

## WhatsApp bot not running / QR not showing

**Symptom**: `/admin/wa/qr` returns 200 but `qr: null`; no `whatsapp-bot` container in `docker compose ps`.

**Cause**: The WhatsApp bot service is **optional** and started only with the `whatsapp` profile.

**Fix**:
```bash
cd /opt/gni-bot-creator
docker compose --profile whatsapp up -d
docker compose ps   # confirm whatsapp-bot is running
docker compose logs whatsapp-bot --tail 20
```

Then in Streamlit (or API), click **Connect WhatsApp** and poll for QR. If the VM IP is blocked by WhatsApp, use **Telegram** or **Make webhook** instead (see docs/WHATSAPP_QR_ARCHITECTURE.md).

---

## Worker not processing (scoring=0, publish=0)

**Symptom**: Worker heartbeat shows `llm_draft=0 publish=0 scoring=0` every cycle.

**Possible causes and fixes**:

1. **DRY_RUN is on** — Worker does not publish for real when `DRY_RUN=1` (default in compose). For production:
   ```bash
   # In .env on VM
   DRY_RUN=0
   ```
   Then restart worker: `docker compose up -d worker`.

2. **No items in pipeline** — Collector may not have created items, or all items are already processed. Check counts:
   ```bash
   docker compose exec postgres psql -U gni -d gni -c "SELECT status, COUNT(*) FROM items GROUP BY status;"
   docker compose exec postgres psql -U gni -d gni -c "SELECT COUNT(*) FROM publications;"
   ```
   If `new`/`scored`/`drafted` are 0, wait for collector to ingest or check collector logs.

3. **Run pipeline once manually** (for debugging):
   ```bash
   docker compose exec worker python -m apps.worker.tasks --once --no-dry-run
   ```

---

## Troubleshooting script

From repo root on the VM:

```bash
bash scripts/troubleshoot_vm.sh
```

This script checks: running services (including whatsapp-bot), worker DRY_RUN, DB item/publication counts, and suggests fixes.
