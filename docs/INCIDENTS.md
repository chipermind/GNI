# GNI Bot Creator — Incident Response

What to do in emergencies.

## Triage

1. **Check health**: `curl http://localhost:8000/health`
2. **Check status**: `curl -H "X-API-Key: $API_KEY" http://localhost:8000/control/status`
3. **Check logs**: `docker compose logs -f --tail=200`

---

## Immediate Actions

### Stop all publishing (emergency pause)

```bash
curl -X POST -H "X-API-Key: $API_KEY" http://localhost:8000/control/pause
```

### Stop the stack

```bash
docker compose down
```

### Restart everything

```bash
docker compose up -d
# Wait 30–60s for services to be healthy
docker compose ps
```

---

## High-Impact Scenarios

### Spam / runaway publishing

1. **Pause**: `POST /control/pause`
2. Check `DRY_RUN` in `.env` — set to `1` to disable real publish
3. Restart worker: `docker compose restart worker`

### Database corruption / restore needed

1. **Pause** and **stop** stack
2. Restore from latest backup (see RUNBOOK.md Backup/Restore)
3. Migrations run automatically on API/worker startup. If you need to run them manually (e.g. after restoring DB): `docker compose run --rm api alembic -c /app/alembic.ini upgrade head`
4. **Resume** and restart

### Credential leak (API_KEY, Telegram, Make)

1. **Pause** immediately
2. Rotate credentials in `.env` and external services (Make, Telegram)
3. Restart: `docker compose down && docker compose up -d`

---

## Escalation

- **Runbook**: `docs/RUNBOOK.md` for detailed procedures
- **Logs**: `docker compose logs` or `journalctl -u gni-bot -f` (if systemd)
- **Metrics**: `http://localhost:9090` (if Prometheus profile enabled)
