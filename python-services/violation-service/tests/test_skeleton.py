"""
Smoke tests for the Ad\u0131m 4 violation-service skeleton. The real state
machine + CVI tests arrive in Ad\u0131m 5. For now we just assert the entry
points are importable and the metrics registry is wired up.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DATABASE_URL_SYNC", "sqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
os.environ.setdefault("MINIO_ENDPOINT", "localhost:9000")
os.environ.setdefault("MINIO_ROOT_USER", "minioadmin")
os.environ.setdefault("MINIO_ROOT_PASSWORD", "minioadmin")


def test_module_imports() -> None:
    from src import health, main, metrics  # noqa: F401


def test_metrics_registered() -> None:
    from prometheus_client import generate_latest

    from src import metrics  # noqa: F401 — import triggers registration

    body = generate_latest().decode()
    for name in (
        "parkguard_violation_up",
        "parkguard_detections_consumed_total",
        "parkguard_violation_messages_failed_total",
        "parkguard_state_transitions_total",
        "parkguard_violations_created_total",
        "parkguard_duplicates_prevented_total",
    ):
        assert name in body, f"metric {name} not registered"
