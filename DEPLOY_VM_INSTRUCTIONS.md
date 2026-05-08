# Deploy WhatsApp Bot Fixes to VM

## Step 1: SSH into your VM

Open PowerShell or Windows Terminal and run:

```powershell
ssh root@217.216.84.81
```

(Enter your password when prompted)

## Step 2: Once connected to the VM, run these commands:

```bash
# Navigate to project directory
cd /opt/gni-bot-creator

# Pull latest changes
git pull origin main
# (or: git pull origin master - if your branch is master)

# Rebuild and restart containers
docker compose up -d --build --force-recreate whatsapp-bot api

# Wait a few seconds for containers to start
sleep 5

# Check status
docker compose ps whatsapp-bot api

# Watch logs (to see QR_READY, CONNECTED, etc.)
docker compose logs -f --tail 200 whatsapp-bot
```

## Step 3: Test the endpoints (in another terminal on VM)

```bash
# SSH into VM again in a new terminal window
ssh root@217.216.84.81

cd /opt/gni-bot-creator

# Load environment variables
set -a && source .env && set +a

# Test bot endpoints
curl -sS http://127.0.0.1:3100/status | jq .
curl -sS http://127.0.0.1:3100/debug/auth | jq .

# Test API endpoints
curl -sS -H "Authorization: Bearer $WA_QR_BRIDGE_TOKEN" http://127.0.0.1:8000/admin/wa/status | jq .

# Trigger reconnect
curl -sS -X POST -H "Authorization: Bearer $WA_QR_BRIDGE_TOKEN" http://127.0.0.1:8000/admin/wa/reconnect

# Check QR (wait a few seconds first)
sleep 3
curl -sS -H "Authorization: Bearer $WA_QR_BRIDGE_TOKEN" http://127.0.0.1:8000/admin/wa/qr | jq .
```

## Alternative: Use the deployment script

Once on the VM, you can use the automated script:

```bash
cd /opt/gni-bot-creator
chmod +x scripts/deploy_to_vm.sh
bash scripts/deploy_to_vm.sh
```

## Troubleshooting

If `git pull` fails:
- Check if you're on the right branch: `git branch`
- Check if there are uncommitted changes: `git status`
- If needed, stash changes: `git stash` then `git pull`

If containers don't start:
- Check logs: `docker compose logs whatsapp-bot`
- Check if port 3100 is available: `netstat -tuln | grep 3100`
