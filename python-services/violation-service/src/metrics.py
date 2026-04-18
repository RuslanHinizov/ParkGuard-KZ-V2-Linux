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

__all__ = [
    "DETECTIONS_CONSUMED",
    "DUPLICATES_PREVENTED",
    "MESSAGES_FAILED",
    "STATE_TRANSITIONS",
    "UP",
    "VIOLATIONS_CREATED",
]
