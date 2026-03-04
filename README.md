# gni-bot-creator

Minimal monorepo for the GNI bot: API, RSS collector, pipeline worker, and publishers (Telegram + Make).

## Pipeline overview

1. **Collector** — Runs RSS + Telegram ingest on an interval (`COLLECTOR_INTERVAL_MINUTES`); writes to raw_items + items (fingerprint dedup). Exits cleanly on SIGTERM.
2. **Worker** — Runs pipeline on a schedule (`RUN_EVERY_MINUTES`): **scoring → LLM classify+generate → render → publish** (Telegram + Make). Ingest is handled by the collector. Idempotency via `items.status` (new → scored → drafted → published | failed).
3. **Publisher** — Telegram Bot API and Make webhook; both support dry_run.
4. **API** — FastAPI health checks, control plane, DB connectivity.

Data flow: **Collector (RSS/Telegram) → Items (DB) → Worker (Score → LLM → Render) → Publish (Telegram / Make)**.

## Worker: RQ (Redis Queue)

The worker uses **RQ** rather than Celery:

- **Redis-native** — The stack already runs Redis; RQ uses it as the only broker, so there are no extra services (e.g. RabbitMQ) or protocol layers.
- **Simplicity** — RQ has a small API and minimal config; jobs are plain Python functions. Celery’s routing, chains, and beat are more than we need for this pipeline.
- **Operational fit** — One Redis connection, easy local runs with `docker compose`, and straightforward debugging with `rq info` / `rq dashboard` (optional).
- **Trade-off** — Celery would be a better fit if we needed scheduled/periodic tasks (beat), complex workflows (chains, groups), or non-Redis brokers. For “consume from Redis and run jobs,” RQ is sufficient and keeps the stack small.

## Run locally with Docker

**One-shot (start and check health):**

```bash
cd gni-bot-creator
cp .env.example .env   # optional; compose has defaults
./scripts/run_local.sh
```

This runs `docker compose up -d`, waits until the API healthcheck passes (max 120s), then calls `http://127.0.0.1:8000/health` and prints **OK** or **FAIL**.

**Manual run:**

1. From repo root:
   ```bash
   cp .env.example .env
   docker compose up -d
   ```

2. Check API health:
   ```bash
   curl http://127.0.0.1:8000/health
   ```

3. **API security (optional)** — When `JWT_SECRET` or `API_KEY` is set, control endpoints require authentication. Use `X-API-Key` header or `Authorization: Bearer <JWT>`. When neither is set, auth is disabled (backward compatible).
   ```bash
   # With API_KEY set in .env:
   curl -H "X-API-Key: $API_KEY" -X POST http://127.0.0.1:8000/control/resume
   ```
   Configure: `JWT_SECRET`, `API_KEY`, `ADMIN_API_KEY` (fallback for local testing), `CORS_ALLOWED_ORIGINS`, `API_RATE_LIMIT_PER_MINUTE`, `API_RATE_LIMIT_PER_HOUR`.

   **Control plane endpoints** (require auth): `/control/pause`, `/control/resume`, `/control/status`, `/sources`, `/review/pending`, `/review/{id}/approve`, `/review/{id}/reject`. `/control/status` returns pause flags, pipeline counters (items/drafts/publications last hour), dependency status (db, redis, ollama), last 10 failures from events_log.

