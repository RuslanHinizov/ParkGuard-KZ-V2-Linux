"""
python-services/shared/redis_client.py

Async Redis client (redis-py `asyncio`). Single shared pool per process.
Provides:
  - `get_redis()`      lazy connection factory
  - `close_redis()`    graceful shutdown
  - `ping()`           healthcheck

Used by CVI manager, active-violation registry, state machine, exit
confirmator, cooldown manager (spec §4.4, §5, §8).
"""

from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis
from redis.asyncio import Redis
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from shared.config import settings
from shared.logging import get_logger

logger = get_logger(__name__)

_client: Redis | None = None


def get_redis() -> Redis:
    """Lazy-init Redis client with AOF-backed persistence on the server side."""
    global _client
    if _client is None:
        _client = aioredis.from_url(
            str(settings.REDIS_URL),
            encoding="utf-8",
            decode_responses=False,   # store bytes; callers decode when needed
            socket_timeout=5.0,
            socket_connect_timeout=3.0,
            retry_on_timeout=True,
            health_check_interval=30,
            max_connections=100,
        )
        logger.info("redis_client_initialized", host=settings.REDIS_HOST, port=settings.REDIS_PORT)
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        logger.info("redis_client_closed")
    _client = None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.1, min=0.1, max=1.0),
    retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    reraise=True,
)
async def ping() -> bool:
    """Healthcheck — returns True if Redis responds to PING."""
    try:
        r = get_redis()
        pong: Any = await r.ping()
        return bool(pong)
    except Exception as exc:  # noqa: BLE001
        logger.warning("redis_ping_failed", error=str(exc))
        return False


__all__ = ["Redis", "close_redis", "get_redis", "ping"]
