"""
python-services/event-api/src/models/zone.py

Zone ORM — spec §10.5 `zones` table.

The polygon is stored as PostGIS `GEOMETRY(POLYGON)` in production; under
SQLite test builds it falls back to a `TEXT` column (WKT round-trip) so the
router-level validation and CRUD behaviour can be exercised without the
full compose stack.

`created_by` gets the `X-Operator-Name` header value captured by the
middleware, so every zone carries provenance without an audit_log join.
"""

from __future__ import annotations

from datetime import datetime

from geoalchemy2 import Geometry
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base

# PostGIS POLYGON on Postgres; WKT-in-TEXT on sqlite so tests run everywhere.
_POLYGON = Text().with_variant(
    Geometry(geometry_type="POLYGON", srid=0, spatial_index=False),
    "postgresql",
)


class Zone(Base):
    __tablename__ = "zones"

    # NOTE: MappedAsDataclass field order — required fields before defaulted ones.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, init=False)
    camera_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("cameras.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    polygon_wkt: Mapped[str] = mapped_column(_POLYGON, nullable=False)
    zone_type: Mapped[str] = mapped_column(String(32), nullable=False, default="no_parking")
    threshold_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    exit_confirm_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    color_hex: Mapped[str] = mapped_column(String(7), nullable=False, default="#FF0000")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        init=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        init=False,
    )
