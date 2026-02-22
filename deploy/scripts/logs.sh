#!/usr/bin/env bash
# Tail logs. Run from repo root.
# Usage: ./deploy/scripts/logs.sh [service]
#   No args: all services. Else: api, worker, whatsapp-bot, wa-qr-ui, postgres, redis, ollama, collector

set -e
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

if [ -n "$1" ]; then
  docker compose -f deploy/docker-compose.prod.yml --project-directory . logs -f "$1"
else
  docker compose -f deploy/docker-compose.prod.yml --project-directory . logs -f
fi
