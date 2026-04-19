"""
python-services/violation-service/src/models/violation.py

Violation ORM — spec §10.5 `violations`. This is the row the state
machine writes in the INSIDE_ZONE → VIOLATED transition; the
(camera_id, zone_id, identity_key, cycle_id) composite unique index
below is the *last* line of defence in the 4-layer duplicate-prevention
strategy (spec §5.2).

The same ORM must run under both Postgres (prod) and SQLite (tests),
so JSONB and UUID are carried via `with_variant` shims.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base

_JSON = JSON().with_variant(JSONB(), "postgresql")


class Violation(Base):
    __tablename__ = "violations"
    __table_args__ = (
        UniqueConstraint(
            "camera_id",
            "zone_id",
            "identity_key",
            "cycle_id",
            name="uniq_violation_identity_cycle",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, init=False)

    # ---- Required fields (no default — must be supplied at construction) ----
    # NOTE: SQLAlchemy's MappedAsDataclass generates __init__ from field
    # declaration order, and Python dataclasses forbid a required arg after
    # a defaulted one. So all non-default fields live up here; defaulted
    # fields follow. SQL column order in the table is cosmetic.
    camera_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("cameras.id", ondelete="CASCADE"),
        nullable=False,
    )
    zone_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("zones.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Idempotency — the ONE constraint that makes duplicate cezas impossible.
    identity_key: Mapped[str] = mapped_column(String(128), nullable=False)
    first_seen_in_zone: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    violation_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # ---- Optional / defaulted fields ----
    cvi_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("cvi_records.cvi_id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    cycle_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    plate_text: Mapped[str | None] = mapped_column(String(16), nullable=True, default=None)
    plate_confidence: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    plate_format: Mapped[str | None] = mapped_column(String(16), nullable=True, default=None)
    plate_region_code: Mapped[str | None] = mapped_column(String(4), nullable=True, default=None)
    plate_valid_format: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_diplomatic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    vehicle_class: Mapped[str | None] = mapped_column(String(20), nullable=True, default=None)

    exit_confirmed_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    snapshot_vehicle_url: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    snapshot_plate_url: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    penalty_card_url: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    bbox: Mapped[dict[str, Any] | None] = mapped_column(_JSON, nullable=True, default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        init=False,
    )
