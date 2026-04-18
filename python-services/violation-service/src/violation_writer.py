"""
python-services/violation-service/src/violation_writer.py

Writes one row into `violations` when the state machine fires
INSIDE_ZONE → VIOLATED. The (camera_id, zone_id, identity_key,
cycle_id) composite unique index catches races silently — we swallow
the IntegrityError, increment the `db_unique` duplicate-prevention
metric, and return the row that already exists (so downstream
snapshot/penalty-card requests refer to the correct violation_id).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from shared.logging import get_logger
from src.metrics import DUPLICATES_PREVENTED, VIOLATIONS_CREATED
from src.models import Violation

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = get_logger(__name__)


@dataclass(slots=True)
class ViolationInsert:
    camera_id: str
    zone_id: int
    cvi_id: UUID | None
    identity_key: str
    cycle_id: int
    plate_text: str | None
    plate_confidence: float | None
    plate_format: str | None
    plate_region_code: str | None
    plate_valid_format: bool
    is_diplomatic: bool
    vehicle_class: str | None
    first_seen_in_zone: datetime
    violation_time: datetime
    bbox: dict[str, int] | None


@dataclass(slots=True)
class WriteResult:
    violation_id: int
    created: bool      # False = IntegrityError swallowed (dup race)


class ViolationWriter:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def insert(self, row: ViolationInsert) -> WriteResult:
        async with self._sf() as sess:
            try:
                v = Violation(
                    camera_id=row.camera_id,
                    zone_id=row.zone_id,
                    cvi_id=row.cvi_id,
                    identity_key=row.identity_key,
                    cycle_id=row.cycle_id,
                    plate_text=row.plate_text,
                    plate_confidence=row.plate_confidence,
                    plate_format=row.plate_format,
                    plate_region_code=row.plate_region_code,
                    plate_valid_format=row.plate_valid_format,
                    is_diplomatic=row.is_diplomatic,
                    vehicle_class=row.vehicle_class,
                    first_seen_in_zone=row.first_seen_in_zone,
                    violation_time=row.violation_time,
                    bbox=row.bbox,
                )
                sess.add(v)
                await sess.flush()
                vid = v.id
                await sess.commit()
                VIOLATIONS_CREATED.labels(
                    camera=row.camera_id, zone=str(row.zone_id)
                ).inc()
                logger.info(
                    "violation_written",
                    id=vid,
                    camera=row.camera_id,
                    zone=row.zone_id,
                    identity=row.identity_key,
                    cycle=row.cycle_id,
                )
                return WriteResult(violation_id=vid, created=True)
            except IntegrityError:
                await sess.rollback()
                DUPLICATES_PREVENTED.labels(layer="db_unique").inc()
                existing_id = await _lookup_existing(
                    sess,
                    camera_id=row.camera_id,
                    zone_id=row.zone_id,
                    identity_key=row.identity_key,
                    cycle_id=row.cycle_id,
                )
                logger.info(
                    "violation_duplicate_swallowed",
                    camera=row.camera_id,
                    zone=row.zone_id,
                    identity=row.identity_key,
                    cycle=row.cycle_id,
                    existing_id=existing_id,
                )
                # If the row vanished (should not happen) we return -1 so
                # the state machine can still progress instead of raising.
                return WriteResult(
                    violation_id=existing_id if existing_id is not None else -1,
                    created=False,
                )


async def _lookup_existing(
    sess: AsyncSession,
    *,
    camera_id: str,
    zone_id: int,
    identity_key: str,
    cycle_id: int,
) -> int | None:
    stmt = (
        select(Violation.id)
        .where(Violation.camera_id == camera_id)
        .where(Violation.zone_id == zone_id)
        .where(Violation.identity_key == identity_key)
        .where(Violation.cycle_id == cycle_id)
    )
    res = await sess.execute(stmt)
    return res.scalar_one_or_none()


__all__ = ["ViolationInsert", "ViolationWriter", "WriteResult"]
