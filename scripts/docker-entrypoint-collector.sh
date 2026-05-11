#!/bin/sh
# Entrypoint for collector on Railway.
# Reconstructs the Telethon session file from TELETHON_SESSION_B64_1 + _2 env vars.
set -e

SESSION_PATH="${TELETHON_SESSION_PATH:-/data/telethon/session}"
SESSION_FILE="${SESSION_PATH}.session"

if [ -n "$TELETHON_SESSION_B64_1" ] && [ ! -f "$SESSION_FILE" ]; then
    echo "[entrypoint] Restoring Telethon session to $SESSION_FILE"
    mkdir -p "$(dirname "$SESSION_FILE")"
    printf '%s%s%s' "$TELETHON_SESSION_B64_1" "${TELETHON_SESSION_B64_2:-}" "${TELETHON_SESSION_B64_3:-}" \
        | base64 -d \
        | gunzip > "$SESSION_FILE"
    chmod 600 "$SESSION_FILE"
    echo "[entrypoint] Session restored ($(wc -c < "$SESSION_FILE") bytes)"
fi

exec "$@"
