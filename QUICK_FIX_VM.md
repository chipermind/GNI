# 🚨 QUICK FIX: You're Inside a Docker Container!

## The Problem

You're seeing `/app #` prompt, which means you're **inside a Docker container**, not on the VM host.

Commands like `docker`, `curl`, `jq` don't exist inside containers - they need to run on the **VM host**.

## Solution: Exit the Container First

### Step 1: Exit the Container

```bash
exit
```

You should see the prompt change from `/app #` to `root@vmi3053842:/opt/gni-l#` or similar.

### Step 2: Navigate to Project Directory

```bash
cd /opt/gni-bot-creator
```

If this directory doesn't exist, check where your project is:
```bash
ls -la /opt/
find /opt -name "docker-compose.yml" 2>/dev/null
```

### Step 3: Install Missing Tools (if needed)

```bash
# For Debian/Ubuntu:
apt-get update && apt-get install -y curl jq

# For Alpine:
apk add --no-cache curl jq
```

### Step 4: Now Run Deployment Commands

```bash
# Pull latest code
git pull origin main

# Rebuild containers
docker compose up -d --build --force-recreate whatsapp-bot api

# Watch logs (from VM host, not inside container!)
docker compose logs -f whatsapp-bot
```

## How to Tell Where You Are

- **Inside container:** Prompt shows `/app #` or `/ #`
- **On VM host:** Prompt shows `root@vmi3053842:/opt/... #`

## Quick Test

Run this to check:
```bash
# If this works, you're on VM host:
docker ps

# If you get "docker: not found", you're inside a container - type 'exit'
```
