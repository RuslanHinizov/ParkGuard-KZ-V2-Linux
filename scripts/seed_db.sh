#!/usr/bin/env bash
# =============================================================================
# scripts/seed_db.sh — load demo camera + zone rows for local dev
# -----------------------------------------------------------------------------
# Uses event-api endpoints (step 2+) so it also exercises X-Operator-Name
# audit logging. Safe to re-run — uses idempotent PUT on fixed IDs.
# =============================================================================

set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
OPERATOR="${DEFAULT_OPERATOR_NAME:-seed-script}"

echo "Seeding demo data via $API_URL as operator '$OPERATOR'..."

curl -fsS -X PUT "$API_URL/api/v1/cameras/cam_demo" \
    -H "Content-Type: application/json" \
    -H "X-Operator-Name: $OPERATOR" \
    -d '{
        "name": "Demo Camera",
        "rtsp_substream_url": "rtsp://demo:demo@127.0.0.1:554/sub",
        "rtsp_mainstream_url": "rtsp://demo:demo@127.0.0.1:554/main",
        "location_description": "Dev fixture — not a real camera",
        "enabled": false,
        "config": {}
    }' >/dev/null && echo "  cam_demo upserted"

echo "Seed complete."
