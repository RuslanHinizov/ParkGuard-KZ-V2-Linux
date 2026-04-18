"""
python-services/violation-service/src/models/cvi.py

CVI record ORM — spec §10.5 `cvi_records`. The in-memory CVI dataclass
(plate_votes, embedding_centroid, state_per_zone, …) lives in
`src.cvi`; this class is its on-disk archival form — what Redis cold-
storages into Postgres after ~10 min of inactivity (spec §4.4).

Kept deliberately small: only the identity evidence that survives past
the active-tracking window. Full observation-count plus plate_votes top
vote is enough for audit queries "give me every CVI seen on camera X
between T1 and T2 that had plate Y".
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base


class CVIRecord(Base):
    __tablename__ = "cvi_records"

    cvi_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    camera_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("cameras.id", ondelete="CASCADE"),
        nullable=False,
    )
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    plate_text: Mapped[str | None] = mapped_column(String(16), nullable=True, default=None)
    plate_confidence: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    dominant_class: Mapped[str | None] = mapped_column(String(20), nullable=True, default=None)
    observations_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        init=False,
    )
