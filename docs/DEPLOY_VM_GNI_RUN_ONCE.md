# Deploy VM — checklist GNI run_once (smoke “run once”)

**ISTO É VM/PRODUÇÃO.** Checklist mínimo para deploy e smoke test com **run_once**. Sem alterar DB/schema nem scheduler (apenas reativar depois). Sem alterar infra além de rebuild/restart do(s) container(s).

**Importante:** Use sempre o compose de produção: `deploy/docker-compose.prod.yml`. **Não existe serviço `scheduler`** nesse compose — só `worker` (e api, collector, etc.). Não use `docker compose build worker scheduler`.

---

## 1. Na VM — atualizar código e reiniciar

```bash
cd /opt/gni   # ou /opt/gni-bot-creator / seu APP_DIR

# Atualizar código (se der "divergent branches", use um dos comandos abaixo)
git pull
# Se falhar com "Need to specify how to reconcile divergent branches":
#   git pull --rebase origin main    # ou: git pull --no-rebase origin main

# Rebuild e restart só do worker (não há serviço "scheduler" neste compose)
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

## 5. Troubleshooting

### `ModuleNotFoundError: No module named 'db'`

O worker está a rodar com **código antigo**. O projeto usa `apps.api.db.models`, não `db.models`. Faça:

1. Resolver o git e puxar o código mais recente:
   ```bash
   cd /opt/gni
   git fetch origin
   git checkout main
   git pull --rebase origin main   # ou: git merge origin/main
   ```
2. Reconstruir a imagem do worker para que o container use o código novo:
   ```bash
   docker compose -f deploy/docker-compose.prod.yml --project-directory . build --no-cache worker
   docker compose -f deploy/docker-compose.prod.yml --project-directory . up -d worker
   ```

### `no such service: scheduler`

O ficheiro `deploy/docker-compose.prod.yml` **não define** o serviço `scheduler`. Use apenas `worker` (e os outros serviços do compose). Exemplo correto:

```bash
docker compose -f deploy/docker-compose.prod.yml --project-directory . build worker
docker compose -f deploy/docker-compose.prod.yml --project-directory . up -d worker
```

### Dois paths na VM: `/opt/gni` vs `/opt/gni-bot-creator`

Use **só um** path no servidor (evita confusão). Em ambos, o compose deve ser `deploy/docker-compose.prod.yml` e a rede `gni-net` está definida no fim do ficheiro.

**Se usar `/opt/gni`** (branches divergentes):

```bash
cd /opt/gni
git fetch origin
git checkout main
git pull --rebase origin main
docker compose -f deploy/docker-compose.prod.yml --project-directory . build --no-cache worker
docker compose -f deploy/docker-compose.prod.yml --project-directory . up -d worker
```

**Se usar `/opt/gni-bot-creator`** (local changes would be overwritten, e.g. `data/gni.db`):

```bash
cd /opt/gni-bot-creator
git fetch origin
git checkout main
git checkout -- data/gni.db
git pull --rebase origin main
docker compose -f deploy/docker-compose.prod.yml --project-directory . build --no-cache worker
docker compose -f deploy/docker-compose.prod.yml --project-directory . up -d worker
```

Se ainda aparecer `undefined network gni-net`, confirme que o ficheiro no servidor tem no fim a secção `networks: gni-net:`. Se não tiver, o pull não trouxe a versão nova — force: `git fetch origin && git reset --hard origin/main`.

### `Container ... is restarting, wait until the container is running`

O worker está em crash loop (por exemplo por causa do `ModuleNotFoundError: db`). Corrija o código/imports como acima, faça rebuild e `up -d worker`. Depois use `docker compose logs -f worker` para confirmar que arranca.

---

## Resumo (copiar/colar)

```bash
cd /opt/gni
git fetch origin && git checkout main && git pull --rebase origin main
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
