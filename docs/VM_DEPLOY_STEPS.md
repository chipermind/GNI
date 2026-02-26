# Deploy na VM — passo a passo

## 1. No seu PC — rodar o deploy

```powershell
cd c:\Users\lucas\GNI\gni-bot-creator
.\scripts\deploy_vm.ps1 -Full
```

(O deploy envia os arquivos e roda `docker compose` na VM.)

---

## 2. Na VM — configurar o .env

Conecte na VM:

```powershell
ssh gni-vm
```

Edite o `.env`:

```bash
nano /opt/gni-bot-creator/.env
```

Confirme ou ajuste:

```env
# Publicar no grupo — uma das opções:
# A) Bot API (padrão):
TELEGRAM_BOT_TOKEN=seu_token_do_BotFather
TELEGRAM_CHAT_ID=-1002698141517
TELEGRAM_TARGET_CHAT_ID=-1002698141517
# B) Ou TELEGRAM_WEBHOOK_URL se você tiver um endpoint que recebe POST e envia via sua conta

# Telethon (ingest dos canais)
TELEGRAM_API_ID=32126246
TELEGRAM_API_HASH=be39b3030639d1224becdcf54fa22a0f
TELETHON_SESSION_PATH=/data/telethon/session
# Add more channels (e.g. Frontline Report): see docs/ADD_TELEGRAM_CHANNEL.md
TELEGRAM_SOURCES=Euro Intel Mais:-1002281264507,Coin Sauce:-1001535764422,Bellum Acta:-1001161666782,Tabz:-1001950487092,Frontline Report:CHAT_ID

# Publicar de verdade
DRY_RUN=0
```

Salve (Ctrl+O, Enter, Ctrl+X).

---

## 3. Na VM — sessão Telethon

Sessão já copiada com `scp`? Verifique:

```bash
ls -la /opt/gni-bot-creator/data/telethon/
```

Deve ter `session.session`. Se não tiver, copie do PC:

```powershell
# No PC
scp C:\Users\lucas\GNI\data\telethon\session* gni-vm:/opt/gni-bot-creator/data/telethon/
```

---

## 4. Na VM — bot no grupo

Crie um bot no @BotFather (se ainda não tiver), copie o token e coloque em `TELEGRAM_BOT_TOKEN`.

Adicione o bot ao grupo **Global News Intel** (menu do grupo → Adicionar membros).

---

## 5. Na VM — fontes Telegram (se ainda não fez)

Replace `CHAT_ID` in `.env` (TELEGRAM_SOURCES) with the real Frontline Report chat_id (get it: forward a message to @userinfobot). Then:

```bash
docker compose exec collector python scripts/add_telegram_sources.py
```

(See docs/ADD_TELEGRAM_CHANNEL.md to add any Telegram channel.)

---

## 6. Na VM — subir/atualizar serviços

```bash
cd /opt/gni-bot-creator
docker compose up -d
```

---

## 7. Conferir logs

```bash
# Collector (ingest)
docker compose logs -f collector

# Worker (scoring + publish)
docker compose logs -f worker
```

---

## Resumo

| Passo | Onde | Ação |
|-------|------|------|
| 1 | PC | `.\scripts\deploy_vm.ps1 -Full` |
| 2 | VM | Editar `.env` (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DRY_RUN=0) |
| 3 | VM | Verificar/copiar `data/telethon/session*` |
| 4 | Telegram | Bot adicionado ao grupo Global News Intel |
| 5 | VM | Rodar `add_telegram_sources.py` (se necessário) |
| 6 | VM | `docker compose up -d` |
| 7 | VM | `docker compose logs -f collector` e `worker` |
