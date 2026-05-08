# Add a Telegram channel for ingest (e.g. Frontline Report)

To have the VM **catch the news** from a Telegram channel (same as your other sources), add it to ingest and run the collector.

## Quick add: Frontline Report (one command on VM)

If the channel is **Frontline Report** (t.me/frontlinelive), run on the VM:

```bash
cd /opt/gni-bot-creator
git pull origin main
bash scripts/add_frontline_report_vm.sh
```

No arguments needed: the script adds **Frontline Report** by username `frontlinelive`. If your channel is different or uses a numeric ID, run: `bash scripts/add_frontline_report_vm.sh -1001991611234` (replace with the real ID from @userinfobot).

---

## Manual: any channel (get chat_id first)

### 1. Get the channel chat_id

- **Option A:** Forward any message from the channel to **@userinfobot** in Telegram. The bot replies with the chat ID (e.g. `-1001991611234`).
- **Option B:** On the VM, run (after Telethon session is set up):
  ```bash
  docker compose exec collector python scripts/telegram_list_chats_telethon.py
  ```
  Join the channel with the Telethon account first if it’s private; the script lists chats and their IDs.

For **Frontline Report**, use the ID you get (e.g. `-100199161XXXX` or the channel’s `@username` if it’s public).

## 2. Add the channel on the VM

**Edit `.env` on the VM** and add the channel to `TELEGRAM_SOURCES` (comma-separated, format `Name:chat_id`):

```bash
cd /opt/gni-bot-creator
nano .env
```

Append or set (one line, no spaces around `=`):

```env
TELEGRAM_SOURCES=Euro Intel Mais:-1002281264507,Coin Sauce:-1001535764422,Bellum Acta:-1001161666782,Tabz:-1001950487092,Frontline Report:-100199161XXXX
```

Replace `-100199161XXXX` with the real **Frontline Report** chat_id from step 1. If the channel has a public username you can use e.g. `Frontline Report:FrontlineReport`.

Save (Ctrl+O, Enter, Ctrl+X).

## 3. Register the source in the DB

Run the add-sources script so the collector will fetch from the new channel:

```bash
docker compose exec collector python scripts/add_telegram_sources.py
```

This reads `TELEGRAM_SOURCES` from the environment (the collector container gets it from your `.env`) and inserts any new sources into the DB. You should see e.g. `Added: Frontline Report (-100199161XXXX)`.

## 4. No need to restart

The collector already loads Telegram sources from the DB on each run. On the next collector cycle it will fetch from **Frontline Report** like the other channels.

To trigger ingest once by hand:

```bash
docker compose exec collector python -m apps.collector
```

(Or wait for the next scheduled run.)

---

**Summary:** Get Frontline Report’s chat_id → add `Frontline Report:chat_id` to `TELEGRAM_SOURCES` in `.env` on the VM → run `docker compose exec collector python scripts/add_telegram_sources.py`.
