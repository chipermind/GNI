# Global News Intel — pipeline 24/7 na VM

Pipeline que agrega notícias de **Hacker News**, **Euro Intel Mais**, **Coin Sauce**, **RU** e sites configurados, e envia para o grupo **Global News Intel** no Telegram, rodando 24/7 na VM.

## Conexão SSH (gni-vm)

Para usar `ssh gni-vm` e `scp ... gni-vm:...`, adicione no `~/.ssh/config` (ou `C:\Users\<user>\.ssh\config` no Windows):

```
Host gni-vm
  HostName 217.216.84.81
  User root
```

Depois: `ssh gni-vm` e `scp data/telethon/session* gni-vm:/opt/gni-bot-creator/data/telethon/`

## Visão geral

```
RSS (sources.yaml)  ─┐
Telegram channels   ─┼─► Collector ─► DB items ─► Worker (scoring, LLM) ─► Publish ─► Global News Intel
(Euro Intel, etc.)  ─┘
```

## 1. Fontes RSS (já configuradas)

- **Hacker News** (frontpage + show)
- **Crypto:** CoinDesk, Cointelegraph, The Block, Bitcoinist
- **Macro:** Reuters, Bloomberg, FT, CNBC
- **Social:** Reddit r/CryptoCurrency

Arquivo: `data/sources.yaml`

## 2. Fontes Telegram (configurar)

Rode localmente para listar seus canais e obter os IDs:

```powershell
python scripts/telegram_list_chats_telethon.py
```

Anote os IDs de **Euro Intel Mais**, **Coin Sauce**, **RU** (e Bellum Acta / Tabz, se quiser).

Na VM, após o deploy, rode para popular o banco (passe TELEGRAM_SOURCES na linha de comando ou coloque no .env da VM):

```bash
# Com os IDs (uma vez):
docker compose exec -e TELEGRAM_SOURCES="Euro Intel Mais:-1002281264507,Coin Sauce:-1001535764422,Bellum Acta:-1001161666782,Tabz:-1001950487092" collector python scripts/add_telegram_sources.py
```

Ou adicione `TELEGRAM_SOURCES=...` no `.env` da VM e depois: `docker compose exec collector python scripts/add_telegram_sources.py`

Ou via API:

```bash
curl -X POST http://localhost:8000/sources -H "Content-Type: application/json" -d '{"name":"Euro Intel Mais","type":"telegram","chat_id":"-1001234567890"}'
```

## 3. Grupo Global News Intel

1. Rode `python scripts/telegram_list_chats_telethon.py` e encontre o ID do grupo **Global News Intel**.
2. Adicione ao `.env` na VM:

```env
TELEGRAM_BOT_TOKEN=seu_token_do_BotFather
TELEGRAM_CHAT_ID=-100xxxxxxxxx
# Ou TELEGRAM_TARGET_CHAT_ID
```

O bot precisa ser **membro** do grupo Global News Intel para enviar mensagens. Adicione o bot pelo menu do grupo → Adicionar membros.

## 4. Sessão Telethon na VM

A sessão Telethon (convertida do Desktop) precisa estar na VM:

```powershell
# No PC (após telegram_convert_desktop_session.py)
scp data/telethon/session* gni-vm:/opt/gni-bot-creator/data/telethon/
# Ou sem alias: scp data/telethon/session* root@217.216.84.81:/opt/gni-bot-creator/data/telethon/
```

Ou copie manualmente os arquivos `session.session` e `session.session-journal` (se existir) para `data/telethon/` na VM.

## 5. .env na VM

Exemplo mínimo para o pipeline 24/7:

```env
# DB
DATABASE_URL=postgresql://gni:gni@postgres:5432/gni
POSTGRES_USER=gni
POSTGRES_PASSWORD=gni
POSTGRES_DB=gni

# Redis
REDIS_URL=redis://redis:6379/0

# Telegram (para publicar no Global News Intel)
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=-100xxxxxxxxx

# Telethon (para ingest dos canais)
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
TELETHON_SESSION_PATH=/data/telethon/session

# Collector
COLLECTOR_INTERVAL_MINUTES=15
INGEST_LIMIT=50
TELEGRAM_SINCE_MINUTES=60

# Worker (publicar, não dry-run)
DRY_RUN=0
RUN_EVERY_MINUTES=15
```

## 6. Deploy e rodar 24/7

```bash
# Deploy
bash scripts/deploy_vm.sh

# Ou manualmente na VM:
cd /opt/gni-bot-creator
docker compose up -d
```

Serviços que sobem:

- **postgres**, **redis**, **ollama** — infra
- **api** — API HTTP
- **collector** — ingest RSS + Telegram a cada 15 min
- **worker** — scoring, LLM, publish a cada 15 min

Todos com `restart: unless-stopped` (rodam 24/7).

## 7. Verificar

```bash
# Logs do collector
docker compose logs -f collector

# Logs do worker
docker compose logs -f worker
```

Mensagens novas devem aparecer no grupo **Global News Intel**.
