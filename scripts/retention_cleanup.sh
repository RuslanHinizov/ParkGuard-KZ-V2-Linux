#!/usr/bin/env bash
# =============================================================================
# scripts/retention_cleanup.sh — enforce spec §12.4 retention rules
# -----------------------------------------------------------------------------
# Removes:
#   * violation snapshots older than VIOLATION_SNAPSHOT_RETENTION_DAYS (90)
#   * penalty-card PDFs older than PENALTY_CARD_RETENTION_DAYS (365)
#   * audit-log rows older than AUDIT_LOG_RETENTION_DAYS (730)
#
# Runs daily at 03:30 local time by default (see systemd/cron unit).
# Idempotent; safe to re-run.
# =============================================================================

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

if [[ -f "$ROOT/.env" ]]; then
    # shellcheck disable=SC1091
    set -a; source "$ROOT/.env"; set +a
fi

SNAP_DAYS="${VIOLATION_SNAPSHOT_RETENTION_DAYS:-90}"
CARD_DAYS="${PENALTY_CARD_RETENTION_DAYS:-365}"
AUDIT_DAYS="${AUDIT_LOG_RETENTION_DAYS:-730}"

SNAP_BUCKET="${MINIO_BUCKET_SNAPSHOTS:-snapshots}"
CARD_BUCKET="${MINIO_BUCKET_CARDS:-penalty-cards}"

echo "[retention] snapshots > ${SNAP_DAYS}d, cards > ${CARD_DAYS}d, audit > ${AUDIT_DAYS}d"

# ---- MinIO object retention via mc ilm or hand-prune ----
docker compose exec -T minio sh -c "
    mc alias set local http://localhost:9000 '$MINIO_ROOT_USER' '$MINIO_ROOT_PASSWORD' >/dev/null
    mc ilm rule add --expire-days '${SNAP_DAYS}' local/${SNAP_BUCKET} 2>/dev/null || true
    mc ilm rule add --expire-days '${CARD_DAYS}' local/${CARD_BUCKET} 2>/dev/null || true
    mc ilm rule ls local/${SNAP_BUCKET} || true
    mc ilm rule ls local/${CARD_BUCKET} || true
"

# ---- Audit-log truncation ----
PGPASSWORD="$POSTGRES_PASSWORD" docker compose exec -T postgres \
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 <<SQL
DELETE FROM audit_log
WHERE created_at < NOW() - INTERVAL '${AUDIT_DAYS} days';
SQL

echo "[retention] done."
