#!/usr/bin/env bash
# GNI V1 — One-command end-to-end test runner.
#
# Usage:
#   TELEGRAM_TEST_CHAT_ID=-100TESTCHAT bash gni/tests/e2e/run_e2e.sh
#
# Safety:
#   - Requires TELEGRAM_TEST_CHAT_ID; aborts otherwise.
#   - Forces TELEGRAM_DRY_RUN unset (publisher path is exercised, but the
#     HTTP layer is mocked inside conftest — no Telegram API call leaves
#     the process under any condition).
#   - Refuses to run if TELEGRAM_TEST_CHAT_ID matches any prod-marker var.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT"

if [[ -z "${TELEGRAM_TEST_CHAT_ID:-}" ]]; then
  echo "ABORT: TELEGRAM_TEST_CHAT_ID is not set." >&2
  echo "       export TELEGRAM_TEST_CHAT_ID=<your test channel id>" >&2
  exit 2
fi

for var in TELEGRAM_CHAT_ID_PROD TELEGRAM_CHAT_ID_PRODUCTION; do
  if [[ -n "${!var:-}" && "${!var}" == "$TELEGRAM_TEST_CHAT_ID" ]]; then
    echo "ABORT: $var equals TELEGRAM_TEST_CHAT_ID — refusing to run." >&2
    exit 2
  fi
done

# Pick the project venv if present, else fall back to system python.
if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PY="$REPO_ROOT/.venv/bin/python"
else
  PY="$(command -v python3 || command -v python)"
fi
echo "[e2e] python: $PY"
echo "[e2e] test chat: $TELEGRAM_TEST_CHAT_ID"
echo "[e2e] repo: $REPO_ROOT"

exec "$PY" -m pytest gni/tests/e2e -v -ra "$@"
