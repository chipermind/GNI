#!/usr/bin/env bash
# Start all services. Run from repo root.
# Usage: ./deploy/scripts/start.sh   (from repo root, with .env in repo root)

set -e
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

if [ ! -f .env ]; then
  echo "Missing .env. Copy deploy/.env.example to .env and set POSTGRES_PASSWORD, JWT_SECRET, API_KEY, WA_QR_BRIDGE_TOKEN, etc."
  exit 1
fi

docker compose -f deploy/docker-compose.prod.yml --project-directory . up -d --build
echo "Started. API: http://localhost:${API_PORT:-8000}  wa-qr-ui: http://localhost:8501  whatsapp-bot: http://localhost:3100"
