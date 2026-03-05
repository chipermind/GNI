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

### Testar formatos e depois verificar o schedule

**Passo 1 — Testar run_once (formatos LONG/SHORT) sem publicar:**
```bash
cd /opt/gni-bot-creator
docker compose -f deploy/docker-compose.prod.yml --project-directory . run --rm worker python -m gni.run_once --job briefing_0900 --dry-run
docker compose -f deploy/docker-compose.prod.yml --project-directory . run --rm worker python -m gni.run_once --job radar_interval --dry-run
```
Confirme nos logs: `format_mode=BRIEFING_LONG` / `format_mode=RADAR_SHORT`, `guard: pass`, e (para LONG) `split_parts=...`.

**Passo 2 — Publicar de verdade (enviar para o Telegram):**
```bash
docker compose -f deploy/docker-compose.prod.yml --project-directory . run --rm worker python -m gni.run_once --job briefing_0900
docker compose -f deploy/docker-compose.prod.yml --project-directory . run --rm worker python -m gni.run_once --job radar_interval
```
Confirme: `telegram_sent_ok` nos logs e mensagens no canal.

**Por que aparece "no radar data loaded"?**  
O `run_once` tenta primeiro dados do desk (`get_latest_raw`); neste repo esse canal costuma estar vazio. Foi adicionado **fallback**: se não houver dados do desk, o `run_once` carrega os últimos itens da base (tabela `items`, alimentada pelo collector). Se houver itens na BD, verá no log `radar_data loaded from DB (fallback)` e o briefing/radar será preenchido com essas notícias.

**Passo 3 — Verificar por que o schedule (worker 24/7) não publica:**

O worker que fica sempre a correr (`apps.worker.tasks`) publica **itens** (pipeline scoring → LLM → render → publish) a cada `RUN_EVERY_MINUTES` (ex.: 15). Se passaram horas e não saiu nada:

1. **DRY_RUN no .env** — Se `DRY_RUN=1` (ou vazio), o worker **não envia** para Telegram/Make (só simula). Para publicar de verdade: no `.env` da VM ponha `DRY_RUN=0` e reinicie o worker:
   ```bash
   # No .env: DRY_RUN=0
   docker compose -f deploy/docker-compose.prod.yml --project-directory . up -d worker --force-recreate
   ```
2. **Logs do worker** — Veja se o pipeline está a correr e se há itens a publicar:
   ```bash
   docker compose -f deploy/docker-compose.prod.yml --project-directory . logs --tail=150 worker
   ```
   Procurar: `Pipeline run llm_draft=X publish=Y` (X,Y > 0 quando há trabalho), `gni_send: sent ok`, ou `publish blocked by pause`, `dry_run=True`.
3. **Publicação pausada** — Se alguém chamou `/control/pause`, o worker não publica. Despausar: `curl -X POST http://localhost:8000/control/resume -H "X-API-Key: SUA_API_KEY"`.
4. **Poucos ou nenhum item** — O pipeline só publica itens que estão em estado `drafted`. Se o collector não ingeriu nada ou nada passou a `drafted`, não há o que publicar. Verifique o collector e a base (items com status scored/drafted).

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

### Por que o bot parou após as atualizações — diagnóstico e reativar

**1. Ver se o worker está a correr**
```bash
cd /opt/gni-bot-creator
docker compose -f deploy/docker-compose.prod.yml --project-directory . ps worker
```
Se estiver `Exited` ou `Restarting`, o contentor não está estável.

**2. Ver os últimos logs do worker (erros, crash, pause)**
```bash
docker compose -f deploy/docker-compose.prod.yml --project-directory . logs --tail=100 worker
```
Procurar por: `ModuleNotFoundError`, `pause_all_publish`, `publish blocked by pause`, `guard_failed`, `radar_llm_fallback`, exceções em geral.

**3. Ver se a publicação está pausada (DB)**
Se a API estiver no ar: `GET http://localhost:8000/control/status` (com X-API-Key ou Bearer). Se `paused` ou `pause_all_publish` for true, o worker não publica.

**4. Reativar**

- **Se o worker estava parado (Exited):** subir de novo:
  ```bash
  docker compose -f deploy/docker-compose.prod.yml --project-directory . up -d worker
  ```
- **Se a publicação estava pausada:** despausar via API:
  ```bash
  curl -X POST http://localhost:8000/control/resume -H "X-API-Key: SEU_API_KEY"
  ```
- **Se o worker crashava (ex.: import `db`):** garantir código atualizado (`git reset --hard origin/main` ou `git pull --rebase`), rebuild e subir:
  ```bash
  git fetch origin && git reset --hard origin/main
  docker compose -f deploy/docker-compose.prod.yml --project-directory . build --no-cache worker
  docker compose -f deploy/docker-compose.prod.yml --project-directory . up -d worker
  ```

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