4. **Environment variable configuration** — Numeric environment variables are parsed safely using `get_int_env()`:
   - **Missing/empty/whitespace** → Uses default value (no error)
   - **Non-numeric value** (e.g., `"abc"`) → Raises `ValueError` with clear message
   - **Valid integer** → Parsed and used
   - **Out of range** (below min or above max) → Raises `ValueError` with clear message
   
   **Best practice:** Never set numeric env vars to empty strings (`""`). Either omit them (to use defaults) or set valid integers. Examples:
   ```bash
   # ✅ Good: omit to use default
   # API_RATE_LIMIT_PER_MINUTE not set → defaults to 60
   
   # ✅ Good: set valid integer
   API_RATE_LIMIT_PER_MINUTE=100
   
   # ❌ Bad: empty string uses default (no error, but not explicit)
   API_RATE_LIMIT_PER_MINUTE=""
   
   # ❌ Bad: non-numeric raises ValueError
   API_RATE_LIMIT_PER_MINUTE="abc"  # Raises: Environment variable API_RATE_LIMIT_PER_MINUTE must be an integer
   ```
   
   **Numeric environment variables and defaults:**
   
   **API (`apps/api/middleware.py`):**
   - `API_RATE_LIMIT_PER_MINUTE` (default: 60, min: 1)
   - `API_RATE_LIMIT_PER_HOUR` (default: 1000, min: 1)
   - `API_MAX_BODY_SIZE` (default: 65536, min: 1024) — 64KB
   
   **API Database (`apps/api/db/session.py`):**
   - `DB_POOL_SIZE` (default: 5, min: 1)
   - `DB_MAX_OVERFLOW` (default: 10, min: 0)
   - `DB_POOL_RECYCLE` (default: 1800, min: 60) — 30 minutes
   - `DB_POOL_TIMEOUT` (default: 30, min: 1)
   
   **Worker Cache (`apps/worker/cache.py`):**
   - `CACHE_TTL_SECONDS` (default: 86400, min: 1) — 24 hours
   
   **Worker Tasks (`apps/worker/tasks.py`):**
   - `RUN_EVERY_MINUTES` (default: 15, min: 1)
   - `TELEGRAM_SINCE_MINUTES` (default: 60, min: 1)
   - `PUBLISH_MAX_WORKERS` (default: 4, min: 1)
   - `MAX_PIPELINE_ATTEMPTS` (default: 3, min: 1)
   
   **Worker Retry (`apps/worker/retry.py`):**
   - `PUBLISH_MAX_ATTEMPTS` (default: 3, min: 1)
   
   **Worker Render (`apps/worker/render.py`):**
   - `WHATSAPP_MAX_CHARS` (default: 3500, min: 100)
   
   **Worker Dedupe (`apps/worker/dedupe.py`):**
   - `DEDUPE_DAYS` (default: 7, min: 1)
   
   **Worker Circuit Breaker (`apps/worker/circuit_breaker.py`):**
   - `CIRCUIT_FAILURE_THRESHOLD` (default: 5, min: 1)
   
   **Worker Ollama (`apps/worker/llm/ollama_ensure.py`):**
   - `OLLAMA_PULL_TIMEOUT_SECONDS` (default: 1800, min: 1) — 30 minutes
   - `OLLAMA_PULL_MAX_RETRIES` (default: 6, min: 1)
   - `OLLAMA_PULL_BACKOFF_SECONDS` (default: 20, min: 1)
   
   **Worker Ollama Client (`apps/worker/llm/ollama_client.py`):**
   - `OLLAMA_MAX_JSON_RETRY` (default: 1, min: 0)
   
   **Collector (`apps/collector/main.py`):**
   - `COLLECTOR_INTERVAL_MINUTES` or `COLLECTOR_INTERVAL` (default: 15, min: 1)
   - `INGEST_LIMIT` (default: 50, min: 1)
   - `TELEGRAM_SINCE_MINUTES` (default: 60, min: 1)
   
   See `apps/shared/env_helpers.py` for implementation details.

5. Stop:
   ```bash
   docker compose down
   ```

**API startup verification** (build, bring up stack, confirm health):

```bash
docker compose build api
docker compose up -d
curl -sf http://127.0.0.1:8000/health && echo " OK" || echo " FAIL"
```

Or run `./scripts/verify_api_startup.sh` from repo root (same steps; exits 0 only if health returns OK).

**Services:** postgres, redis, ollama, api (FastAPI, port 8000), collector (RSS + Telegram ingest), worker (scoring → LLM → publish). All use an internal Docker network, healthchecks, and `restart: unless-stopped`.

**Dependencies:** Single source of truth: `requirements.txt`. API, worker, and collector install from the same file. Key libs pinned: fastapi, uvicorn, sqlalchemy, psycopg2-binary, redis, httpx, telethon, feedparser, pydantic.

**Docker security:** Containers run as root. For production hardening, add a non-root user in Dockerfiles; bind mounts (e.g. `./data/telethon`) may need `chown` to match container uid.

### Streamlit app (WhatsApp Connect)

Login + WhatsApp Connect UI. Talks only to your FastAPI backend (no secrets required in the app).

**Run locally:**

```bash
# From repo root (uses streamlit_app.py entrypoint)
pip install -r requirements-streamlit.txt
streamlit run streamlit_app.py

# Or from the app folder
cd apps/wa-qr-cloud-ui
pip install -r requirements.txt
streamlit run app.py
```

