"""
python-services/event-api/src/routers/violations.py

Violations REST + WebSocket endpoints (spec §11, Adım 10).

REST
----
GET    /api/v1/violations                  list w/ cursor pagination + filters
GET    /api/v1/violations/{id}             fetch one (404 if not found)
PATCH  /api/v1/violations/{id}             update status (approved/disputed)
POST   /api/v1/violations/{id}/penalty-card trigger penalty-card-service

WebSocket
---------
GET /ws/violations   real-time ViolationEvent stream; JSON frames pushed on
                     every new violation committed by violation-service.
                     Uses the in-process ConnectionManager fanout.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.orm import load_only

from shared import kafka_client
from shared.config import settings
from shared.db import get_session
from shared.logging import get_logger
from shared.schemas import PenaltyCardRequest, ViolationOut, ViolationPatch, ViolationStatus
from src.models.violation import Violation
from src.services.audit import record_audit
from src.services.ws_manager import manager

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/violations", tags=["violations"])
ws_router = APIRouter(tags=["stream"])

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _to_out(row: Violation) -> ViolationOut:
    from uuid import UUID

    return ViolationOut(
        id=row.id,
        camera_id=row.camera_id,
        zone_id=row.zone_id,
        cvi_id=UUID(row.cvi_id) if row.cvi_id else None,
        plate_text=row.plate_text,
        plate_format=row.plate_format,
        plate_region_code=row.plate_region_code,
        plate_confidence=row.plate_confidence,
        vehicle_class=row.vehicle_class,
        first_seen_in_zone=row.first_seen_in_zone,
        violation_time=row.violation_time,
        exit_confirmed_time=row.exit_confirmed_time,
        duration_seconds=row.duration_seconds,
        snapshot_vehicle_url=row.snapshot_vehicle_url,
        snapshot_plate_url=row.snapshot_plate_url,
        status=ViolationStatus(row.status),
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        penalty_card_url=row.penalty_card_url,
        created_at=row.created_at,
    )


# --------------------------------------------------------------------------- #
# REST endpoints
# --------------------------------------------------------------------------- #
@router.get("", response_model=list[ViolationOut])
async def list_violations(
    camera_id: Annotated[str | None, Query()] = None,
    zone_id: Annotated[int | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    from_time: Annotated[datetime | None, Query()] = None,
    to_time: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sess: "AsyncSession" = Depends(get_session),
) -> list[ViolationOut]:
    """
    List violations. All filters are optional and combinable.
    Ordered newest-first by violation_time.
    """
    q = select(Violation)
    if camera_id:
        q = q.where(Violation.camera_id == camera_id)
    if zone_id is not None:
        q = q.where(Violation.zone_id == zone_id)
    if status:
        q = q.where(Violation.status == status)
    if from_time:
        q = q.where(Violation.violation_time >= from_time)
    if to_time:
        q = q.where(Violation.violation_time <= to_time)
    q = q.order_by(Violation.violation_time.desc()).limit(limit).offset(offset)
    rows = (await sess.execute(q)).scalars().all()
    return [_to_out(r) for r in rows]


@router.get("/{violation_id}", response_model=ViolationOut)
async def get_violation(
    violation_id: int,
    sess: "AsyncSession" = Depends(get_session),
) -> ViolationOut:
    row = await sess.get(Violation, violation_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Violation {violation_id} not found")
    return _to_out(row)


@router.patch("/{violation_id}", response_model=ViolationOut)
async def patch_violation(
    violation_id: int,
    body: ViolationPatch,
    request: Request,
    sess: "AsyncSession" = Depends(get_session),
) -> ViolationOut:
    """
    Update violation status. Operator name is required (middleware enforces
    X-Operator-Name). Writes an audit_log row.
    """
    row = await sess.get(Violation, violation_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Violation {violation_id} not found")

    operator: str = getattr(request.state, "operator_name", "unknown")
    old_status = row.status
    row.status = body.status.value
    row.reviewed_by = operator
    row.reviewed_at = datetime.now(tz=timezone.utc)
    await sess.flush()

    await record_audit(
        sess,
        action=f"violation.status:{old_status}->{row.status}",
        target_type="violation",
        target_id=str(violation_id),
        operator_name=operator,
        client_ip=getattr(request.state, "client_ip", None),
        after={"status": row.status},
    )
    await sess.commit()
    await sess.refresh(row)
    logger.info("violation_status_updated",
                id=violation_id, status=row.status, operator=operator)
    return _to_out(row)


@router.post("/{violation_id}/penalty-card", status_code=status.HTTP_202_ACCEPTED)
async def trigger_penalty_card(
    violation_id: int,
    request: Request,
    sess: "AsyncSession" = Depends(get_session),
) -> dict[str, str | int]:
    """
    Enqueue a penalty-card-service request. Idempotent — if a card URL
    already exists the request is skipped and the existing URL returned.
    """
    row = await sess.get(Violation, violation_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Violation {violation_id} not found")

    if row.penalty_card_url:
        return {"status": "already_issued", "url": row.penalty_card_url,
                "violation_id": violation_id}

    req = PenaltyCardRequest(violation_id=violation_id)
    await kafka_client.send_json(
        settings.KAFKA_TOPIC_PENALTY_REQ,
        req.model_dump(mode="json"),
        key=str(violation_id),
    )
    logger.info("penalty_card_triggered",
                violation_id=violation_id,
                operator=getattr(request.state, "operator_name", "unknown"))
    return {"status": "queued", "violation_id": violation_id}


# --------------------------------------------------------------------------- #
# WebSocket
# --------------------------------------------------------------------------- #
@ws_router.websocket("/ws/violations")
async def ws_violations(websocket: WebSocket) -> None:
    """
    Stream ViolationEvent JSON frames to connected dashboard clients.
    Frames arrive via the in-process fanout task (see main.py lifespan).
    The client receives only push data; sends are ignored.
    """
    await manager.connect(websocket)
    try:
        while True:
            # Keep the connection alive; we only push data server→client.
            # recv_text() blocks until client sends something or disconnects.
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
    finally:
        await manager.disconnect(websocket)
