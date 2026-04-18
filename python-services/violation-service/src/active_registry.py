"""
python-services/violation-service/src/active_registry.py

Redis-backed active-violation registry — spec §8, §1.2 layer 2.

Two small primitives on top of Redis:

  * `is_active(camera, zone, identity)`  — "is there already a live
    violation for this (camera, zone, identity)?"
  * `mark_active(camera, zone, identity, cycle, violation_id)` — set
    the active key and a 1-hour TTL (violation duration cap per spec)
  * `cycle(camera, zone, identity)` → integer, INCR on COOLDOWN exit

The registry tolerates a missing Redis (offline tests). When `redis`
is None every call becomes a no-op and callers fall back to the DB
unique constraint for duplicate-prevention.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shared.logging import get_logger

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = get_logger(__name__)

ACTIVE_TTL_SECONDS = 3600     # spec §8: bound a single violation at 1 h


def _active_key(camera_id: str, zone_id: int, identity_key: str) -> str:
    return f"active_violation:{camera_id}:{zone_id}:{identity_key}"


def _cycle_key(camera_id: str, zone_id: int, identity_key: str) -> str:
    return f"cycle:{camera_id}:{zone_id}:{identity_key}"


class ActiveViolationRegistry:
    def __init__(self, redis: Redis | None) -> None:
        self._redis = redis
        self._fallback_active: dict[str, str] = {}
        self._fallback_cycle: dict[str, int] = {}

    # ----------------------------------------------------------- active
    async def is_active(self, camera_id: str, zone_id: int, identity_key: str) -> bool:
        key = _active_key(camera_id, zone_id, identity_key)
        if self._redis is None:
            return key in self._fallback_active
        try:
            return await self._redis.exists(key) > 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("active_registry_redis_error", op="exists", error=str(exc))
            return key in self._fallback_active

    async def mark_active(
        self,
        camera_id: str,
        zone_id: int,
        identity_key: str,
        *,
        violation_id: int,
    ) -> None:
        key = _active_key(camera_id, zone_id, identity_key)
        self._fallback_active[key] = str(violation_id)
        if self._redis is None:
            return
        try:
            await self._redis.set(key, str(violation_id), ex=ACTIVE_TTL_SECONDS)
        except Exception as exc:  # noqa: BLE001
            logger.warning("active_registry_redis_error", op="set", error=str(exc))

    async def clear(self, camera_id: str, zone_id: int, identity_key: str) -> None:
        key = _active_key(camera_id, zone_id, identity_key)
        self._fallback_active.pop(key, None)
        if self._redis is None:
            return
        try:
            await self._redis.delete(key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("active_registry_redis_error", op="del", error=str(exc))

    # ----------------------------------------------------------- cycle
    async def current_cycle(
        self,
        camera_id: str,
        zone_id: int,
        identity_key: str,
    ) -> int:
        key = _cycle_key(camera_id, zone_id, identity_key)
        if self._redis is None:
            return self._fallback_cycle.get(key, 0)
        try:
            val = await self._redis.get(key)
            return int(val) if val is not None else 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("active_registry_redis_error", op="cycle_get", error=str(exc))
            return self._fallback_cycle.get(key, 0)

    async def bump_cycle(
        self,
        camera_id: str,
        zone_id: int,
        identity_key: str,
    ) -> int:
        """INCR the cycle counter; return the new value."""
        key = _cycle_key(camera_id, zone_id, identity_key)
        new = self._fallback_cycle.get(key, 0) + 1
        self._fallback_cycle[key] = new
        if self._redis is None:
            return new
        try:
            red_new = await self._redis.incr(key)
            return int(red_new)
        except Exception as exc:  # noqa: BLE001
            logger.warning("active_registry_redis_error", op="cycle_incr", error=str(exc))
            return new


__all__ = ["ACTIVE_TTL_SECONDS", "ActiveViolationRegistry"]