**Point to your VM backend (no secrets file):**

- **Query param (recommended):** Open the app with `?api_base_url=http://YOUR_VM_IP:8000` (e.g. `https://yourapp.streamlit.app/?api_base_url=http://1.2.3.4:8000`). Replace `YOUR_VM_IP` with your VM’s public IP.
- **Env (optional):** Set `API_BASE_URL=http://YOUR_VM_IP:8000` in Streamlit Cloud → Settings → Environment variables, or when running locally.

No `.streamlit/secrets.toml` or other secrets are required; the app works with the query param or env only.

**Streamlit Cloud deployment:**

- **Main file path:** `streamlit_app.py` (repo root).
- **Requirements file:** `requirements-streamlit.txt` (in Cloud app settings, set this so only Streamlit deps are installed).
- **Secrets:** None. Optional env `API_BASE_URL` if you don’t want to use the query param.

### Production VM deployment

The `docker-compose.yml` is tuned for production:

- **Ports:** Only the **API** is exposed (default 8000; use 80/443 if behind a reverse proxy). Postgres, Redis, Ollama, and whatsapp-bot have **no** port mappings. Optional: `docker compose --profile qr-ui up -d` exposes Streamlit QR UI on 8501.
- **Worker / collector / whatsapp-bot:** No port mapping; internal network only.
- **VM-first / internal DNS:** Containers use service names only: `postgres:5432`, `redis:6379`, `ollama:11434`, `whatsapp-bot:3100`. No localhost in inter-service URLs (startup fails in Docker if set).
- **Volumes:** Named: `postgres_data`, `redis_data`, `ollama_models`. Bind mounts: `./data/telethon` (Telethon), `./data/wa-auth` (Baileys), `./backups` (pg dumps when using `--profile backup`). Create `./backups` on the host if using the backup profile.
- **Healthchecks:** postgres (pg_isready), redis (redis-cli ping), ollama (ollama list), api (GET /health), worker (Redis ping), whatsapp-bot (GET /health). Collector has no healthcheck.

**Bootstrap VM (one command):**

```bash
# On VM inside /opt/gni-bot-creator: configure everything
bash scripts/bootstrap_vm_all.sh
```

Requires `.env` (copies from `.env.example` if missing, then prompts to edit). Validates env, starts services, installs systemd, runs **flux_e2e_verify.sh**; finishes with PASS only if E2E passes. Prints exact commands for logs and status.

**Deploy from local machine to VM:**

```bash
# Deploy (rsync + docker compose on VM; health check)
bash scripts/deploy_vm.sh

# Tail logs on VM
bash scripts/tail_vm_logs.sh

# SSH into VM at deploy path
bash scripts/ssh_vm.sh
```

Variables (defaults): `VM_USER=root`, `VM_HOST=217.216.84.81`, `VM_PATH=/opt/gni-bot-creator`.

**Deploy on VM (manual):**

```bash
# e.g. deploy path /opt/gni-bot-creator
cp .env.example .env
# Edit .env: API_PORT=8000, OLLAMA_BASE_URL=http://ollama:11434, DEPLOY_PATH=/opt/gni-bot-creator
docker compose up -d
docker compose ps   # all should show healthy
```

**Ollama model:** Default is `qwen2.5:7b` (VM-friendly). The worker pulls the model on startup if missing; see **docs/OLLAMA.md** for recommended models, disk/RAM, and verify commands.

**Test Ollama from inside a container:**

```bash
# From api container (curl installed):
docker compose exec api curl -s http://ollama:11434/api/tags

# From worker container (Python):
docker compose exec worker python -c "import httpx; r=httpx.get('http://ollama:11434/api/tags'); print(r.status_code)"
```

**Run 24/7 (systemd):**

```bash
# On VM: install systemd unit (auto-start on boot)
sudo bash scripts/install_systemd.sh

# Status and logs
sudo systemctl status gni-bot
sudo journalctl -u gni-bot -f

# Uninstall
sudo bash scripts/uninstall_systemd.sh
```

After VM reboot, the stack auto-starts.

### VM Firewall (UFW)

Expose only SSH and the API (or reverse-proxy ports). Block everything else.

