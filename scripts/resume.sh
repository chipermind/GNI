#!/usr/bin/env bash
# Resume GNI pipeline. Source .env and POST to /control/resume.
# Usage: bash scripts/resume.sh (on VM)
# Or via SSH: ssh gni-vm "bash /opt/gni-bot-creator/scripts/resume.sh"
set -e
cd "$(dirname "$0")/.."
source .env 2>/dev/null || true
curl -s -X POST -H "X-API-Key: $API_KEY" http://localhost:8000/control/resume
