"""
python-services/event-api/src/models/camera.py

Camera ORM — mirrors spec §10.5 `cameras` table exactly.

ID is a human-friendly VARCHAR(32) (e.g. "cam_entrance", "cam_05"), not a
UUID, because operators reference cameras by short slug in logs, RTSP
URLs, and DeepStream sensor_id. The slug is validated at the HTTP layer
(see schemas.CameraCreate.id pattern).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base

# Portable JSON column type: JSONB on Postgres, plain JSON elsewhere (tests).
_JSON = JSON().with_variant(JSONB(), "postgresql")


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    rtsp_substream_url: Mapped[str] = mapped_column(Text, nullable=False)
    rtsp_mainstream_url: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    location_description: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    config: Mapped[dict[str, Any]] = mapped_column(
        _JSON,
        nullable=False,
        default_factory=dict,
    )
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
