"""
python-services/violation-service/src/metrics.py

Prometheus metrics registered at import time. Collectors are module-level
singletons; the health server exposes them via /metrics. New metrics land
here as adımlar 5/6/8 add features (state transitions, CVI matches,
snapshot round-trip latency).
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge

UP = Gauge(
    "parkguard_violation_up",
    "1 if the violation-service main loop is running, 0 during shutdown.",
)

DETECTIONS_CONSUMED = Counter(
    "parkguard_detections_consumed_total",
    "Objects pulled from the detections Kafka topic.",
    labelnames=("camera",),
)

MESSAGES_FAILED = Counter(
    "parkguard_violation_messages_failed_total",
    "Detection messages that could not be processed.",
    labelnames=("reason",),
)

# Placeholders populated from Adım 5 onwards.
STATE_TRANSITIONS = Counter(
    "parkguard_state_transitions_total",
    "CVI×zone state transitions.",
    labelnames=("from_state", "to_state"),
)

VIOLATIONS_CREATED = Counter(
    "parkguard_violations_created_total",
    "Rows inserted into the violations table.",
    labelnames=("camera", "zone"),
)

DUPLICATES_PREVENTED = Counter(
    "parkguard_duplicates_prevented_total",
    "Would-be duplicates blocked by a specific defense layer.",
    labelnames=("layer",),   # layer ∈ {cvi, redis, exit_confirm, db_unique}
)

OCR_RESULTS_APPLIED = Counter(
    "parkguard_ocr_results_applied_total",
    "ocr_results messages folded back into a CVI's plate_votes.",
    labelnames=("outcome",),  # stabilised | vote_only | no_cvi | invalid
)

CVI_ACTIVE = Gauge(
    "parkguard_cvi_active",
    "Number of CVI records currently held in the in-memory registry "
    "(across all cameras). Updated after each eviction cycle.",
)

CVI_EVICTIONS = Counter(
    "parkguard_cvi_evictions_total",
    "CVIs removed by the periodic idle-timeout eviction loop.",
)

__all__ = [
    "CVI_ACTIVE",
    "CVI_EVICTIONS",
    "DETECTIONS_CONSUMED",
    "DUPLICATES_PREVENTED",
    "MESSAGES_FAILED",
    "OCR_RESULTS_APPLIED",
    "STATE_TRANSITIONS",
    "UP",
    "VIOLATIONS_CREATED",
]
