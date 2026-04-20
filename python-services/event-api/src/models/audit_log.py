"""
python-services/event-api/src/models/audit_log.py

Audit log ORM — spec §10.5 `audit_log`. Records every successful
mutation with the operator name taken from the `X-Operator-Name`
header. This is NOT authentication — the header is an honor-system
identifier captured for traceability only (see spec §11).

The `payload` JSONB column holds a compact diff:
    { "before": {...}|null, "after": {...}|null }
`null` on either side encodes create/delete. For PATCH we keep the
full snapshot so backfill / rollback tooling doesn't need to chase
chains of partial diffs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base

_JSON = JSON().with_variant(JSONB(), "postgresql")
_INET = String(45).with_variant(INET(), "postgresql")


class AuditLog(Base):
    __tablename__ = "audit_log"

    # NOTE: MappedAsDataclass requires non-default fields before defaulted ones.
    # `action` is required; all other user-supplied cols default to None.
    # BigInteger on Postgres (BIGSERIAL); plain Integer on SQLite so
    # autoincrement works without the RETURNING clause raising NOT NULL.
    _ID_COL = BigInteger().with_variant(Integer(), "sqlite")
    id: Mapped[int] = mapped_column(_ID_COL, primary_key=True, autoincrement=True, init=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    operator_name: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    payload: Mapped[dict[str, Any] | None] = mapped_column(_JSON, nullable=True, default=None)
    ip_address: Mapped[str | None] = mapped_column(_INET, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        init=False,
    )
