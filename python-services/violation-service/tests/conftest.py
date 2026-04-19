"""
python-services/violation-service/tests/conftest.py

Fixtures for violation-service tests. The SQLite engine runs against
`:memory:` with a StaticPool so the schema-create connection and the
session-factory connection see the same database. We register minimal
`cameras` / `zones` fixture tables here (violation-service never owns
these in production — they live in event-api — but we need them for FK
references during tests).

No docker, no Kafka, no Redis required. `fakeredis` fills the Redis
role; the CVI manager + state machine + writer behave identically
against it.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

# BEFORE importing shared.config. The typed Settings insists on real
# postgres/redis DSN strings; we feed it placeholder DSNs that pass
# pydantic validation but never hit a real server. The actual test
# engine is an in-memory sqlite created below with StaticPool.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/parkguard"
)
os.environ.setdefault(
    "DATABASE_URL_SYNC", "postgresql+psycopg2://test:test@localhost:5432/parkguard"
)
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("REDIS_URL", "redis://:test@localhost:6379/0")
os.environ.setdefault("REDIS_PASSWORD", "test")
os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
os.environ.setdefault("MINIO_ENDPOINT", "localhost:9000")
os.environ.setdefault("MINIO_ROOT_USER", "minioadmin")
os.environ.setdefault("MINIO_ROOT_PASSWORD", "minioadmin")
os.environ.setdefault("ENV", "dev")

import fakeredis.aioredis as fake_aioredis  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool  # noqa: E402

from shared.db import Base  # noqa: E402
from src import models  # noqa: E402,F401 — register Violation + CVIRecord
from tests._fixture_models import FixtureCamera, FixtureZone  # noqa: E402,F401

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):  # noqa: ANN001
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def seeded(session_factory) -> "AsyncIterator[dict]":  # noqa: ANN001
    """Insert one camera and one zone (POLYGON (0,0)-(100,100))."""
    async with session_factory() as sess:
        # cameras
        await sess.execute(
            text(
                "INSERT INTO cameras (id, name, rtsp_url) "
                "VALUES ('cam_test', 'Test Cam', 'rtsp://x')"
            )
        )
        # zones — square polygon 0,0 → 100,100
        wkt_poly = "POLYGON((0 0,100 0,100 100,0 100,0 0))"
        await sess.execute(
            text(
                "INSERT INTO zones "
                "(camera_id, name, zone_type, polygon_wkt, "
                " threshold_seconds, exit_confirm_seconds, cooldown_seconds, enabled) "
                "VALUES (:cam, 'zone1', 'no_parking', :poly, 20, 10, 10, 1)"
            ),
            {"cam": "cam_test", "poly": wkt_poly},
        )
        await sess.commit()

        result = await sess.execute(text("SELECT id FROM zones WHERE name = 'zone1'"))
        zone_id = result.scalar_one()

    yield {"camera_id": "cam_test", "zone_id": int(zone_id)}


@pytest_asyncio.fixture
async def redis():  # fake aioredis client
    client = fake_aioredis.FakeRedis()
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def fixed_now():
    """Deterministic base timestamp for state-machine tests."""
    from datetime import datetime, timezone

    return datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone.utc)
