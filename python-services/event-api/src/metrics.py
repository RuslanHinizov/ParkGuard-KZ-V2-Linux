"""
python-services/event-api/src/metrics.py

Prometheus metrics for the event-api — module-level singletons.
Registered at import time; exposed via prometheus-fastapi-instrumentator
at /metrics (see main.py create_app).

HTTP request/latency histograms are added automatically by the
instrumentator. Custom metrics here cover business-level signals.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge

EVENT_API_UP = Gauge(
    "parkguard_event_api_up",
    "1 if the event-api process is running and accepting requests.",
)

WS_CONNECTIONS_ACTIVE = Gauge(
    "parkguard_event_api_ws_connections_active",
    "Number of currently-connected WebSocket (dashboard) clients.",
)

CAMERAS_TOTAL = Gauge(
    "parkguard_cameras_total",
    "Total number of camera rows in the database.",
)

ZONES_TOTAL = Gauge(
    "parkguard_zones_total",
    "Total number of zone rows in the database.",
)

VIOLATIONS_PATCHED = Counter(
    "parkguard_event_api_violations_patched_total",
    "Status transitions applied via the REST PATCH endpoint.",
    labelnames=("new_status",),
)

PENALTY_CARDS_TRIGGERED = Counter(
    "parkguard_event_api_penalty_cards_triggered_total",
    "Penalty-card Kafka messages enqueued from the /penalty-card endpoint.",
)

__all__ = [
    "CAMERAS_TOTAL",
    "EVENT_API_UP",
    "PENALTY_CARDS_TRIGGERED",
    "VIOLATIONS_PATCHED",
    "WS_CONNECTIONS_ACTIVE",
    "ZONES_TOTAL",
]
