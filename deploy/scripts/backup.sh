#!/usr/bin/env bash
# Backup Postgres and named volumes. Run from repo root.
# Creates ./backups/gni_YYYYMMDD_HHMMSS.sql and optionally a tarball of volume data.
# Requires .env with POSTGRES_PASSWORD. BACKUP_RETENTION (default 7) keeps last N dumps.

set -e
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
mkdir -p backups

BACKUP_RETENTION="${BACKUP_RETENTION:-7}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
SQL_FILE="backups/gni_${TIMESTAMP}.sql"

# Run pg_dump inside postgres container (env already set there)
CONTAINER=$(docker compose -f deploy/docker-compose.prod.yml --project-directory . ps -q postgres 2>/dev/null || true)
if [ -z "$CONTAINER" ]; then
  echo "Postgres container not running. Start stack first: ./deploy/scripts/start.sh"
  exit 1
fi

docker exec "$CONTAINER" pg_dump -U "${POSTGRES_USER:-gni}" -d "${POSTGRES_DB:-gni}" -Fp > "$SQL_FILE"
echo "Backup: $SQL_FILE ($(du -h "$SQL_FILE" | cut -f1))"

# Retention
cd backups
ls -t gni_*.sql 2>/dev/null | tail -n +$((BACKUP_RETENTION + 1)) | xargs -r rm -f
echo "Retention: kept last $BACKUP_RETENTION backups"

# Optional: export named volumes (postgres_data, redis_data, wa_auth) to tarball
# VOL_BACKUP="backups/volumes_${TIMESTAMP}.tar.gz"
# docker run --rm -v postgres_data:/pg -v redis_data:/rd -v wa_auth:/wa -v "$(pwd)/backups:/out" alpine tar -czf /out/volumes_${TIMESTAMP}.tar.gz -C /pg . -C /rd . -C /wa .
# echo "Volumes: $VOL_BACKUP"
