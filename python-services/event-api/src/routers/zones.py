"""
python-services/event-api/src/routers/zones.py

Zones CRUD (spec §11 — /api/v1/cameras/{id}/zones).

    GET    /api/v1/cameras/{camera_id}/zones
    POST   /api/v1/cameras/{camera_id}/zones
    GET    /api/v1/zones/{zone_id}
    PUT    /api/v1/zones/{zone_id}
    DELETE /api/v1/zones/{zone_id}

Polygon validity is checked with shapely (spec §15.2 — no hand-rolled ray
casting). Storing as WKT keeps portability between PostGIS and sqlite test
builds; the Postgres path reads the same column through GeoAlchemy2.

Every mutation writes an `audit_log` row using the same service helper as
the cameras router. `created_by` is set from X-Operator-Name at insert
time (spec §10.5 column) — it remains on the row as provenance even if
later operators modify it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from shared.db import get_session
from shared.geometry import polygon_is_valid_and_closed
from shared.logging import get_logger
from shared.schemas import PolygonPoint, ZoneCreate, ZoneOut, ZoneUpdate
from src.models.camera import Camera
from src.models.zone import Zone
from src.services.audit import record_audit

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# Two mount points — one scoped under a camera (list/create), one flat
# (fetch/update/delete by zone id). Both share the same module so the
# helpers stay local.
camera_zones = APIRouter(prefix="/api/v1/cameras/{camera_id}/zones", tags=["zones"])
zones = APIRouter(prefix="/api/v1/zones", tags=["zones"])


# --------------------------------------------------------------------------- #
# WKT helpers
# --------------------------------------------------------------------------- #
def _points_to_wkt(points: list[PolygonPoint]) -> str:
    """Close the ring if the caller didn't, then emit POLYGON((x y, ...))."""
    if not points:
        raise HTTPException(400, "polygon requires at least 3 points")
    ring = [(p.x, p.y) for p in points]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    inside = ", ".join(f"{x} {y}" for x, y in ring)
    return f"POLYGON(({inside}))"


def _wkt_to_points(wkt: str) -> list[PolygonPoint]:
    """Best-effort parser — handles the exact subset we emit above."""
    if not wkt.upper().startswith("POLYGON"):
        return []
    try:
        inner = wkt[wkt.index("((") + 2 : wkt.rindex("))")]
    except ValueError:
        return []
    out: list[PolygonPoint] = []
    for token in inner.split(","):
        parts = token.strip().split()
        if len(parts) != 2:
            continue
        out.append(PolygonPoint(x=float(parts[0]), y=float(parts[1])))
    # Trim the closing duplicate so the wire shape matches the caller's.
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def _validate_polygon(points: list[PolygonPoint]) -> None:
    ok, reason = polygon_is_valid_and_closed([(p.x, p.y) for p in points])
    if not ok:
        raise HTTPException(status_code=400, detail=f"invalid polygon: {reason}")


def _to_out(row: Zone) -> ZoneOut:
    return ZoneOut(
        id=row.id,
        camera_id=row.camera_id,
        name=row.name,
        zone_type=row.zone_type,
        polygon=_wkt_to_points(row.polygon_wkt),
        threshold_seconds=row.threshold_seconds,
        exit_confirm_seconds=row.exit_confirm_seconds,
        cooldown_seconds=row.cooldown_seconds,
        color_hex=row.color_hex,
        enabled=row.enabled,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _snapshot(row: Zone) -> dict[str, object]:
    return {
        "id": row.id,
        "camera_id": row.camera_id,
        "name": row.name,
        "zone_type": row.zone_type,
        "polygon_wkt": row.polygon_wkt,
        "threshold_seconds": row.threshold_seconds,
        "exit_confirm_seconds": row.exit_confirm_seconds,
        "cooldown_seconds": row.cooldown_seconds,
        "color_hex": row.color_hex,
        "enabled": row.enabled,
    }


async def _get_zone_or_404(session: "AsyncSession", zone_id: int) -> Zone:
    row = await session.get(Zone, zone_id)
    if row is None:
        raise HTTPException(404, f"zone {zone_id} not found")
    return row


# --------------------------------------------------------------------------- #
# camera-scoped routes
# --------------------------------------------------------------------------- #
@camera_zones.get("", response_model=list[ZoneOut])
async def list_zones_for_camera(
    camera_id: str,
    session: "AsyncSession" = Depends(get_session),
) -> list[ZoneOut]:
    if await session.get(Camera, camera_id) is None:
        raise HTTPException(404, f"camera '{camera_id}' not found")
    stmt = select(Zone).where(Zone.camera_id == camera_id).order_by(Zone.id)
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_out(r) for r in rows]


