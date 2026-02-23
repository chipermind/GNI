#!/usr/bin/env bash
# Desk24H deploy smoke — run inside VM (or docker compose exec api).
# Validates: compose dry-run produces output, DB saved snapshot + post.
# No Telegram. Requires: Ollama reachable (e.g. ollama:11434).
# Usage: bash scripts/desk_deploy_smoke_vm.sh
#        docker compose exec -T api bash scripts/desk_deploy_smoke_vm.sh
set -e

DESK_TYPE="${DESK_SMOKE_TYPE:-PANORAMA_0900}"
FAILED=0

echo "=== Desk24H deploy smoke ==="
echo "  DESK_TYPE=$DESK_TYPE"
echo "  DESK24H_ENABLED=${DESK24H_ENABLED:-0}"
echo ""

# 1) Compose dry-run (generates 1 message, saves to SQLite, no Telegram)
echo "1) Running compose dry-run..."
OUT=$(python -m desk.scheduler --dry-run --type "$DESK_TYPE" --compose 2>&1) || true
echo "$OUT"

if echo "$OUT" | grep -q '"ok": true'; then
  echo "[PASS] Compose returned ok=true"
else
  echo "[WARN] Compose may have failed (ok!=true or validation failed)"
fi

# 2) Check DB: snapshot + post saved
echo ""
echo "2) Checking DB (snapshot + post)..."
PY_CHECK='
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from desk.storage import init_db, get_conn

init_db()
with get_conn() as c:
    r = c.execute("SELECT COUNT(*) FROM snapshots").fetchone()
    snap_count = r[0] if r else 0
    r = c.execute("SELECT COUNT(*) FROM posts").fetchone()
    post_count = r[0] if r else 0
print(f"snapshots={snap_count} posts={post_count}")
if snap_count >= 1 and post_count >= 1:
    sys.exit(0)
sys.exit(1)
'
if python -c "$PY_CHECK" 2>/dev/null; then
  echo "[PASS] DB has snapshot and post"
else
  echo "[FAIL] DB missing snapshot or post"
  FAILED=1
fi

# 3) Check meta for blocked_claims, used_sources (when present)
echo ""
echo "3) Last post meta (blocked_claims, used_sources)..."
PY_META='
import sys, json
try:
    from desk.storage import init_db, get_conn
except ImportError:
    from pathlib import Path
    sys.path.insert(0, str(Path.cwd()))
    from desk.storage import init_db, get_conn

init_db()
with get_conn() as c:
    r = c.execute("SELECT meta_json FROM posts ORDER BY created_at DESC LIMIT 1").fetchone()
if r and r[0]:
    m = json.loads(r[0])
    print("  blocked_claims:", m.get("blocked_claims", "N/A"))
    print("  used_sources:", m.get("used_sources", "N/A"))
else:
    print("  (no posts)")
'
python -c "$PY_META" 2>/dev/null || true

echo ""
if [ "$FAILED" -eq 0 ]; then
  echo "=== Desk24H smoke: PASS ==="
  exit 0
else
  echo "=== Desk24H smoke: FAIL ==="
  exit 1
fi
