#!/usr/bin/env sh
# Run radar-format message send inside the worker container. For use on the VM with cron.
# Ensure .env has TELEGRAM_BOT_TOKEN and TELEGRAM_TARGET_CHAT_ID (or TELEGRAM_CHAT_ID).
#
# Usage:
#   ./scripts/run_radar_send.sh           # 1 message, send to Telegram
#   ./scripts/run_radar_send.sh 3         # 3 messages
#
# Cron example (every 6 hours):
#   0 */6 * * * /opt/gni-bot-creator/scripts/run_radar_send.sh >> /var/log/gni-radar.log 2>&1

set -e
cd "$(dirname "$0")/.."
COUNT="${1:-1}"
docker compose exec -T worker python scripts/send_radar_messages.py --count "$COUNT" --send
