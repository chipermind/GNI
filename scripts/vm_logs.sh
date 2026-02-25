#!/usr/bin/env bash
# Show Docker status and worker (and key service) logs. Run on VM: bash scripts/vm_logs.sh
# Optional: bash scripts/vm_logs.sh worker 200   (service name and line count)
set -e

cd "$(dirname "$0")/.."
SERVICE="${1:-worker}"
LINES="${2:-120}"

echo "=== docker compose ps ==="
docker compose ps
echo ""

echo "=== logs: $SERVICE (last $LINES lines) ==="
docker compose logs "$SERVICE" --tail "$LINES"
echo ""

if [ "$SERVICE" = "worker" ]; then
  echo "=== ollama (last 30 lines, in case worker fails on Ollama) ==="
  docker compose logs ollama --tail 30 2>/dev/null || true
fi
