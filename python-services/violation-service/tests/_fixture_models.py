"""
python-services/violation-service/tests/_fixture_models.py

Test-only stub ORM classes for the `cameras` and `zones` tables. The
real definitions live in event-api (spec §10.5); violation-service
must reference them via FK but should not own their production
schema. These stubs exist purely so `Base.metadata.create_all` has the
parent tables available when running the sqlite-in-memory test suite.
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base


class FixtureCamera(Base):
    __tablename__ = "cameras"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    rtsp_url: Mapped[str] = mapped_column(Text, nullable=False)


class FixtureZone(Base):
    __tablename__ = "zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, init=False)
    camera_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("cameras.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    zone_type: Mapped[str] = mapped_column(String(32), nullable=False, default="no_parking")
    polygon_wkt: Mapped[str] = mapped_column(Text, nullable=False)
    threshold_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    exit_confirm_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
