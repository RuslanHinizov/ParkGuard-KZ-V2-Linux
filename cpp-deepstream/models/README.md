# DeepStream model artifacts

The TensorRT engines (`*.engine`) are **not** checked in — they are GPU
architecture-bound (sm_120 for RTX 5070 Ti Blackwell) and must be built on
the host per spec §7.4 / §15.6.

Expected contents (Adım 3):

```
yolo11s.onnx            # YOLO11s exported at 640x640 FP32 via Ultralytics
yolo11s_fp16.engine     # built by scripts/build_tensorrt_engines.sh
labels_yolo11s.txt      # symlink to ../configs/labels_yolo11s.txt (COCO 80)
```

Added in Adım 6 (SGIE ReID):

```
osnet_x0_25_veh.onnx
osnet_x0_25_veh_fp16.engine
```

Build the engines:

```bash
scripts/download_nomeroff_models.sh       # unrelated — plate-service
scripts/build_tensorrt_engines.sh         # YOLO11s + OSNet FP16
```

Verify:

```bash
ls -lh cpp-deepstream/models/*.engine
```
