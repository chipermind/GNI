#!/usr/bin/env bash
set -e
cd /opt/gni-bot-creator

echo "=== Pull latest (get desk, migrate_db) ==="
git pull origin main 2>/dev/null || git pull origin master 2>/dev/null || true

echo ""
echo "=== Rebuild API (includes desk module) ==="
docker compose build api

echo ""
echo "=== Restart services ==="
docker compose up -d

echo ""
echo "=== Wait for API (15s) ==="
sleep 15

echo ""
echo "=== Migrate desk DB ==="
docker compose exec -T api python -c 'from desk.storage import init_db, DB_PATH; init_db(); print("migrate ok")'

echo ""
echo "=== Smoke desk ==="
docker compose exec -T api python -m desk.scheduler --dry-run --type PANORAMA_0900

echo ""
echo "=== Container status ==="
docker compose ps

echo ""
echo "=== Health ==="
curl -sf http://localhost:8000/health && echo "" || echo "Health failed"
