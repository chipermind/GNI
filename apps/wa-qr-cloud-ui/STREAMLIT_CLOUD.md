# Streamlit Cloud — point app at your VM API

1. Open **https://share.streamlit.io** → your app → **⋮** → **Settings**.
2. Open the **Secrets** tab and add (use your VM IP and the same token as in your VM `.env`):
   ```toml
   GNI_API_BASE_URL = "http://217.216.84.81:8000"
   WA_QR_BRIDGE_TOKEN = "same_value_as_WA_QR_BRIDGE_TOKEN_in_VM_env"
   ```
   Get the token on the VM: `grep WA_QR_BRIDGE_TOKEN /opt/gni-bot-creator/.env` — copy that value into `WA_QR_BRIDGE_TOKEN` in Streamlit Secrets.
   **Optional:** `WA_API_PREFIX = "/wa"` — use public aliases (`/wa/status`, `/wa/qr`, `/wa/connect`) instead of `/admin/wa/*`. Default is `/admin/wa`; both work with the same auth.
3. **Save**. The app will redeploy and use this URL and token for login and WhatsApp Connect.

**If the app is on HTTPS** (e.g. `*.streamlit.app`), the API URL **must be HTTPS** or the browser will block requests. Use a reverse proxy or tunnel to expose your API over HTTPS.

**Create a user** on the VM first (once the API is up):
```bash
curl -s -X POST http://YOUR_VM_IP:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"your@email.com","password":"YourPassword"}'
```
Then log in on the Streamlit app with that email and password.

**Test WhatsApp reconnect + QR on the VM** (use the token from `.env`):
```bash
cd /opt/gni-bot-creator
export $(grep WA_QR_BRIDGE_TOKEN .env | xargs)
curl -X POST -H "Authorization: Bearer $WA_QR_BRIDGE_TOKEN" http://localhost:8000/admin/wa/reconnect
sleep 60
curl -H "Authorization: Bearer $WA_QR_BRIDGE_TOKEN" http://localhost:8000/admin/wa/qr
```
If the second `curl` returns `"qr": "<string>"`, the bot is working. If it returns `"qr": null`, check `docker compose logs -f whatsapp-bot`.
