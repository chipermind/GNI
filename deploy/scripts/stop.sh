#!/usr/bin/env bash
# Stop all services. Run from repo root.

set -e
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

docker compose -f deploy/docker-compose.prod.yml --project-directory . down
echo "Stopped."
