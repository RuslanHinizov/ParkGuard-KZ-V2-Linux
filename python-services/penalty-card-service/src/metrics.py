"""
python-services/penalty-card-service/src/metrics.py

Prometheus metric primitives for the penalty-card-service.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

UP = Gauge("penalty_card_up", "1 if service is ready")

CARDS_GENERATED = Counter(
    "penalty_card_generated_total",
    "Penalty PDFs successfully generated and uploaded",
    ["camera"],
)

CARD_FAILURES = Counter(
    "penalty_card_failures_total",
    "PDF generation / upload failures",
    ["reason"],
)

RENDER_LATENCY = Histogram(
    "penalty_card_render_latency_seconds",
    "Time from consume to MinIO upload (full round-trip)",
    buckets=(0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0),
)

PDF_BYTES = Histogram(
    "penalty_card_pdf_bytes",
    "Generated PDF size in bytes",
    buckets=(20_000, 50_000, 100_000, 250_000, 500_000),
)

__all__ = [
    "CARDS_GENERATED",
    "CARD_FAILURES",
    "PDF_BYTES",
    "RENDER_LATENCY",
    "UP",
]
