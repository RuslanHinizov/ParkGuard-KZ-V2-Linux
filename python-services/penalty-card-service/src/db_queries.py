"""
python-services/penalty-card-service/src/db_queries.py

Read violation row + zone name; write back penalty_card_url.

Intentionally plain SQLAlchemy core queries — no ORM model import — so
this service has no dependency on violation-service's model package. The
relevant columns are queried by name via `text()` / explicit column refs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(slots=True)
class ViolationRow:
    id: int
    camera_id: str
    zone_id: int
    zone_name: str
    violation_time: datetime
    duration_seconds: int | None
    plate_text: str | None
    plate_format: str
    plate_region_code: str | None
    is_diplomatic: bool
    vehicle_class: str | None
    snapshot_vehicle_url: str | None
    penalty_card_url: str | None


async def fetch_violation(
    sf: "async_sessionmaker[AsyncSession]",
    violation_id: int,
) -> ViolationRow | None:
    """
    Join violations ⟶ zones to get the human-readable zone name for the card.
    Returns None if the violation_id doesn't exist.
    """
    stmt = text(
        """
        SELECT
            v.id,
            v.camera_id,
            v.zone_id,
            COALESCE(z.name, '') AS zone_name,
            v.violation_time,
            v.duration_seconds,
            v.plate_text,
            COALESCE(v.plate_format, 'unknown') AS plate_format,
            v.plate_region_code,
            COALESCE(v.is_diplomatic, FALSE) AS is_diplomatic,
            v.vehicle_class,
            v.snapshot_vehicle_url,
            v.penalty_card_url
        FROM violations v
        LEFT JOIN zones z ON z.id = v.zone_id
        WHERE v.id = :vid
        """
    )
    async with sf() as sess:
        row = (await sess.execute(stmt, {"vid": violation_id})).mappings().one_or_none()
    if row is None:
        return None
    return ViolationRow(
        id=row["id"],
        camera_id=row["camera_id"],
        zone_id=row["zone_id"],
        zone_name=row["zone_name"] or f"Zone {row['zone_id']}",
        violation_time=row["violation_time"],
        duration_seconds=row["duration_seconds"],
        plate_text=row["plate_text"],
        plate_format=row["plate_format"],
        plate_region_code=row["plate_region_code"],
        is_diplomatic=bool(row["is_diplomatic"]),
        vehicle_class=row["vehicle_class"],
        snapshot_vehicle_url=row["snapshot_vehicle_url"],
        penalty_card_url=row["penalty_card_url"],
    )


async def write_card_url(
    sf: "async_sessionmaker[AsyncSession]",
    violation_id: int,
    url: str,
) -> None:
    """Update violations.penalty_card_url and flip status to 'card_issued'."""
    stmt = text(
        """
        UPDATE violations
        SET penalty_card_url = :url,
            status           = 'card_issued'
        WHERE id = :vid
        """
    )
    async with sf() as sess:
        await sess.execute(stmt, {"url": url, "vid": violation_id})
        await sess.commit()


__all__ = ["ViolationRow", "fetch_violation", "write_card_url"]
