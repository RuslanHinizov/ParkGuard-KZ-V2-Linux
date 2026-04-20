"""
python-services/snapshot-consumer/tests/conftest.py

pydantic's Settings object parses environment at import time and will reject
a missing POSTGRES_PASSWORD / REDIS_PASSWORD even though the snapshot-consumer
doesn't actually use those services. We set placeholder values here so
`shared.config` imports cleanly in tests.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/parkguard")
os.environ.setdefault("DATABASE_URL_SYNC", "postgresql+psycopg2://test:test@localhost:5432/parkguard")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("REDIS_URL", "redis://:test@localhost:6379/0")
os.environ.setdefault("REDIS_PASSWORD", "test")
os.environ.setdefault("MINIO_ROOT_PASSWORD", "test")
