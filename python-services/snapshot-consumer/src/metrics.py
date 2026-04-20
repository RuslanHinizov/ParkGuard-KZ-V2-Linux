"""
python-services/snapshot-consumer/src/metrics.py

Prometheus metric primitives for the snapshot-consumer.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

UP = Gauge("snapshot_consumer_up", "1 if service is ready")

UPLOADS_TOTAL = Counter(
    "snapshot_consumer_uploads_total",
    "Snapshots successfully uploaded to MinIO",
    ["camera"],
)

UPLOAD_FAILURES_TOTAL = Counter(
    "snapshot_consumer_upload_failures_total",
    "Upload attempts that terminated in a failure",
    ["reason"],
)

MISSING_FILES_TOTAL = Counter(
    "snapshot_consumer_missing_files_total",
    "snapshot_responses whose local JPEG file was absent",
)

STATUS_FILTERED_TOTAL = Counter(
    "snapshot_consumer_status_filtered_total",
    "snapshot_responses dropped before upload (non-ok status)",
    ["status"],
)

UPLOAD_LATENCY = Histogram(
    "snapshot_consumer_upload_latency_seconds",
    "Time from consume to MinIO ack",
    buckets=(0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.5, 5.0),
)

UPLOAD_BYTES = Histogram(
    "snapshot_consumer_upload_bytes",
    "JPEG size uploaded",
    buckets=(10_000, 50_000, 100_000, 250_000, 500_000, 1_000_000, 2_500_000),
)

__all__ = [
    "MISSING_FILES_TOTAL",
    "STATUS_FILTERED_TOTAL",
    "UP",
    "UPLOADS_TOTAL",
    "UPLOAD_BYTES",
    "UPLOAD_FAILURES_TOTAL",
    "UPLOAD_LATENCY",
]
