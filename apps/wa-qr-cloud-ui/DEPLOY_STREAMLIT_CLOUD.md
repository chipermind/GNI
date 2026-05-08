# Deploy UI changes to Streamlit Cloud

After changing the Streamlit app (e.g. UI, sidebar, pages), deploy to Streamlit Cloud:

## 1. Commit and push from your repo root

From the **repository root** (e.g. `gni-bot-creator` or the repo that contains `apps/wa-qr-cloud-ui`):

```bash
# Stage Streamlit app changes
git add apps/wa-qr-cloud-ui/
git add apps/wa-qr-cloud-ui/app.py
git add apps/wa-qr-cloud-ui/pages/
git add apps/wa-qr-cloud-ui/src/
git add apps/wa-qr-cloud-ui/assets/

# Commit
git commit -m "Streamlit: premium UI — sidebar, login card, WhatsApp Connect logo, status card"

# Push to the remote Streamlit Cloud is watching (e.g. main or master)
git push origin main
```

(Use your actual branch name if different, e.g. `master`.)

## 2. Let Streamlit Cloud redeploy

- **Auto-deploy:** If the app is set to deploy on push, wait 1–2 minutes; the new version will go live.
- **Manual redeploy:** Open [share.streamlit.io](https://share.streamlit.io) → your app → **⋮** → **Reboot app**.

## 3. Streamlit Cloud app settings (reminder)

- **Root directory:** `apps/wa-qr-cloud-ui`
- **Main file path:** `app.py`
- **Secrets:** `GNI_API_BASE_URL` (your VM API URL, no trailing slash)

After pushing, your updated sidebar (GNI, icons, “You’re on”), login card, WhatsApp Connect logo, and Status card will appear on Streamlit Cloud.