```bash
# Reset and default deny
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Allow SSH (required for admin)
sudo ufw allow 22/tcp

# Allow API (direct) — use one of:
sudo ufw allow 8000/tcp
# Or, if API is behind nginx/caddy on 80/443:
# sudo ufw allow 80/tcp
# sudo ufw allow 443/tcp

# Optional: allow Streamlit QR UI only if you use --profile qr-ui
# sudo ufw allow 8501/tcp

# Enable (will prompt to confirm if SSH might be affected)
sudo ufw enable
sudo ufw status verbose
```

Do **not** allow 5432 (Postgres), 6379 (Redis), 11434 (Ollama), or 3100 (whatsapp-bot); those services are internal only.

**VM verification:**

```bash
# On VM inside /opt/gni-bot-creator
bash scripts/verify_vm.sh
```

Checks: Docker health, API health, internal connectivity (api→ollama, postgres, redis), control endpoints (pause/resume), Make webhook smoke test. Prints PASS/FAIL per step; exits non-zero on any FAIL.

## Verification checklist

Run the full E2E verification to ensure ingest, scoring, LLM draft, and publish work end-to-end:

```bash
# Required in .env for real publish: TELEGRAM_BOT_TOKEN, TELEGRAM_TARGET_CHAT_ID (or TELEGRAM_CHAT_ID), MAKE_WEBHOOK_URL
bash scripts/verify_e2e.sh
```

The script:

1. Starts `docker compose` and waits for health
2. Asserts `curl /health` returns OK
3. Resumes publish (`POST /control/resume`)
4. Ingests one RSS batch (limit 5)
5. Optionally ingests Telegram (last 10 min) if a Telethon session exists
6. Runs scoring, LLM draft, and publish for real (Telegram Bot API + Make webhook)
7. Pauses (`POST /control/pause`)
8. Attempts publish again and asserts it is blocked (logs `publish_blocked`)
9. Prints summary: items ingested, drafts, publications success/failed/blocked

If Telegram or Make is not configured, the script prints which env vars are missing and exits non-zero. On success, it prints **ALL CHECKS PASSED**.

When `API_KEY` is set (e.g. in `.env`), the script passes it to control endpoints. Without `API_KEY`, auth is disabled and no header is sent.

### Webhook Verification (Make)

Smoke test for the Make webhook — sends a test payload and checks 2xx response.

**Set `MAKE_WEBHOOK_URL`** in `.env` or export it:

```bash
# In .env:
MAKE_WEBHOOK_URL=https://your-token@hook.us2.make.com/...
```

**Run the smoke test:**

```bash
python scripts/test_make_webhook.py
```

- Prints HTTP status code and response text (trimmed).
- Exits 0 on 2xx, non-zero otherwise.
- If `MAKE_WEBHOOK_URL` is missing, exits with a clear message.

### Manual Make webhook publish

To publish one rendered message to Make webhook (for acceptance testing):

```bash
# With stack running and MAKE_WEBHOOK_URL in .env:
docker compose run --rm -e DRY_RUN=0 worker python scripts/publish_one_make.py

# Simulate failure (invalid URL):
MAKE_WEBHOOK_URL=https://invalid-404.example.com docker compose run --rm worker python scripts/publish_one_make.py
```

On success: logs Publication (sent) and events_log (make_publish_success). On failure: attempts increase with backoff, events_log (make_publish_failure, make_dead_letter).

### Simulate news (format and workflow)

Validate template format and Make payload without Docker/Ollama:

```bash
python scripts/simulate_news.py
# Or: pytest tests/test_simulate_workflow.py -v
```

Checks Template A (ANALISE_INTEL), Template B (FLASH_SETORIAL), and Make webhook payload spec. On Windows use `set PYTHONIOENCODING=utf-8` first if you see encoding errors.

### GNI preview (format + splitter, no Telegram)

Validate editorial format, guard, and LONG splitter locally without sending. Dry-run (default) prints: `format_mode`, guard result (pass/fail), and for LONG the number of chunks and each chunk with a clear separator. Optionally send to a **test channel** with `--send` (requires `TELEGRAM_TEST_CHAT_ID`; script exits with error if unset — safe).

**Quick commands:**

```bash
# 1 LONG (dry-run: guard + chunks count + chunks with separator)
python scripts/gni_preview.py --mode LONG

# 2 SHORT (dry-run)
python scripts/gni_preview.py --mode SHORT
python scripts/gni_preview.py --job-name radar_interval

# Send to test channel (only when TELEGRAM_TEST_CHAT_ID is set)
python scripts/gni_preview.py --mode SHORT --send
```

