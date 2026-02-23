#!/usr/bin/env bash
# Deploy gni-bot-creator to VM from LOCAL machine.
# Usage: bash scripts/deploy_vm.sh
# Requires: rsync, ssh, curl
# Syncs project to /opt/gni-bot-creator; if ../apps/whatsapp-bot exists, syncs it to /opt/apps/whatsapp-bot.
set -e

VM_USER="${VM_USER:-root}"
VM_HOST="${VM_HOST:-217.216.84.81}"
VM_PATH="${VM_PATH:-/opt/gni-bot-creator}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REMOTE="${VM_USER}@${VM_HOST}:${VM_PATH}"

echo "=== GNI Bot Creator â€” VM Deploy ==="
echo "  VM: ${VM_USER}@${VM_HOST}"
echo "  Path: ${VM_PATH}"
echo "  Repo: ${REPO_ROOT}"
echo ""

# 1) Rsync project to VM (exclude large/cache dirs)
echo "1) Syncing project to VM..."
rsync -avz --delete \
  --exclude='.git' \
  --exclude='__pycache__' \
  --exclude='.venv' \
  --exclude='venv' \
  --exclude='node_modules' \
  --exclude='data/postgres' \
  --exclude='data/redis' \
  --exclude='*.log' \
  --exclude='.pytest_cache' \
  --exclude='*.pyc' \
  "$REPO_ROOT/" "$REMOTE/"

# 2) whatsapp-bot is in apps/whatsapp-bot â€” synced by step 1 with node_modules/dist excluded

echo ""
echo "3) Running docker compose on VM..."
ssh "${VM_USER}@${VM_HOST}" "cd ${VM_PATH} && \
  [ -f .env ] || cp .env.example .env 2>/dev/null || true && \
  docker compose build && \
  docker compose up -d"

echo ""
echo "4) Waiting for API health (max 120s)..."
for i in $(seq 1 24); do
  if ssh "${VM_USER}@${VM_HOST}" "curl -sf http://localhost:8000/health" 2>/dev/null; then
    echo ""
    echo "5) Desk24H smoke (compose dry-run)..."
    ssh "${VM_USER}@${VM_HOST}" "cd ${VM_PATH} && docker compose exec -T api python -m desk.scheduler --dry-run --type PANORAMA_0900 --compose 2>/dev/null" || echo "Desk smoke skipped"
    echo ""
    echo "=== Deploy complete ==="
    echo "  API health: OK"
    echo "  curl http://localhost:8000/health (on VM)"
    echo "  URL: http://${VM_HOST}:8000/health"
    echo ""
    exit 0
  fi
  sleep 5
done

echo ""
echo "ERROR: Health check failed after 120s"
ssh "${VM_USER}@${VM_HOST}" "cd ${VM_PATH} && docker compose ps"
exit 1
