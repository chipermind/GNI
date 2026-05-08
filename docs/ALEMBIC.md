# Alembic: structure, first boot, and adding migrations

## Structure (repository-level)

- **`alembic.ini`** (repo root) — Config; no credentials. `sqlalchemy.url` is set from `DATABASE_URL` in `alembic/env.py`.
- **`alembic/env.py`** — Loads config, sets URL from env, imports `Base` from `db.models` (Docker) or `apps.api.db.models` (local). Used for both `upgrade` and `revision --autogenerate`.
- **`alembic/versions/`** — One file per migration (e.g. `001_initial.py`, `002_ensure_missing_columns.py`). Keep in git.

The API Docker image copies `alembic.ini` and `alembic/` into `/app/` so migrations run inside the container without shell workarounds.

## First boot behavior

On API (and worker) startup, `init_db()` in Python does the following:

1. **If Alembic is present** (`alembic.ini` and `alembic/` exist): run `alembic upgrade head`.  
   - On **success**: log `DB bootstrap: alembic upgrade head succeeded` and continue.
2. **If Alembic is missing or `upgrade head` fails** (e.g. no revisions applied yet, empty DB): fall back to **`Base.metadata.create_all(engine)`** using the same `DATABASE_URL`.  
   - Log: `DB bootstrap: Base.metadata.create_all (fallback; alembic not present or no revisions)`.

So the API starts successfully on first boot even when no migrations have been run; tables are created from the current models. Once migrations exist and run successfully, Alembic is used on subsequent starts. No shell scripts or `docker exec` are required for normal startup.

## Adding migrations going forward

1. **Change models** in `apps/api/db/models.py` (or the codebase that defines `Base.metadata`).
2. **From repo root** (with `DATABASE_URL` set):
   ```bash
   alembic revision --autogenerate -m "describe_your_change"
   ```
3. **Review** the new file under `alembic/versions/` and fix any optional/rename logic if needed.
4. **Commit** the new version file. On next deploy, `init_db()` will run `alembic upgrade head` and apply it.

**Inside the API container** (optional, for ad‑hoc checks):

```bash
docker compose exec api alembic -c /app/alembic.ini current
docker compose exec api alembic -c /app/alembic.ini upgrade head
```

Normal startup already runs migrations; use the above only for debugging or manual recovery.