**Flags:** `--mode LONG|SHORT|FLASH`, `--job-name briefing_0900|radar_interval|intel_flash|...`, `--dry-run` (default: true), `--send`. Safe: without `TELEGRAM_TEST_CHAT_ID`, `--send` does not send and exits with error.

### Flux E2E Verification

Runs **on the VM** (or locally). One command to confirm the VM is production-ready:

```bash
bash scripts/flux_e2e_verify.sh
```

**Steps:** (1) `docker compose up -d`, (2) wait for healthchecks, (3) `curl http://localhost:8000/health` PASS, (4) insert 2 test items (A: alegação/não confirmada → Template A, B: defesa/sistema/teste → Template B), (5) run pipeline for those items (classify + generate + render), (6) validate Template A (Portuguese headings + ⸻) and Template B (Em destaque + 📌 Insight:), (7) publish: DRY_RUN=true → invoke but not sent; DRY_RUN=false → max 1 msg per channel if Telegram/Make/WhatsApp configured, (8) PASS/FAIL summary; exit 0 only if all checks pass.

**CLI for pipeline on specific items:**
```bash
python -m apps.worker.run_pipeline --item-ids 1,2 --publish
# Or dry-run:
python -m apps.worker.run_pipeline --item-ids 1,2 --dry-run
```

**Safety:** Never loops infinitely; max 2 publish per channel during test; skips publish step with SKIPPED if env vars missing.

## Database migrations (Alembic)

Schema migrations use **Alembic**. Migrations live in `alembic/versions/`. Full lifecycle (structure, first boot, adding migrations): **`docs/ALEMBIC.md`**.

**First boot:** API and worker run `init_db()` on startup: if Alembic is present they run `alembic upgrade head`; if that fails or Alembic is missing they fall back to `Base.metadata.create_all()` so the app starts on a fresh DB with no manual steps.

**Upgrade from repo root** (optional; migrations also run at startup):
```bash
alembic upgrade head
```
Requires `DATABASE_URL` in env or `.env`.

**Current migrations:** `001_initial`, `002_ensure_missing_columns`, `003_add_composite_indexes`, `004_dead_letter_queue`, `005_users`. See `alembic/versions/`.

**Backup (Postgres):** Run `scripts/backup_postgres.sh` (or `docker compose --profile backup run --rm backup`). Writes to `./backups/gni_YYYYMMDD_HHMMSS.sql`; retention via `BACKUP_RETENTION` (default 7). Cron (daily 02:30): `30 2 * * * /opt/gni-bot-creator/scripts/backup_postgres.sh`. Restore: see `docs/RUNBOOK.md` (Backup / Restore).

## Deploy to VM

1. **Provision Ubuntu** (Docker, UFW, optional swap):
   ```bash
   sudo bash scripts/provision_ubuntu.sh
   # Optional swap: sudo bash scripts/provision_ubuntu.sh --swap 2048
   ```

2. **Clone repo** and configure:
   ```bash
   git clone <repo-url> /opt/gni-bot-creator
   cd /opt/gni-bot-creator
   cp .env.example .env
   # Edit .env (Postgres, DRY_RUN, etc.)
   ```

3. **Install systemd unit** (docker compose up -d on boot):
   ```bash
   sudo APP_DIR=/opt/gni-bot-creator bash scripts/install_systemd.sh
   sudo systemctl start gni-bot.service
   ```

4. **Reboot** — After reboot, `gni-bot.service` starts and runs `docker compose up -d` so the stack auto-starts.

## Deployment hardening

- **Startup validation** — App fails fast on bad config (DATABASE_URL, REDIS_URL). `validate_config()` runs at API and worker startup. In Docker, any critical URL containing localhost/127.0.0.1 causes immediate exit (VM-first: use service DNS only).
- **Feature flags** — Runtime toggles in DB: `GET /control/features`, `POST /control/features/{name}?enabled=true|false`.
- **Readiness/liveness** — `/health/live` (always 200), `/health/ready` (DB + Redis). Use `/health/ready` for zero-downtime deploys.
- **Secrets provider** — `get_secret(key)` abstracts access. Default: EnvProvider (env from .env, docker, k8s). Set `SECRETS_PROVIDER` to swap (no infra lock-in).

## Observability

