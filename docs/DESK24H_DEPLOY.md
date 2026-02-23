# Desk24H — Deploy & Rollback

## Step 1: Deploy with feature flag off

Deploy new code with `DESK24H_ENABLED=0` (default in `.env.example` and `docker-compose.yml`).

```bash
# Deploy (DESK24H_ENABLED=0 by default)
./scripts/deploy_vm.ps1
# or
bash scripts/deploy_vm.sh
```

## Step 2: Smoke tests on VM

Run compose dry-run (1 message, saves to SQLite, no Telegram):

```bash
# Full compose + validate + save to DB (dry-run, no send)
docker compose exec -T api python -m desk.scheduler --dry-run --type PANORAMA_0900 --compose
```

Run VM smoke script:

```bash
docker compose exec -T api bash scripts/desk_deploy_smoke_vm.sh
```

Validates:
- Compose returned `ok=true` (or at least ran)
- DB has `snapshots` and `posts` rows
- Meta shows `blocked_claims`, `used_sources` when present

## Step 3: Activate and monitor

Set in `.env` on VM:

```env
DESK24H_ENABLED=1
```

Restart API:

```bash
docker compose restart api
```

Monitor logs for:
- `blocked_claims` — sections replaced with filler (citation violations)
- `used_sources` — citations/sources used
- `sent_ok` — Telegram/Make send succeeded

```bash
docker compose logs -f api | grep -E 'run_window|blocked_claims|used_sources|sent_ok'
```

## Rollback (instant)

If issues occur:

1. Set `DESK24H_ENABLED=0` in `.env`
2. Restart: `docker compose restart api`

Scheduler stops; no more desk posts sent.
