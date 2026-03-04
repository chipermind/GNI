#!/usr/bin/env bash
# Smoke test: run run_once LONG and SHORT in dry-run. Exit 0 only if both succeed.
# Run from repo root: ./deploy/scripts/smoke_run_once.sh
# Optional: COMPOSE_FILE and PROJECT_DIR (default: deploy/docker-compose.prod.yml and .)

set -e
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_FILE="${COMPOSE_FILE:-deploy/docker-compose.prod.yml}"
PROJECT_DIR="${PROJECT_DIR:-.}"

run() {
  docker compose -f "$COMPOSE_FILE" --project-directory "$PROJECT_DIR" run --rm worker python -m gni.run_once --job "$1" --dry-run
}

echo "Smoke: run_once LONG (briefing_0900) dry-run..."
run "briefing_0900"
echo "Smoke: run_once SHORT (radar_interval) dry-run..."
run "radar_interval"
echo "Smoke OK: both run_once dry-runs succeeded. Expect format_mode= and (for LONG) split_parts= in logs."
