#!/usr/bin/env bash
# =============================================================================
# scripts/backup_db.sh — nightly pg_dump of ParkGuard KZ PostgreSQL
# -----------------------------------------------------------------------------
# Intended to run from cron on the Ubuntu host, e.g.:
#   15 2 * * *  /opt/parkguard/scripts/backup_db.sh >>/var/log/parkguard-backup.log 2>&1
#
# Produces a custom-format (pg_restore-compatible) dump that retains PostGIS
# geometry columns. Keeps the last $BACKUP_KEEP_DAYS (default 14) days.
# =============================================================================

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

# ---- Load .env so we get DB creds without hardcoding ----
if [[ -f "$ROOT/.env" ]]; then
    # shellcheck disable=SC1091
    set -a; source "$ROOT/.env"; set +a
fi

BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$BACKUP_DIR/parkguard-${TIMESTAMP}.dump"

mkdir -p "$BACKUP_DIR"

echo "[backup_db] dumping $POSTGRES_DB -> $OUT"
PGPASSWORD="$POSTGRES_PASSWORD" docker compose exec -T postgres \
    pg_dump \
        --username "$POSTGRES_USER" \
        --dbname "$POSTGRES_DB" \
        --format=custom \
        --no-owner \
        --compress=6 \
    > "$OUT"

echo "[backup_db] wrote $(du -h "$OUT" | cut -f1)"

# ---- Rotate ----
echo "[backup_db] pruning backups older than ${BACKUP_KEEP_DAYS} days"
find "$BACKUP_DIR" -maxdepth 1 -name 'parkguard-*.dump' -type f \
    -mtime "+${BACKUP_KEEP_DAYS}" -print -delete || true

echo "[backup_db] done."
