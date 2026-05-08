# GNI — Streamlit Cloud UI

Multi-page Streamlit app: **Home**, **WhatsApp Connect**, **Monitoring**, **Posts**. Login via API (JWT); only **GNI_API_BASE_URL** is required in Secrets.

## Streamlit Cloud Setup

1. Deploy from this repo. Set **Root directory** to this folder (e.g. `apps/wa-qr-cloud-ui`). **Main file path:** `app.py`.

2. In **Settings → Secrets**, set **only**:

```toml
GNI_API_BASE_URL = "https://your-api.example.com"
```

(No trailing slash. Use your VM API URL.)

3. **Save** and **Reboot**. Users log in with their API account (email/password); JWT in session; they see only their own WhatsApp QR/status.

**Optional:** `SEED_CLIENT_EMAIL` / `SEED_CLIENT_PASSWORD` (legacy fallback), `SEED_CLIENT_ROLE`, `API_KEY`, `AUTO_REFRESH_SECONDS`.

**WhatsApp Connect** (when using bridge token auth):
```toml
GNI_API_BASE_URL = "http://217.216.84.81:8000"
WA_QR_BRIDGE_TOKEN = "same_value_as_WA_QR_BRIDGE_TOKEN_in_VM_env"
WA_API_PREFIX = "/admin/wa"
```
`WA_API_PREFIX` defaults to `/admin/wa`; endpoints: `/admin/wa/status`, `/admin/wa/qr`, `/admin/wa/reconnect`.

## Troubleshooting

- **"Missing configuration"** — Set **GNI_API_BASE_URL** in **Settings → Secrets**. Save and refresh.

- **Health check failure**  
  The app calls `GET {GNI_API_BASE_URL}/health`. If you see "API health: …" with an error: (1) Check `GNI_API_BASE_URL` is correct and reachable from Streamlit Cloud. (2) Ensure the API is up and `/health` returns 200. (3) If the API uses auth for `/health`, it must allow unauthenticated access for this check or you’ll see a 401.

- **Invalid email or password** — Log in with an account that exists in the API (e.g. `/auth/register` or your seed script).

- **Monitoring / Posts return 401 or 404**  
  If your backend expects `X-API-Key`, set `API_KEY` in Secrets. Endpoints: `/monitoring/status`, `/monitoring/recent`, `POST /monitoring/run`, `/review/pending`, `POST /review/{id}/approve`, `POST /review/{id}/reject`.

## Run locally

```bash
cd apps/wa-qr-cloud-ui
pip install -r requirements.txt
# Create .streamlit/secrets.toml with the keys above (or export env vars)
streamlit run app.py
```

## Security

- **Secrets:** Stored only in Streamlit Secrets or env; never in code or logs.
- **Passwords:** Hashed with bcrypt (passlib); never stored in plaintext.
- **Auth:** Session-only (`st.session_state`); no local file persistence (Cloud disk is ephemeral).
- **QR data:** Never printed or logged.

## Pages

- **Home** — Config OK, API health, quick links. Login required.
- **WhatsApp Connect** — Client role only. Step-by-step QR flow; status chips 🟢🟡🔴; Refresh QR (rate-limited 10s).
- **Monitoring** — Scraping/jobs; client sees own data, admin sees all; "Run now ▶️" with confirmation.
- **Posts** — Pending/Published tabs; Approve ✅ / Reject ❌; same client/admin visibility.
