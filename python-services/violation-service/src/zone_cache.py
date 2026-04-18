"""
python-services/violation-service/src/zone_cache.py

Lightweight zone cache for the state machine hot-path. Zones are
edited by the operator via event-api (Adım 4) and mutate rarely;
reloading them from Postgres on every observation would be wasteful,
but staleness for >60 s is unacceptable (operators expect "save
polygon → block parking there now"). So the cache holds the parsed
polygon in memory and refreshes on a simple TTL.

The cache reads through raw SQL (SELECT … FROM zones) rather than
importing the event-api ORM class. This keeps the violation-service
package-independent and avoids two mapper registrations on the same
Python Base metadata.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from shapely import wkt
from shapely.geometry import Polygon
from sqlalchemy import text

from shared.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = get_logger(__name__)

DEFAULT_TTL_SECONDS = 30.0

# Postgres returns the geometry as hex-EWKB by default; ST_AsText coerces
# to WKT which shapely parses directly. SQLite tests already store WKT.
_ZONES_QUERY_PG = text(
    """
    SELECT id,
           camera_id,
           name,
           zone_type,
           ST_AsText(polygon_wkt) AS polygon_wkt,
           threshold_seconds,
           exit_confirm_seconds,
           cooldown_seconds
    FROM zones
    WHERE enabled = TRUE
    """
)

_ZONES_QUERY_SQLITE = text(
    """
    SELECT id, camera_id, name, zone_type, polygon_wkt,
           threshold_seconds, exit_confirm_seconds, cooldown_seconds
    FROM zones
    WHERE enabled = 1
    """
)


@dataclass(slots=True)
class ZoneEntry:
    id: int
    camera_id: str
    name: str
    zone_type: str
    polygon: Polygon
    threshold_seconds: int
    exit_confirm_seconds: int
    cooldown_seconds: int


@dataclass(slots=True)
class ZoneCache:
    session_factory: async_sessionmaker[AsyncSession]
    ttl_seconds: float = DEFAULT_TTL_SECONDS
    _by_camera: dict[str, list[ZoneEntry]] = field(default_factory=dict)
    _last_refresh: float = 0.0

    async def zones_for_camera(self, camera_id: str) -> list[ZoneEntry]:
        if (time.monotonic() - self._last_refresh) > self.ttl_seconds:
            await self.refresh()
        return self._by_camera.get(camera_id, [])

    def seed(self, entries: list[ZoneEntry]) -> None:
        """Test helper — preload zones without touching the DB."""
        by_camera: dict[str, list[ZoneEntry]] = {}
        for e in entries:
            by_camera.setdefault(e.camera_id, []).append(e)
        self._by_camera = by_camera
        self._last_refresh = time.monotonic()

    async def refresh(self) -> None:
        async with self.session_factory() as sess:
            dialect = sess.bind.dialect.name if sess.bind is not None else "postgresql"
            query = _ZONES_QUERY_PG if dialect == "postgresql" else _ZONES_QUERY_SQLITE
            rows = (await sess.execute(query)).mappings().all()

        by_camera: dict[str, list[ZoneEntry]] = {}
        for r in rows:
            try:
                poly = _parse_polygon(r["polygon_wkt"])
            except Exception as exc:  # noqa: BLE001
                logger.warning("zone_polygon_parse_failed", zone=r["id"], error=str(exc))
                continue
            entry = ZoneEntry(
                id=r["id"],
                camera_id=r["camera_id"],
                name=r["name"],
                zone_type=r["zone_type"],
                polygon=poly,
                threshold_seconds=r["threshold_seconds"],
                exit_confirm_seconds=r["exit_confirm_seconds"],
                cooldown_seconds=r["cooldown_seconds"],
            )
            by_camera.setdefault(r["camera_id"], []).append(entry)

        self._by_camera = by_camera
        self._last_refresh = time.monotonic()
        logger.debug("zone_cache_refreshed", cameras=len(by_camera), rows=len(rows))


def _parse_polygon(value: str | bytes) -> Polygon:
    """Accepts WKT (sqlite, tests) or WKB-hex (PostGIS without ST_AsText)."""
    if isinstance(value, (bytes, bytearray)):
        return wkt.loads(value.decode())
    s = str(value)
    if s and s[0] in "01" and all(ch in "0123456789ABCDEFabcdef" for ch in s[:16]):
        from shapely import wkb  # noqa: PLC0415

        return wkb.loads(bytes.fromhex(s))
    return wkt.loads(s)


__all__ = ["DEFAULT_TTL_SECONDS", "ZoneCache", "ZoneEntry"]
