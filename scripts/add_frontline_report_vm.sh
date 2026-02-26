#!/usr/bin/env bash
# Add Frontline Report (and optionally other channels) to Telegram ingest on the VM.
# Usage on VM:
#   cd /opt/gni-bot-creator
#   bash scripts/add_frontline_report_vm.sh
# (adds Frontline Report using username frontlinelive; or pass chat_id/username as first arg)
# Or: bash scripts/add_frontline_report_vm.sh -1001991611234
# Or: bash scripts/add_frontline_report_vm.sh "Frontline Report:-100xxx,Other:-100yyy"
set -e

cd "$(dirname "$0")/.."

# Default: add Frontline Report by public username (t.me/frontlinelive)
if [ -z "$1" ]; then
  SOURCES="Frontline Report:frontlinelive"
  echo "Adding default: Frontline Report (username: frontlinelive)"
else
  # If single argument looks like a raw ID (starts with -100), treat as "Frontline Report:ID"
  if [[ "$1" =~ ^-100[0-9]+$ ]]; then
    SOURCES="Frontline Report:$1"
  else
    SOURCES="$1"
  fi
fi

echo "Adding Telegram source(s): $SOURCES"
docker compose exec -e TELEGRAM_SOURCES="$SOURCES" collector python scripts/add_telegram_sources.py
echo "Done. Collector will fetch from the new channel(s) on next run."
