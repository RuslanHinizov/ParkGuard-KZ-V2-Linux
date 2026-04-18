"""
python-services/event-api/src/services/audit.py

Tiny helper that records one row in `audit_log` per successful mutation.
Routers call `record_audit(...)` from inside the same SQLAlchemy session
as the business-object write so either both commit or both roll back
— we never want a phantom audit row for a failed write.

Payload shape is `{"before": {...}|null, "after": {...}|null}`. Pydantic
models come in pre-serialised via `model_dump(mode="json")` so the
stored JSON survives a round-trip even with datetimes/UUIDs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.models.audit_log import AuditLog

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def record_audit(
    session: "AsyncSession",
    *,
    action: str,
    target_type: str,
    target_id: str | None,
    operator_name: str | None,
    client_ip: str | None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Append an audit row inside the caller's transaction."""
    row = AuditLog(
        operator_name=operator_name,
        action=action,
        target_type=target_type,
        target_id=target_id,
        payload={"before": before, "after": after},
        ip_address=client_ip,
    )
    session.add(row)
    # caller commits; we flush so the row gets an id for downstream ref
    await session.flush()