- **Structured logging** — Set `LOG_JSON=1` for JSON logs; `LOG_LEVEL` (INFO, DEBUG, etc.).
- **Prometheus metrics** — API: `GET /metrics`. Metrics: `items_ingested_total`, `drafts_generated_total`, `publications_success_total`, `publications_failed_total`, `llm_latency_seconds`, `pipeline_cycle_duration_seconds`, plus `pipeline_step_items_total{step}`, `publish_total{channel,status}`.
- **Monitoring** — `docker compose --profile monitoring up -d` adds Prometheus (port 9090); scrapes `api:8000/metrics` every 30s.
- **Health** — `/health/live`, `/health/ready` (DB + Redis), `/health/detailed` (DB, Redis, Ollama).
- **Runbook** — `docs/RUNBOOK.md` (deploy, pause/resume, DLQ, backup, common failures). `docs/INCIDENTS.md` (emergencies).
- **Admin UI** — `GET /admin` serves a minimal page: pause flag, pending review (approve/reject), DLQ (retry/drop). Requires API key when auth enabled.
- **OpenTelemetry tracing** — Set `OTEL_EXPORTER_OTLP_ENDPOINT` to enable; spans for pipeline steps.

## WhatsApp QR Bridge (Secure)

The **WhatsApp bot** (`whatsapp-bot`) runs on the internal network and is not exposed publicly. To show QR code and status in a remote UI (e.g. **Streamlit Cloud**), the API exposes a secure bridge that proxies only the necessary data.

**Important:** The Streamlit (or any remote) UI must call the **API** endpoints below, not the whatsapp-bot service directly. The bot should remain reachable only from inside your network (e.g. `http://whatsapp-bot:3100`).

### Configuration

- `WA_BOT_BASE_URL` — Internal URL of whatsapp-bot (default: `http://whatsapp-bot:3100`).
- `WA_QR_BRIDGE_TOKEN` — **Required.** Long random secret; used as Bearer token for bridge endpoints. Generate e.g. with `openssl rand -hex 32`.
- `WA_QR_TTL_SECONDS` — Redis cache TTL for QR (default: 120).
- `WA_KEEPALIVE_INTERVAL_SECONDS` — Background keepalive interval (default: 25).
- `WA_RECONNECT_BACKOFF_SECONDS` — Backoff after keepalive errors (default: 30).
- `WA_QR_RATE_LIMIT_PER_MINUTE` — Per-IP rate limit for `/admin/wa/qr` (default: 20).
- `STREAMLIT_ORIGIN` — Optional. If set (e.g. `https://yourapp.streamlit.app`), this origin is added to CORS so the Streamlit app can call the API.

### Endpoints (Bearer token required)

All bridge endpoints require: `Authorization: Bearer <WA_QR_BRIDGE_TOKEN>`. Missing or invalid token returns **401**.

- **`GET /admin/wa/status`** — Proxies to whatsapp-bot `/health`. Returns `connected`, `status`, `lastDisconnectReason`, `server_time` (ISO8601).
- **`GET /admin/wa/qr`** — Returns `qr` (string or `null`), `status` (`qr_ready` or `not_ready`), `ts` (unix timestamp). Reads from Redis first; on miss, proxies to whatsapp-bot and caches. Public aliases: `GET /wa/qr`, `POST /wa/connect` (same as reconnect).

### Example (do not log or commit the token)

```bash
# Set token in env (e.g. export WA_QR_BRIDGE_TOKEN="your_long_random_token")
curl -s -H "Authorization: Bearer $WA_QR_BRIDGE_TOKEN" "https://your-api.example.com/admin/wa/status"
curl -s -H "Authorization: Bearer $WA_QR_BRIDGE_TOKEN" "https://your-api.example.com/admin/wa/qr"
```

If `WA_QR_BRIDGE_TOKEN` is not set, bridge endpoints return **503** (bridge not configured). The raw QR string is never logged by the API.

### WhatsApp QR: How it works (24/7 robust)

