#!/usr/bin/env bash
# Build deploy archive. Run from repo root (parent of deploy/).
# Output: deploy_bundle.tar.gz (and deploy_bundle.zip if zip available).
# Contents: deploy/ folder so that after extract you have deploy/docker-compose.prod.yml, etc.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Repo root = parent of deploy/
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

OUTPUT_NAME="deploy_bundle"
TAR_FILE="${OUTPUT_NAME}.tar.gz"

if [ ! -d deploy ]; then
  echo "Missing deploy/ directory. Run from repo root."
  exit 1
fi

echo "Creating $TAR_FILE from deploy/..."
tar -czf "$TAR_FILE" deploy
echo "Created: $REPO_ROOT/$TAR_FILE ($(du -h "$TAR_FILE" | cut -f1))"

if command -v zip >/dev/null 2>&1; then
  ZIP_FILE="${OUTPUT_NAME}.zip"
  echo "Creating $ZIP_FILE..."
  zip -r -q "$ZIP_FILE" deploy
  echo "Created: $REPO_ROOT/$ZIP_FILE ($(du -h "$ZIP_FILE" | cut -f1))"
fi

echo "To deploy: extract on VM (e.g. tar -xzf $TAR_FILE -C /opt/gni-bot-creator) then copy deploy/.env.example to .env and run ./deploy/scripts/start.sh from repo root."
