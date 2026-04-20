"""
python-services/event-api/src/routers/cameras.py

Cameras CRUD (spec §11 — /api/v1/cameras/*).

POST   /api/v1/cameras            — strict create (409 if id exists)
GET    /api/v1/cameras            — list, optional `?enabled=true`
GET    /api/v1/cameras/{id}       — fetch one (404 if not found)
PUT    /api/v1/cameras/{id}       — idempotent upsert (create or replace)
DELETE /api/v1/cameras/{id}       — hard delete

Mutations require the `X-Operator-Name` header (middleware enforces that
before we get here). Every successful mutation records an `audit_log`
row inside the same transaction.

The RTSP reload / live-snapshot / mjpeg endpoints listed in spec §11
are NOT in scope for Adım 2 — they land once the C++ DeepStream
pipeline is wired (Adım 3+) and will reuse this router.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from shared.db import get_session
from shared.logging import get_logger
from shared.schemas import CameraCreate, CameraOut, CameraUpdate
from src.models.camera import Camera
from src.services.audit import record_audit

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/cameras", tags=["cameras"])


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _to_out(row: Camera) -> CameraOut:
    return CameraOut(
        id=row.id,
        name=row.name,
        rtsp_substream_url=row.rtsp_substream_url,
        rtsp_mainstream_url=row.rtsp_mainstream_url,
        location_description=row.location_description,
        enabled=row.enabled,
        config=row.config,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _snapshot(row: Camera) -> dict[str, object]:
    """Audit-payload snapshot — stable subset, excludes row timestamps."""
    return {
        "id": row.id,
        "name": row.name,
        "rtsp_substream_url": row.rtsp_substream_url,
        "rtsp_mainstream_url": row.rtsp_mainstream_url,
        "location_description": row.location_description,
        "enabled": row.enabled,
        "config": row.config,
    }


async def _get_or_404(session: "AsyncSession", camera_id: str) -> Camera:
    row = await session.get(Camera, camera_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"camera '{camera_id}' not found")
    return row


# --------------------------------------------------------------------------- #
# routes
# --------------------------------------------------------------------------- #
@router.get("", response_model=list[CameraOut])
async def list_cameras(
    enabled: bool | None = Query(None, description="Filter by enabled flag"),
    session: "AsyncSession" = Depends(get_session),
) -> list[CameraOut]:
    stmt = select(Camera).order_by(Camera.id)
    if enabled is not None:
        stmt = stmt.where(Camera.enabled == enabled)
    result = await session.execute(stmt)
    return [_to_out(r) for r in result.scalars().all()]


@router.get("/{camera_id}", response_model=CameraOut)
async def get_camera(
    camera_id: str,
    session: "AsyncSession" = Depends(get_session),
) -> CameraOut:
    row = await _get_or_404(session, camera_id)
    return _to_out(row)


@router.post("", response_model=CameraOut, status_code=status.HTTP_201_CREATED)
async def create_camera(
    payload: CameraCreate,
    request: Request,
    session: "AsyncSession" = Depends(get_session),
) -> CameraOut:
    existing = await session.get(Camera, payload.id)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"camera '{payload.id}' already exists",
        )

    row = Camera(
        id=payload.id,
        name=payload.name,
        rtsp_substream_url=payload.rtsp_substream_url,
        rtsp_mainstream_url=payload.rtsp_mainstream_url,
        location_description=payload.location_description,
        enabled=payload.enabled,
        config=payload.config,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:  # race window between check + insert
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"camera '{payload.id}' already exists",
        ) from exc

    await record_audit(
        session,
        action="camera.create",
        target_type="camera",
        target_id=row.id,
        operator_name=request.state.operator_name,
        client_ip=request.state.client_ip,
        before=None,
        after=_snapshot(row),
    )
    await session.commit()
    await session.refresh(row)
    logger.info("camera_created", camera_id=row.id, operator=request.state.operator_name)
    return _to_out(row)


@router.put("/{camera_id}", response_model=CameraOut)
async def upsert_camera(
    camera_id: str,
    payload: CameraCreate | CameraUpdate,
    request: Request,
    session: "AsyncSession" = Depends(get_session),
) -> CameraOut:
    """Idempotent upsert — if missing, create with given body (body.id ignored
    in favour of the path param); if present, replace all fields found in
    the body. Seed scripts rely on this for re-runnable fixtures."""
    row = await session.get(Camera, camera_id)
    action: str
    before: dict[str, object] | None

    if row is None:
        # create path — require full CameraCreate-like body
        if not isinstance(payload, CameraCreate):
            # CameraUpdate with no existing row is ambiguous → 404
            raise HTTPException(
                status_code=404,
                detail=f"camera '{camera_id}' not found; POST or PUT full body to create",
            )
        row = Camera(
            id=camera_id,
            name=payload.name,
            rtsp_substream_url=payload.rtsp_substream_url,
            rtsp_mainstream_url=payload.rtsp_mainstream_url,
            location_description=payload.location_description,
            enabled=payload.enabled,
            config=payload.config,
        )
        session.add(row)
        action, before = "camera.create", None
    else:
        before = _snapshot(row)
        data = payload.model_dump(exclude_unset=True)
        data.pop("id", None)
        for k, v in data.items():
            setattr(row, k, v)
        action = "camera.update"

    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(exc.orig)) from exc

    await record_audit(
        session,
        action=action,
        target_type="camera",
        target_id=row.id,
        operator_name=request.state.operator_name,
        client_ip=request.state.client_ip,
        before=before,
        after=_snapshot(row),
    )
    await session.commit()
    await session.refresh(row)
    logger.info(
        "camera_upserted",
        camera_id=row.id,
        action=action,
        operator=request.state.operator_name,
    )
    return _to_out(row)


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_camera(
    camera_id: str,
    request: Request,
    session: "AsyncSession" = Depends(get_session),
) -> None:
    row = await _get_or_404(session, camera_id)
    before = _snapshot(row)
    await session.delete(row)
    await record_audit(
        session,
        action="camera.delete",
        target_type="camera",
        target_id=camera_id,
        operator_name=request.state.operator_name,
        client_ip=request.state.client_ip,
        before=before,
        after=None,
    )
    await session.commit()
    logger.info("camera_deleted", camera_id=camera_id, operator=request.state.operator_name)
