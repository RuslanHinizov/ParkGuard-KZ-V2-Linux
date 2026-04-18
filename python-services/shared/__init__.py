"""
python-services/shared/__init__.py

Cross-service shared utilities: DB, Redis, Kafka clients, geometry helpers,
Kazakhstan plate normalizer, structured logging, Pydantic schemas.

Imported by every Python service (violation, plate, event-api, snapshot,
penalty-card, worker-pool) via PYTHONPATH mount in Docker.
"""

from __future__ import annotations

__all__: list[str] = []
