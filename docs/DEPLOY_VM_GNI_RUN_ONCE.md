# Deploy VM — checklist GNI run_once (smoke “run once”)

**ISTO É VM/PRODUÇÃO.** Checklist mínimo para deploy e smoke test com **run_once**. Sem alterar DB/schema nem scheduler (apenas reativar depois). Sem alterar infra além de rebuild/restart do(s) container(s).

---

## 1. Na VM — atualizar código e reiniciar

```bash
cd /opt/gni-bot-creator   # ou seu APP_DIR

git pull

# Rebuild e restart do(s) container(s) relevante(s) (worker; scheduler se existir)
docker compose -f deploy/docker-compose.prod.yml --project-directory . build worker
docker compose -f deploy/docker-compose.prod.yml --project-directory . up -d worker

# Alternativa: script que sobe todos
# ./deploy/scripts/start.sh
```

---

## 2. Smoke test — run_once LONG e SHORT

Rodar **uma vez** cada job para validar: carrega notícias → router → format_mode → guards → (opcional) publicar.

**Dry-run (não publica):**

```bash
# LONG (briefing)
docker compose -f deploy/docker-compose.prod.yml --project-directory . run --rm worker python -m gni.run_once --job briefing_0900 --dry-run

# SHORT (radar)
docker compose -f deploy/docker-compose.prod.yml --project-directory . run --rm worker python -m gni.run_once --job radar_interval --dry-run
```

**Ou use o script de smoke (ambos dry-run em sequência):**

```bash
./deploy/scripts/smoke_run_once.sh
```

**Publicar de verdade (sem --dry-run):**

```bash
docker compose -f deploy/docker-compose.prod.yml --project-directory . run --rm worker python -m gni.run_once --job briefing_0900
docker compose -f deploy/docker-compose.prod.yml --project-directory . run --rm worker python -m gni.run_once --job radar_interval
```

**Logs esperados no worker/VM:** `format_mode=...`, `split_parts=...` (se LONG), `telegram_sent_ok` (se enviado).

---

## 3. Tail logs

```bash
docker compose -f deploy/docker-compose.prod.yml --project-directory . logs -f worker
```

(Saída de `run --rm` já vai para o terminal.)

---

## 4. Reativar scheduler (se tiver sido pausado)

Quando estável: via API `POST /control/resume` ou reiniciando o serviço que dispara os jobs.

---

## Resumo (copiar/colar)

```bash
cd /opt/gni-bot-creator
git pull
docker compose -f deploy/docker-compose.prod.yml --project-directory . build worker
docker compose -f deploy/docker-compose.prod.yml --project-directory . up -d worker

# Smoke (dry-run) — 1 LONG + 1 SHORT
./deploy/scripts/smoke_run_once.sh
# Ou manual:
docker compose -f deploy/docker-compose.prod.yml --project-directory . run --rm worker python -m gni.run_once --job briefing_0900 --dry-run
docker compose -f deploy/docker-compose.prod.yml --project-directory . run --rm worker python -m gni.run_once --job radar_interval --dry-run

# Publicar (opcional)
docker compose -f deploy/docker-compose.prod.yml --project-directory . run --rm worker python -m gni.run_once --job briefing_0900
docker compose -f deploy/docker-compose.prod.yml --project-directory . run --rm worker python -m gni.run_once --job radar_interval

# Logs
docker compose -f deploy/docker-compose.prod.yml --project-directory . logs -f worker
```