@camera_zones.post("", response_model=ZoneOut, status_code=status.HTTP_201_CREATED)
async def create_zone(
    camera_id: str,
    payload: ZoneCreate,
    request: Request,
    session: "AsyncSession" = Depends(get_session),
) -> ZoneOut:
    if await session.get(Camera, camera_id) is None:
        raise HTTPException(404, f"camera '{camera_id}' not found")

    _validate_polygon(payload.polygon)

    row = Zone(
        camera_id=camera_id,
        name=payload.name,
        zone_type=payload.zone_type.value,
        polygon_wkt=_points_to_wkt(payload.polygon),
        threshold_seconds=payload.threshold_seconds,
        exit_confirm_seconds=payload.exit_confirm_seconds,
        cooldown_seconds=payload.cooldown_seconds,
        color_hex=payload.color_hex,
        enabled=payload.enabled,
        created_by=request.state.operator_name,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(400, str(exc.orig)) from exc

    await record_audit(
        session,
        action="zone.create",
        target_type="zone",
        target_id=str(row.id),
        operator_name=request.state.operator_name,
        client_ip=request.state.client_ip,
        before=None,
        after=_snapshot(row),
    )
    await session.commit()
    await session.refresh(row)
    logger.info(
        "zone_created",
        zone_id=row.id,
        camera_id=camera_id,
        operator=request.state.operator_name,
    )
    return _to_out(row)


# --------------------------------------------------------------------------- #
# zone-id routes
# --------------------------------------------------------------------------- #
@zones.get("/{zone_id}", response_model=ZoneOut)
async def get_zone(
    zone_id: int,
    session: "AsyncSession" = Depends(get_session),
) -> ZoneOut:
    row = await _get_zone_or_404(session, zone_id)
    return _to_out(row)


@zones.put("/{zone_id}", response_model=ZoneOut)
async def update_zone(
    zone_id: int,
    payload: ZoneUpdate,
    request: Request,
    session: "AsyncSession" = Depends(get_session),
) -> ZoneOut:
    row = await _get_zone_or_404(session, zone_id)
    before = _snapshot(row)

    data = payload.model_dump(exclude_unset=True)
    if (poly := data.pop("polygon", None)) is not None:
        poly_points = [PolygonPoint(**p) if isinstance(p, dict) else p for p in poly]
        _validate_polygon(poly_points)
        row.polygon_wkt = _points_to_wkt(poly_points)
    if (zt := data.pop("zone_type", None)) is not None:
        row.zone_type = zt.value if hasattr(zt, "value") else zt
    for k, v in data.items():
        setattr(row, k, v)

    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(400, str(exc.orig)) from exc

    await record_audit(
        session,
        action="zone.update",
        target_type="zone",
        target_id=str(row.id),
        operator_name=request.state.operator_name,
        client_ip=request.state.client_ip,
        before=before,
        after=_snapshot(row),
    )
    await session.commit()
    await session.refresh(row)
    logger.info("zone_updated", zone_id=row.id, operator=request.state.operator_name)
    return _to_out(row)


@zones.delete("/{zone_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_zone(
    zone_id: int,
    request: Request,
    session: "AsyncSession" = Depends(get_session),
) -> None:
    row = await _get_zone_or_404(session, zone_id)
    before = _snapshot(row)
    await session.delete(row)
    await record_audit(
        session,
        action="zone.delete",
        target_type="zone",
        target_id=str(zone_id),
        operator_name=request.state.operator_name,
        client_ip=request.state.client_ip,
        before=before,
        after=None,
    )
    await session.commit()
    logger.info("zone_deleted", zone_id=zone_id, operator=request.state.operator_name)
