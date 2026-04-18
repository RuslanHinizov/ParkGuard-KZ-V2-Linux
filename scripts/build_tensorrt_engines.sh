#!/usr/bin/env bash
# =============================================================================
# scripts/build_tensorrt_engines.sh — HOST-side TensorRT engine build
# -----------------------------------------------------------------------------
# RTX 5070 Ti (Blackwell, sm_120) requires TensorRT ≥ 10.14.1.48 on the
# HOST machine (spec §7.4, §15.6). Engines built on other architectures
# will NOT load on this GPU. Never build engines inside a container
# without GPU access.
#
# Produces:
#   cpp-deepstream/models/yolo11s_fp16.engine
#   cpp-deepstream/models/osnet_x0_25_veh_fp16.engine
# =============================================================================

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
MODELS_DIR="$ROOT/cpp-deepstream/models"

YOLO_ONNX="$MODELS_DIR/yolo11s.onnx"
YOLO_ENGINE="$MODELS_DIR/yolo11s_fp16.engine"

OSNET_ONNX="$MODELS_DIR/osnet_x0_25_veh.onnx"
OSNET_ENGINE="$MODELS_DIR/osnet_x0_25_veh_fp16.engine"

# -------- Pre-flight --------
if ! command -v trtexec >/dev/null; then
    echo "trtexec not found. Install TensorRT 10.14+ on the host:"
    echo "  https://developer.nvidia.com/tensorrt-download"
    exit 1
fi

TRTEXEC_VER=$(trtexec --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo "unknown")
echo "TensorRT trtexec version: $TRTEXEC_VER"

if ! command -v nvidia-smi >/dev/null; then
    echo "nvidia-smi not found — GPU drivers missing."
    exit 1
fi
GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader,nounits | head -1)
COMPUTE_CAP=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader,nounits | head -1)
echo "GPU: $GPU_NAME (compute capability $COMPUTE_CAP)"

if [[ "$COMPUTE_CAP" != "12.0" && "$COMPUTE_CAP" != "9.0" && "$COMPUTE_CAP" != "8.9" ]]; then
    echo "Warning: compute capability $COMPUTE_CAP not explicitly tested (target sm_120 Blackwell)."
fi

mkdir -p "$MODELS_DIR"

# -------- YOLO11s --------
if [[ ! -f "$YOLO_ONNX" ]]; then
    echo "Missing $YOLO_ONNX — download YOLO11s ONNX and place it there."
    echo "  Export from Ultralytics: yolo export model=yolo11s.pt format=onnx opset=17 imgsz=640"
    exit 1
fi

if [[ -f "$YOLO_ENGINE" && "$YOLO_ENGINE" -nt "$YOLO_ONNX" ]]; then
    echo "YOLO engine up to date — skip."
else
    echo "Building YOLO11s FP16 engine..."
    trtexec \
        --onnx="$YOLO_ONNX" \
        --saveEngine="$YOLO_ENGINE" \
        --fp16 \
        --builderOptimizationLevel=5 \
        --memPoolSize=workspace:2048 \
        --verbose
fi

# -------- OSNet ReID --------
if [[ ! -f "$OSNET_ONNX" ]]; then
    echo "Missing $OSNET_ONNX — fetch OSNet_x0_25 veh-reid ONNX and place it there."
    echo "  See: https://github.com/KaiyangZhou/deep-person-reid (veh-reid variant)"
    exit 1
fi

if [[ -f "$OSNET_ENGINE" && "$OSNET_ENGINE" -nt "$OSNET_ONNX" ]]; then
    echo "OSNet engine up to date — skip."
else
    echo "Building OSNet_x0_25 FP16 engine..."
    trtexec \
        --onnx="$OSNET_ONNX" \
        --saveEngine="$OSNET_ENGINE" \
        --fp16 \
        --builderOptimizationLevel=5 \
        --memPoolSize=workspace:1024 \
        --verbose
fi

echo "Engines built:"
ls -lh "$MODELS_DIR"/*.engine
