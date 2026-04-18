"""
python-services/shared/db.py

SQLAlchemy 2.x async engine + session factory shared across Python services.
Uses asyncpg driver; alembic uses the sync `DATABASE_URL_SYNC` variant.
All models inherit `Base` from this module so Alembic autogenerate can
discover them via metadata reflection.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, MappedAsDataclass

from shared.config import settings
from shared.logging import get_logger

logger = get_logger(__name__)


class Base(MappedAsDataclass, DeclarativeBase):
    """Declarative base for all ORM models (dataclass-style mapping)."""


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Lazy-init async engine (one per process)."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            str(settings.DATABASE_URL),
            echo=False,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=3600,
            future=True,
        )
        logger.info("db_engine_initialized", url=_mask_url(str(settings.DATABASE_URL)))
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """
    Transactional session scope. Commits on success, rolls back on exception.
    Use as ``async with session_scope() as s: ...``.
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a session without auto-commit."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def dispose_engine() -> None:
    """Close the engine gracefully (call on service shutdown)."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        logger.info("db_engine_disposed")
    _engine = None
    _session_factory = None


async def ping() -> bool:
    """Healthcheck — returns True if DB accepts `SELECT 1`."""
    from sqlalchemy import text

    try:
        async with session_scope() as s:
            await s.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001 — health should swallow errors
        logger.warning("db_ping_failed", error=str(exc))
        return False


def _mask_url(url: str) -> str:
    """Hide password in logs: postgresql+asyncpg://user:***@host/db"""
    try:
        prefix, rest = url.split("://", 1)
        if "@" in rest:
            creds, host = rest.split("@", 1)
            if ":" in creds:
                user, _pwd = creds.split(":", 1)
                return f"{prefix}://{user}:***@{host}"
        return url
    except Exception:  # noqa: BLE001
        return "postgresql://***"


__all__ = [
    "Base",
    "dispose_engine",
    "get_engine",
    "get_session",
    "get_session_factory",
    "ping",
    "session_scope",
]


# Hint for external type checkers / alembic autogenerate.
metadata: Any = Base.metadata
