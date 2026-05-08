# Bot 24/7 on the VM

The bot runs **persistently** as long as the **collector** and **worker** containers are up. No manual run needed after setup.

## How it works

- **Collector:** Runs in a loop; every N minutes it ingests from RSS + Telegram and writes items to the DB.
- **Worker:** Runs in a loop; every N minutes it runs the pipeline (scoring → LLM draft → publish to Telegram/WhatsApp).

So **keep both containers running** and the bot will keep catching news and publishing 24/7.

## Make sure it’s on

```bash
cd /opt/gni-bot-creator
docker compose ps
```

You should see **collector** and **worker** as **Up**. If not:

```bash
docker compose up -d collector worker
```

## Publish for real (not dry-run)

In `.env` on the VM set:

```env
DRY_RUN=0
```

Then restart the worker so it picks the new value:

```bash
docker compose up -d worker
```

## Manual one-off run (optional)

**Ingest once (RSS + Telegram):**

```bash
docker compose exec collector python -m apps.collector --once
```

**Run the full pipeline once and publish:**

```bash
docker compose exec worker python -m apps.worker.tasks --once --no-dry-run
```

(Do **not** use `run_pipeline --item-ids` for normal use; the worker loop already runs the full pipeline.)

## If the bot stops

1. Check containers: `docker compose ps` — worker and collector should be Up (healthy).
2. Check worker logs: `bash scripts/vm_logs.sh worker 80`
3. Ensure `DRY_RUN=0` in `.env` and restart worker: `docker compose up -d worker`
