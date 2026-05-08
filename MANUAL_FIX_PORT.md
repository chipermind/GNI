# Manual Fix for Port Mapping Issue

## The Problem
Port 3100 shows `null` in Docker inspect, meaning the port mapping isn't being applied.

## Solution: Manual Edit on VM

SSH into your VM and run:

```bash
cd /opt/gni-bot-creator

# Edit docker-compose.yml
nano docker-compose.yml
```

Find the `whatsapp-bot:` section (around line 152-157) and change:

**FROM:**
```yaml
    ports:
      - "3100:3100"
```

**TO:**
```yaml
    ports:
      - "0.0.0.0:3100:3100"
```

Save: `Ctrl+X`, then `Y`, then `Enter`

## Then recreate container:

```bash
# Stop and remove
docker compose --profile whatsapp stop whatsapp-bot
docker compose --profile whatsapp rm -f whatsapp-bot

# Recreate
docker compose --profile whatsapp up -d --force-recreate whatsapp-bot

# Wait
sleep 5

# Verify port mapping (should show 0.0.0.0:3100->3100/tcp)
CONTAINER_ID=$(docker compose ps -q whatsapp-bot)
docker inspect $CONTAINER_ID --format='{{json .NetworkSettings.Ports}}' | jq .

# Test
curl -sS http://127.0.0.1:3100/health
```

## Alternative: If port mapping still doesn't work

Try removing the profile temporarily to see if that's the issue:

```bash
# Edit docker-compose.yml
nano docker-compose.yml

# Find and REMOVE these lines under whatsapp-bot:
#   profiles:
#     - whatsapp

# Save and recreate
docker compose stop whatsapp-bot
docker compose rm -f whatsapp-bot
docker compose up -d --force-recreate whatsapp-bot
```
