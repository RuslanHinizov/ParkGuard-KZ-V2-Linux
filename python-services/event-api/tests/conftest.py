"""
python-services/event-api/tests/conftest.py

Pytest fixtures for event-api. The suite runs against an in-process
SQLite engine so you can `pytest` on Windows without docker; spec-level
SQL features (PostGIS, INET) are simulated via SQLAlchemy types that
degrade gracefully under SQLite (see models.camera / models.audit_log).

When the full compose stack is up (`make test` on Linux), the same
tests run against the real Postgres engine by setting
DATABASE_URL / DATABASE_URL_SYNC in the environment.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

# Set env vars BEFORE importing shared.config (parsed at module level).
# Use valid Postgres-scheme URLs so pydantic's PostgresDsn validator passes;
# the actual SQLite engine is built explicitly in the `engine` fixture and
# monkeypatched into shared.db — these DSNs are never opened for real.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/parkguard")
os.environ.setdefault("DATABASE_URL_SYNC", "postgresql+psycopg2://test:test@localhost:5432/parkguard")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("REDIS_URL", "redis://:test@localhost:6379/0")
os.environ.setdefault("REDIS_PASSWORD", "test")
os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
os.environ.setdefault("MINIO_ENDPOINT", "localhost:9000")
os.environ.setdefault("MINIO_ROOT_USER", "minioadmin")
os.environ.setdefault("MINIO_ROOT_PASSWORD", "minioadmin")
os.environ.setdefault("ENV", "dev")

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool  # noqa: E402

import shared.db as shared_db  # noqa: E402
import shared.kafka_client as shared_kafka  # noqa: E402
import shared.minio_client as shared_minio  # noqa: E402
import shared.redis_client as shared_redis  # noqa: E402
from shared.db import Base, get_session as _original_get_session  # noqa: E402
from src import models  # noqa: E402,F401  — side-effect: register ORM classes
from src.main import create_app  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI


@pytest_asyncio.fixture
async def engine():
    """Fresh SQLite engine + schema per test module."""
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
async def app(monkeypatch, session_factory) -> "AsyncIterator[FastAPI]":  # noqa: ANN001
    """Build FastAPI app with shared DB/Kafka/Redis/MinIO stubs."""
    # --- DB: point shared.db.get_session at our test session factory ------
    async def _test_get_session():
        async with session_factory() as s:
            yield s

    async def _ping_true() -> bool:
        return True

    monkeypatch.setattr(shared_db, "get_session", _test_get_session)
    monkeypatch.setattr(shared_db, "ping", _ping_true)

    # --- Kafka / Redis / MinIO: disable network I/O in tests --------------
    async def _noop(*_a, **_kw):
        return None

    monkeypatch.setattr(shared_kafka, "get_producer", _noop)
    monkeypatch.setattr(shared_kafka, "close_producer", _noop)
    monkeypatch.setattr(shared_kafka, "ping", _ping_true)
    monkeypatch.setattr(shared_redis, "close_redis", _noop)
    monkeypatch.setattr(shared_redis, "ping", _ping_true)
    monkeypatch.setattr(shared_minio, "ping", _ping_true)

    _app = create_app()
    # Override the FastAPI dependency too (it resolved at import time).
    # NOTE: use the pre-captured original function — at this point
    # monkeypatch has already replaced shared_db.get_session, so a
    # fresh `from shared.db import get_session` would give us the stub.
    _app.dependency_overrides[_original_get_session] = _test_get_session
    yield _app


@pytest_asyncio.fixture
async def client(app) -> "AsyncIterator[AsyncClient]":  # noqa: ANN001
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


@pytest.fixture
def operator_headers() -> dict[str, str]:
    return {"X-Operator-Name": "pytest-operator"}
