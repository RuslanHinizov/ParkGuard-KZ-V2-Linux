"""
python-services/event-api/src/models/violation.py

Read/patch ORM for the `violations` table (event-api side).

The event-api only needs to: list, fetch, patch status, and write back
penalty_card_url.  Full violation creation lives in violation-service;
this definition intentionally omits foreign-key cascade declarations so
the model works against SQLite in tests without PostGIS.

Spec §11 routes that touch this table:
  GET    /api/v1/violations
  GET    /api/v1/violations/{id}
  PATCH  /api/v1/violations/{id}         (status + reviewed_by/at)
  POST   /api/v1/violations/{id}/penalty-card
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base


class Violation(Base):
    __tablename__ = "violations"

    id: Mapped[int]              = mapped_column(Integer, primary_key=True)
    camera_id: Mapped[str]       = mapped_column(String(32), nullable=False)
    zone_id: Mapped[int]         = mapped_column(Integer, nullable=False)
    identity_key: Mapped[str]    = mapped_column(String(128), nullable=False)
    first_seen_in_zone: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    violation_time: Mapped[datetime]     = mapped_column(DateTime(timezone=True))

    cvi_id: Mapped[str | None]   = mapped_column(Uuid(as_uuid=False), nullable=True, default=None)
    cycle_id: Mapped[int]        = mapped_column(Integer, default=0)

    plate_text: Mapped[str | None]            = mapped_column(String(16), nullable=True, default=None)
    plate_confidence: Mapped[float | None]    = mapped_column(Float, nullable=True, default=None)
    plate_format: Mapped[str | None]          = mapped_column(String(16), nullable=True, default=None)
    plate_region_code: Mapped[str | None]     = mapped_column(String(4), nullable=True, default=None)
    plate_valid_format: Mapped[bool]          = mapped_column(Boolean, default=False)
    is_diplomatic: Mapped[bool]               = mapped_column(Boolean, default=False)
    vehicle_class: Mapped[str | None]         = mapped_column(String(20), nullable=True, default=None)

    exit_confirmed_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    duration_seconds: Mapped[int | None]      = mapped_column(Integer, nullable=True, default=None)

    snapshot_vehicle_url: Mapped[str | None]  = mapped_column(Text, nullable=True, default=None)
    snapshot_plate_url: Mapped[str | None]    = mapped_column(Text, nullable=True, default=None)

    status: Mapped[str]          = mapped_column(String(20), default="pending")
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    penalty_card_url: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        init=False,
    )
