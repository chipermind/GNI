#!/usr/bin/env bash
# Quick VM smoke: containers, API health, desk dry-run, radar dry-run.
# Run ON THE VM: cd /opt/gni-bot-creator && bash scripts/run_tests_vm.sh
set -e

cd "$(dirname "$0")/.."
FAILED=0

_pass() { echo "  PASS: $1"; }
_fail() { echo "  FAIL: $1"; FAILED=1; }

echo "=== VM tests ==="
echo ""

# 1) Containers
echo "1) Containers"
if docker compose ps 2>/dev/null | grep -qE "Exit [1-9]"; then
  _fail "Some container exited with error"
  docker compose ps
else
  _pass "No exited containers"
fi
echo ""

# 2) API health (if api is running)
echo "2) API health"
if docker compose ps 2>/dev/null | grep -q "api"; then
  if docker compose exec -T api curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
    _pass "API /health"
  else
    _fail "API /health"
  fi
else
  echo "  (api not in stack, skip)"
fi
echo ""

# 3) Desk dry-run (FLOW_1330)
echo "3) Desk FLOW_1330 dry-run"
if docker compose ps 2>/dev/null | grep -q "api"; then
  OUT=$(docker compose exec -T api python -m desk.scheduler --dry-run --type FLOW_1330 --compose 2>&1) || true
  echo "$OUT" | tail -3
  if echo "$OUT" | grep -q '"ok": true'; then
    _pass "Desk compose"
  else
    _fail "Desk compose (ok!=true)"
  fi
else
  echo "  (api not in stack, skip)"
fi
echo ""

# 4) Radar format dry-run (worker)
echo "4) Radar format dry-run (worker)"
if docker compose ps 2>/dev/null | grep -q "worker"; then
  if docker compose exec -T worker python scripts/send_radar_messages.py --count 1 2>&1 | grep -qE "dry-run|SENT|chars"; then
    _pass "Radar script"
  else
    _fail "Radar script"
  fi
else
  echo "  (worker not in stack, skip)"
fi
echo ""

if [ "$FAILED" -eq 0 ]; then
  echo "=== All VM tests OK ==="
  exit 0
else
  echo "=== Some tests failed ==="
  exit 1
fi