1. **Redis persistence**: When the bridge receives a QR from the bot (via GET /wa/qr or keepalive), it caches it in Redis (`wa:last_qr`, `wa:last_qr_ts`) with TTL `WA_QR_TTL_SECONDS` (default 120s).
2. **Read path**: GET /wa/qr (and /admin/wa/qr) reads Redis first; on hit returns `{ "qr": "<string>", "status": "qr_ready", "ts": <unix> }`. On miss, proxies to the bot, caches on success, returns `{ "qr": null, "status": "not_ready" }` otherwise.
3. **Background keepalive**: The API runs a background task (every `WA_KEEPALIVE_INTERVAL_SECONDS`) that checks connection status. If disconnected, it triggers reconnect, polls the bot for QR, and caches it in Redis. On errors it backs off (`WA_RECONNECT_BACKOFF_SECONDS` + jitter) and continues.
4. **Streamlit UI**: After Connect/Reconnect, polls the QR endpoint for up to 90s with progressive intervals (5/10/15s). Renders QR with qrcode lib; on timeout shows "Bridge is reconnecting; try Refresh QR in 10s".

**Commands to run 24/7 on VM:**
```bash
docker compose up -d
docker compose logs -f api    # or worker, if applicable
curl -s -H "Authorization: Bearer $WA_QR_BRIDGE_TOKEN" http://127.0.0.1:8000/wa/status
./scripts/verify_wa_flow.sh   # Verify full flow (status → connect → poll QR)
```

## Resilience (circuit breaker + retry)

External calls (Ollama, Telegram API, Make webhook) are protected by:

- **Circuit breaker** — Opens after repeated failures (default: 5); blocks further calls until recovery timeout (default: 60s); then half-open to test. Config: `CIRCUIT_FAILURE_THRESHOLD`, `CIRCUIT_RECOVERY_TIMEOUT`.
- **Exponential backoff retry** — Config: `PUBLISH_MAX_ATTEMPTS`, `PUBLISH_BACKOFF_BASE`. `CircuitOpenError` fails immediately (no retry).
- **Graceful degradation** — Pipeline does not crash; failed items are marked `failed` with `last_error`; circuit recovers automatically.

## Log rotation basics

- **Docker JSON log driver** (default): logs grow unbounded. Limit size in `docker-compose.yml` per service, e.g.:
  ```yaml
  logging:
    driver: json-file
    options:
      max-size: "10m"
      max-file: "3"
  ```
- **Host logrotate**: if you redirect container stdout to a file, add a `/etc/logrotate.d/gni-bot` rule to rotate that file (daily, keep 7, compress).

## Postgres backup basics

- **Manual dump**: `docker compose exec postgres pg_dump -U gni gni > backup_$(date +%Y%m%d).sql`
- **Cron**: add a daily job (e.g. 2am) that runs the above and copies the file off-host or to S3. Restore with `psql` or `docker compose exec -T postgres psql -U gni gni < backup.sql`.

## Telegram ingestion (Telethon)

The collector can ingest from **Telegram** channels/chats in addition to RSS. Sources are read from the DB: `sources` with `type=telegram` and `chat_id` set (channel username, e.g. `channelname`, or numeric ID).

### One-time login (create session)

