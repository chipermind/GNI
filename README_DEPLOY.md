# VM deployment — GNI bot creator

Step-by-step to run the full stack on a VM (Ubuntu) with Docker.

## 1. Clone and prepare

```bash
# Clone repo (or extract deploy_bundle.tar.gz into an existing clone)
git clone <REPO_URL> /opt/gni-bot-creator
cd /opt/gni-bot-creator
```

If you only have the deploy archive:

```bash
mkdir -p /opt/gni-bot-creator
cd /opt/gni-bot-creator
tar -xzf deploy_bundle.tar.gz
# You now have deploy/ with docker-compose.prod.yml, .env.example, scripts/, nginx/
# You still need the rest of the repo (apps/, etc.) for Docker build context. Clone the repo and merge deploy/ into it, or extract the archive inside a full clone.
```

Recommended: clone the full repo, then overwrite or merge `deploy/` from the archive so you have both app code and deploy files.

## 2. Set environment

```bash
cp deploy/.env.example .env
chmod 600 .env
```

Edit `.env` and set at least:

- **POSTGRES_PASSWORD** — strong password for Postgres (and use the same value in DATABASE_URL if you set it explicitly).
- **JWT_SECRET** — long random string (e.g. `openssl rand -hex 24`).
- **API_KEY** — API key for programmatic access.
- **ADMIN_API_KEY** — admin API key.
- **WA_QR_BRIDGE_TOKEN** — long random string; same token used in Streamlit/wa-qr-ui to call the API for WhatsApp QR/status.

Optional but recommended for delivery:

- **TELEGRAM_BOT_TOKEN** and **TELEGRAM_TARGET_CHAT_ID** (or **TELEGRAM_WEBHOOK_URL**) for Telegram fallback when WhatsApp is disconnected.

Do **not** commit `.env` or put real secrets in the repo.

## 3. Install Docker (Ubuntu)

On a fresh Ubuntu VM:

```bash
sudo bash deploy/scripts/install_docker_ubuntu.sh
docker compose version
```

## 4. Start the stack

From the repo root:

```bash
./deploy/scripts/start.sh
```

This runs:

- `docker compose -f deploy/docker-compose.prod.yml --project-directory . up -d --build`

Services: **postgres**, **redis**, **ollama**, **api**, **collector**, **worker**, **whatsapp-bot**, **wa-qr-ui**.

## 5. Verify health

- **API:** `curl -s http://localhost:8000/health`
- **wa-qr-ui (Streamlit):** open http://localhost:8501
- **whatsapp-bot:** `curl -s http://localhost:3100/health`

Containers use healthchecks; `docker compose -f deploy/docker-compose.prod.yml --project-directory . ps` should show “healthy” for api and wa-qr-ui once ready.

## 6. Open the UI

1. Open **http://&lt;VM_IP&gt;:8501** (Streamlit wa-qr-ui).
2. Set the backend URL to **http://&lt;VM_IP&gt;:8000** (or paste when prompted).
3. Log in (or use seed credentials if configured).
4. Go to **WhatsApp Connect** and paste **WA_QR_BRIDGE_TOKEN** (same as in `.env`). Connect and scan the QR.

---

## Useful commands

- **Stop:** `./deploy/scripts/stop.sh`
- **Logs:** `./deploy/scripts/logs.sh` or `./deploy/scripts/logs.sh api`
- **Backup Postgres + retention:** `./deploy/scripts/backup.sh` (writes to `backups/`, keeps last 7 by default)

---

## Common failures and behavior

### WhatsApp disconnected loop (WA_CONNECT_START → DISCONNECTED → BACKOFF)

- **Cause:** Bot cannot keep a connection to WhatsApp (e.g. IP/network block, unsupported browser/user-agent in headless, or protocol/version mismatch).
- **What you see:** Logs show repeated connect → disconnect → backoff; QR may never appear or connection drops soon after.
- **Actions:**
  - Check **whatsapp-bot** logs: `./deploy/scripts/logs.sh whatsapp-bot`. Look for `DISCONNECTED_PAYLOAD`, `statusCode`, and `reason`.
  - Use **Telegram fallback:** set **TELEGRAM_BOT_TOKEN** and **TELEGRAM_TARGET_CHAT_ID** (or **TELEGRAM_WEBHOOK_URL**) in `.env`. When WhatsApp is not connected, messages are sent via Telegram instead of failing.
  - Consider a proxy or different network if WhatsApp is blocking the VM IP; see project docs for proxy/architecture options.

### Delivery fallback (Telegram when WhatsApp is down)

- If **whatsapp-bot** is not connected (`connected=false`), the worker sends via **Telegram** (Bot API or TELEGRAM_WEBHOOK_URL) so delivery does not fail.
- In the UI, when WhatsApp is unavailable you’ll see: “WhatsApp unavailable, using Telegram fallback.”

### API or wa-qr-ui unhealthy

- Ensure **POSTGRES_PASSWORD** and **DATABASE_URL** (if set) match and that Postgres is up.
- For **wa-qr-ui**, the image includes `.streamlit/config.toml` with `fileWatcherType = "none"` and `runOnSave = false` to avoid restart loops; no extra config needed on the VM.

### Build failures

- Run from **repo root** with `-f deploy/docker-compose.prod.yml --project-directory .` so build context is the repo (required for `apps/*` Dockerfiles).
- If you only have the deploy archive, you must have the full repo (e.g. clone first, then merge in `deploy/`) so that build context exists.

---

## Build deploy archive (from repo root)

To produce a deploy bundle (e.g. for copying to a VM or handing off):

```bash
./deploy/scripts/build_archive.sh
```

Output: **deploy_bundle.tar.gz** (and **deploy_bundle.zip** if `zip` is installed). Contents: the **deploy/** folder (docker-compose.prod.yml, .env.example, nginx/, scripts/). No secrets or private keys are included.

Then copy `deploy_bundle.tar.gz` to the VM, extract (e.g. into a clone), set `.env` as above, and run `./deploy/scripts/start.sh`.
