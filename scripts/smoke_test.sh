#!/usr/bin/env bash
# =============================================================================
# scripts/smoke_test.sh — ParkGuard KZ dev smoke test
# -----------------------------------------------------------------------------
# Verifies every infrastructure service is reachable and functional.
# Intended to run on the Ubuntu host after `docker compose up -d`.
#
# Exit codes:
#   0  all checks passed
#   1  one or more checks failed
# =============================================================================

set -euo pipefail

# ---- Colors ----
R=$'\033[31m' G=$'\033[32m' Y=$'\033[33m' B=$'\033[1m' N=$'\033[0m'
ok()     { echo "${G}[OK]${N} $*"; }
warn()   { echo "${Y}[WARN]${N} $*"; }
fail()   { echo "${R}[FAIL]${N} $*"; FAILURES=$((FAILURES+1)); }
step()   { echo ""; echo "${B}==> $*${N}"; }

FAILURES=0

# ---- .env loading ----
if [[ -f .env ]]; then
    # shellcheck disable=SC1091
    set -a; source .env; set +a
fi

POSTGRES_HOST_LOCAL="${POSTGRES_HOST_LOCAL:-localhost}"
REDIS_HOST_LOCAL="${REDIS_HOST_LOCAL:-localhost}"
KAFKA_HOST_LOCAL="${KAFKA_HOST_LOCAL:-localhost}"
MINIO_HOST_LOCAL="${MINIO_HOST_LOCAL:-localhost}"
API_HOST_LOCAL="${API_HOST_LOCAL:-localhost}"

# ---- 1. docker compose up -d state ----
step "docker compose services"
if ! command -v docker >/dev/null; then
    fail "docker not in PATH"
    exit 1
fi
docker compose ps --status running --format "  {{.Service}}  {{.Status}}" || true
SERVICE_COUNT=$(docker compose ps --status running --format '{{.Service}}' 2>/dev/null | wc -l | tr -d ' ')
if [[ "$SERVICE_COUNT" -lt 4 ]]; then
    fail "Expected ≥4 running services, got $SERVICE_COUNT"
else
    ok "$SERVICE_COUNT services running"
fi

# ---- 2. PostgreSQL (+PostGIS) ----
step "PostgreSQL (+PostGIS)"
if PGPASSWORD="$POSTGRES_PASSWORD" docker compose exec -T postgres \
      psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT 1" >/dev/null 2>&1; then
    ok "postgres accepts connections"
else
    fail "postgres not reachable"
fi

if PGPASSWORD="$POSTGRES_PASSWORD" docker compose exec -T postgres \
      psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
      "SELECT extversion FROM pg_extension WHERE extname='postgis'" 2>/dev/null \
      | grep -qE '^[0-9]'; then
    ok "postgis extension installed"
else
    warn "postgis not yet installed — run 'make migrate'"
fi

# ---- 3. Redis ----
step "Redis"
if docker compose exec -T redis \
      redis-cli -a "$REDIS_PASSWORD" --no-auth-warning ping 2>/dev/null | grep -q PONG; then
    ok "redis PONG"
else
    fail "redis not reachable"
fi

# Check AOF is enabled (spec §4.4 state persistence).
if docker compose exec -T redis \
      redis-cli -a "$REDIS_PASSWORD" --no-auth-warning config get appendonly 2>/dev/null \
      | grep -q yes; then
    ok "redis AOF enabled"
else
    fail "redis AOF is NOT enabled — state loss on restart"
fi

# ---- 4. Kafka + topics ----
step "Kafka + topics"
if docker compose exec -T kafka \
      kafka-broker-api-versions --bootstrap-server localhost:9092 >/dev/null 2>&1; then
    ok "kafka broker alive"
else
    fail "kafka broker unreachable"
fi

EXPECTED_TOPICS=(detections violations snapshot_requests snapshot_responses ocr_requests ocr_results penalty_card_requests)
ACTUAL_TOPICS=$(docker compose exec -T kafka \
                kafka-topics --bootstrap-server localhost:9092 --list 2>/dev/null || echo "")
for t in "${EXPECTED_TOPICS[@]}"; do
    if echo "$ACTUAL_TOPICS" | grep -qx "$t"; then
        ok "topic: $t"
    else
        fail "topic missing: $t  (check kafka-init container logs)"
    fi
done

# ---- 5. MinIO ----
step "MinIO"
if curl -fs "http://${MINIO_HOST_LOCAL}:9000/minio/health/live" >/dev/null; then
    ok "minio API live"
else
    fail "minio API not reachable"
fi

for b in "${MINIO_BUCKET_SNAPSHOTS:-snapshots}" "${MINIO_BUCKET_CARDS:-penalty-cards}"; do
    if docker compose exec -T minio \
          sh -c "mc alias set local http://localhost:9000 $MINIO_ROOT_USER $MINIO_ROOT_PASSWORD >/dev/null 2>&1 && mc ls local/$b >/dev/null 2>&1"; then
        ok "bucket: $b"
    else
        warn "bucket missing: $b (check minio-init)"
    fi
done

# ---- 6. event-api ----
step "event-api"
if curl -fs "http://${API_HOST_LOCAL}:8000/healthz" >/dev/null 2>&1; then
    ok "event-api /healthz"
    if curl -fs "http://${API_HOST_LOCAL}:8000/readyz" | grep -qE '"status":"(ok|degraded)"'; then
        ok "event-api /readyz"
    else
        warn "event-api /readyz not ready"
    fi
else
    warn "event-api not reachable (expected in Adım 1 if not yet built)"
fi

# ---- Summary ----
echo ""
if [[ $FAILURES -eq 0 ]]; then
    echo "${G}${B}SMOKE TEST: ALL CRITICAL CHECKS PASSED${N}"
    exit 0
else
    echo "${R}${B}SMOKE TEST: $FAILURES critical check(s) failed${N}"
    exit 1
fi