1. Get **API ID** and **API hash** from [my.telegram.org](https://my.telegram.org) (create an application).
2. Add to `.env`:
   ```bash
   TELEGRAM_API_ID=12345678
   TELEGRAM_API_HASH=your_api_hash_here
   ```
3. Ensure the session volume exists and run the login (interactive; use a TTY):
   ```bash
   mkdir -p data/telethon
   docker compose run --rm -it worker python -m apps.collector.telegram_login
   ```
4. When prompted, enter your **phone number** (with country code, e.g. `+1234567890`) and the **verification code** Telegram sends. The session is saved under `./data/telethon` (mounted at `/data/telethon` in the worker).

After this, the worker can run Telegram ingest without re-entering credentials.

### Ingest Telegram messages

- **Manual run** (last 60 minutes, configurable):
  ```bash
  docker compose run --rm worker python -m apps.collector.telegram_ingest --since-minutes 60
  ```
- The pipeline scheduler also runs Telegram ingest (with `TELEGRAM_SINCE_MINUTES`, default 60) together with RSS on each cycle.

### Adding Telegram sources

Insert rows into `sources` with `type='telegram'` and `chat_id` set to the channel username (e.g. `durov`) or the numeric channel/chat ID. Example (SQL):

```sql
INSERT INTO sources (name, type, chat_id) VALUES ('My Channel', 'telegram', 'channelname');
```

## How to run in dry_run

- **Default**: set `DRY_RUN=1` in `.env`; the worker publishes in dry_run (prints messages / payloads, still writes to `publications`).
- **One-shot**: `docker compose run --rm worker python -m apps.worker.tasks --once --dry-run`
- **Scheduler**: leave `DRY_RUN=1` in `.env` so the pipeline runs continuously but never sends real Telegram/Make traffic.

## VM environment and secrets

For production VM deployment:

1. **Copy and fill env:** `cp .env.example .env`
2. **Generate secrets:** run `bash scripts/gen_secrets.sh` and paste the output into `.env` (Postgres password, JWT secret, API key, WA_QR_BRIDGE_TOKEN).
3. **Validate:** `python scripts/validate_env.py [api|worker|all]` (with `.env` loaded). If required vars are missing, exit is 1 with a clear error.
4. **Startup:** API and Worker run env validation on startup; if required vars are missing, the service refuses to start with an error.

Conditional requirements: Telegram (TELEGRAM_BOT_TOKEN + TELEGRAM_TARGET_CHAT_ID) only when either is set; MAKE_WEBHOOK_URL only when set; WA_QR_BRIDGE_TOKEN required when STREAMLIT_ORIGIN is set.

## Environment variables (placeholders)

Copy from `.env.example`. Compose provides defaults for Postgres and Redis so `up -d` works without a filled `.env`.

| Variable | Service | Description |
|----------|---------|-------------|
| `POSTGRES_USER` | postgres | DB user (default: gni) |
| `POSTGRES_PASSWORD` | postgres | DB password (default: gni) |
| `POSTGRES_DB` | postgres | DB name (default: gni) |
| `REDIS_URL` | api, worker | Set by compose (redis://redis:6379/0) |
| `DATABASE_URL` | api, worker, collector | Set by compose from Postgres vars |
| `RUN_EVERY_MINUTES` | worker | Pipeline interval in minutes (default: 15) |
| `INGEST_LIMIT` | collector | Max items per ingest run (default: 50) |
| `DRY_RUN` | worker | 1 = dry_run (default), 0 = real publish (stubs still used) |
| `OLLAMA_BASE_URL` | worker | Ollama API URL (compose: http://ollama:11434) |
| `RQ_QUEUE` | worker | Queue name (default: default) |
| `API_HOST` | API | Bind host |
| `API_PORT` | API | Bind port |
| `API_SECRET_KEY` | API | Secret key placeholder |
| `COLLECTOR_INTERVAL_MINUTES` | collector | Ingest interval in minutes (default: 15) |
| `RSS_SOURCES_PATH` | Collector | Path to sources config |
| `TELEGRAM_API_ID` | collector | API ID from my.telegram.org (for ingest) |
| `TELEGRAM_API_HASH` | collector | API hash from my.telegram.org (for ingest) |
| `TELETHON_SESSION_PATH` | collector, worker | Session file path (default: /data/telethon/session) |
| `TELEGRAM_SINCE_MINUTES` | collector | Minutes of history for Telegram ingest (default: 60) |
| `TELEGRAM_BOT_TOKEN` | Publisher | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Publisher | Telegram chat/channel ID |
| `MAKE_WEBHOOK_URL` | Publisher | Make webhook URL |
| `MAKE_API_KEY` | Publisher | Make API key |
| `DATA_SOURCES_PATH` | Shared | Override path to sources YAML |
| `DATA_KEYWORDS_PATH` | Shared | Override path to keywords YAML |

## Troubleshooting

### ModuleNotFoundError: No module named 'apps.api'

This happens when Python cannot resolve the `apps` package (e.g. inside the API or worker container). The fix is in place if you use the repo’s Docker setup:

1. **Root cause:** The container’s working directory must be the repo root (`/app`), and `PYTHONPATH` must be set to `/app` so that `import apps.api` resolves to `/app/apps/api`.

2. **What we do:**
   - **API Dockerfile:** Copies the full `apps/` tree into `/app/apps/`, sets `ENV PYTHONPATH=/app`, and runs `uvicorn apps.api.main:app` (so the app is loaded as a package).
   - **Worker Dockerfile:** Copies the repo with `COPY . .` and sets `ENV PYTHONPATH=/app`.
   - **docker-compose:** Both `api` and `worker` services have `environment: PYTHONPATH: /app` (and `env_file: .env` is unchanged).

3. **Verify:** From repo root:
   ```bash
   docker compose build api worker
   docker compose up -d
   docker compose exec api python -c "import apps.api.core.settings; print('OK')"
   docker compose exec worker python -c "import apps.worker.tasks; print('OK')"
   ```
   Or run `./scripts/test_imports.sh all` to test both.
