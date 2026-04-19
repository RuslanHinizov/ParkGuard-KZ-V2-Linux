"""
python-services/plate-service/src/metrics.py

Prometheus metrics for plate-service. Scraped on `:9300/metrics`.

Counter / histogram inventory matches spec §12 observability table:

    parkguard_plate_ocr_requests_total{region=...}       counter
    parkguard_plate_ocr_results_total{format=...,valid=...}  counter
    parkguard_plate_ocr_rejected_total{reason=...}       counter
    parkguard_plate_ocr_latency_seconds                  histogram
    parkguard_plate_up                                   gauge
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

OCR_REQUESTS: Counter = Counter(
    "parkguard_plate_ocr_requests_total",
    "OCR requests consumed from ocr_requests topic.",
    labelnames=("camera",),
)

OCR_RESULTS: Counter = Counter(
    "parkguard_plate_ocr_results_total",
    "OCR results produced to ocr_results topic.",
    labelnames=("format", "valid"),
)

OCR_REJECTED: Counter = Counter(
    "parkguard_plate_ocr_rejected_total",
    "OCR attempts rejected before/after inference.",
    labelnames=("reason",),   # decode_error | no_plate | region_not_accepted | low_confidence
)

OCR_LATENCY: Histogram = Histogram(
    "parkguard_plate_ocr_latency_seconds",
    "Latency from consume(ocr_request) to produce(ocr_result).",
    buckets=(0.01, 0.025, 0.05, 0.1, 0.2, 0.35, 0.6, 1.0, 2.0, 5.0),
)

UP: Gauge = Gauge(
    "parkguard_plate_up",
    "1 while the plate-service main loop is running.",
)

__all__ = ["OCR_LATENCY", "OCR_REJECTED", "OCR_REQUESTS", "OCR_RESULTS", "UP"]
