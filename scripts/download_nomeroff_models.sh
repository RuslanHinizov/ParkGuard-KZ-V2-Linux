#!/usr/bin/env bash
# =============================================================================
# scripts/download_nomeroff_models.sh
# -----------------------------------------------------------------------------
# Pre-caches Nomeroff-net 4.0.1 weights into the plate-service image layer by
# invoking the pipeline once with OpenCV as image loader. Run from a machine
# with network access + enough free disk (~2 GB for all regions).
#
# In prod this runs during the plate-service image build (see plate-service
# Dockerfile); as a standalone script it also works for dev caches.
# =============================================================================

set -euo pipefail

if ! command -v python >/dev/null && ! command -v python3 >/dev/null; then
    echo "python/python3 not found"
    exit 1
fi

PY=$(command -v python3 || command -v python)
CACHE_DIR="${NOMEROFF_CACHE_DIR:-$HOME/.cache/nomeroff-net}"
mkdir -p "$CACHE_DIR"

echo "Caching Nomeroff-net 4.0.1 weights to: $CACHE_DIR"

"$PY" -m pip install --quiet --disable-pip-version-check "nomeroff-net==4.0.1"

"$PY" - <<'PY'
from nomeroff_net import pipeline

p = pipeline(
    "number_plate_detection_and_reading",
    image_loader="opencv",
)
print("models cached")
PY

echo "Done."
