# Deploy to Streamlit Cloud (fix "Missing configuration")

## 1. Push your code (if needed)

If your app at **automatewa.streamlit.app** is connected to a repo that doesn’t have this folder yet:

- Push this folder (`wa-qr-cloud-ui`) to that repo.
- In Streamlit Cloud: **Settings → General → Root directory** = path to this app (e.g. `apps/wa-qr-cloud-ui`). **Main file path:** `app.py`.
- Click **Reboot app** after saving.

## 2. Add secrets (fix the red banner)

1. Open **[Streamlit Community Cloud](https://share.streamlit.io/)** and sign in.
2. Open your app (**automatewa**).
3. Go to **Settings → Secrets**.
4. Paste the block below and **replace the placeholders** with your real values:

```toml
GNI_API_BASE_URL = "https://your-api.example.com"
WA_QR_BRIDGE_TOKEN = "your_long_random_bridge_token"
SEED_CLIENT_EMAIL = "admin@yourcompany.com"
SEED_CLIENT_PASSWORD = "your_secure_password"
SEED_CLIENT_ROLE = "client"
```

5. Click **Save**. The app will reload; the "Missing configuration" error should disappear.
6. Log in with `SEED_CLIENT_EMAIL` and `SEED_CLIENT_PASSWORD`.

## 3. Optional

- **API key:** If your backend uses `X-API-Key` for Monitoring/Posts, add in Secrets:  
  `API_KEY = "your-api-key"`
- **Root directory:** If the repo root is not this app, set **Root directory** to e.g. `apps/wa-qr-cloud-ui`.

After saving secrets, always **refresh the app** or use **Reboot app** in Settings.
