"""
Zones CRUD tests. Each mutation also verifies an audit_log row is written.
Polygon validity is asserted against shapely (spec §15.2).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select


CAM_PAYLOAD = {
    "id": "cam_01",
    "name": "Entrance",
    "rtsp_substream_url": "rtsp://demo/sub",
    "enabled": True,
}

ZONE_PAYLOAD = {
    "name": "No-parking lane A",
    "zone_type": "no_parking",
    "polygon": [
        {"x": 100, "y": 100},
        {"x": 400, "y": 100},
        {"x": 400, "y": 300},
        {"x": 100, "y": 300},
    ],
    "threshold_seconds": 25,
    "exit_confirm_seconds": 10,
    "cooldown_seconds": 10,
    "color_hex": "#FF0000",
    "enabled": True,
}


@pytest.fixture
async def seeded_camera(client, operator_headers):  # noqa: ANN001
    r = await client.post("/api/v1/cameras", json=CAM_PAYLOAD, headers=operator_headers)
    assert r.status_code == 201
    return CAM_PAYLOAD["id"]


@pytest.mark.asyncio
async def test_create_and_list(client, seeded_camera, operator_headers) -> None:  # noqa: ANN001
    r = await client.post(
        f"/api/v1/cameras/{seeded_camera}/zones",
        json=ZONE_PAYLOAD,
        headers=operator_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["camera_id"] == seeded_camera
    assert body["name"] == ZONE_PAYLOAD["name"]
    assert len(body["polygon"]) == 4

    r2 = await client.get(f"/api/v1/cameras/{seeded_camera}/zones")
    assert r2.status_code == 200
    assert len(r2.json()) == 1


@pytest.mark.asyncio
async def test_rejects_degenerate_polygon(client, seeded_camera, operator_headers) -> None:  # noqa: ANN001
    bad = {**ZONE_PAYLOAD, "polygon": [{"x": 0, "y": 0}, {"x": 1, "y": 1}, {"x": 2, "y": 2}]}
    r = await client.post(
        f"/api/v1/cameras/{seeded_camera}/zones",
        json=bad,
        headers=operator_headers,
    )
    assert r.status_code == 400
    assert "polygon" in r.json()["detail"]


@pytest.mark.asyncio
async def test_update_polygon_and_threshold(client, seeded_camera, operator_headers) -> None:  # noqa: ANN001
    r = await client.post(
        f"/api/v1/cameras/{seeded_camera}/zones",
        json=ZONE_PAYLOAD,
        headers=operator_headers,
    )
    zid = r.json()["id"]

    r2 = await client.put(
        f"/api/v1/zones/{zid}",
        json={
            "threshold_seconds": 60,
            "polygon": [
                {"x": 10, "y": 10},
                {"x": 200, "y": 10},
                {"x": 200, "y": 200},
                {"x": 10, "y": 200},
            ],
        },
        headers=operator_headers,
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["threshold_seconds"] == 60
    xs = [p["x"] for p in body["polygon"]]
    assert max(xs) == 200


@pytest.mark.asyncio
async def test_delete(client, seeded_camera, operator_headers) -> None:  # noqa: ANN001
    r = await client.post(
        f"/api/v1/cameras/{seeded_camera}/zones",
        json=ZONE_PAYLOAD,
        headers=operator_headers,
    )
    zid = r.json()["id"]
    r2 = await client.delete(f"/api/v1/zones/{zid}", headers=operator_headers)
    assert r2.status_code == 204
    r3 = await client.get(f"/api/v1/zones/{zid}")
    assert r3.status_code == 404


@pytest.mark.asyncio
async def test_created_by_set_from_header(client, seeded_camera, operator_headers) -> None:  # noqa: ANN001
    r = await client.post(
        f"/api/v1/cameras/{seeded_camera}/zones",
        json=ZONE_PAYLOAD,
        headers=operator_headers,
    )
    assert r.json()["created_by"] == "pytest-operator"


@pytest.mark.asyncio
async def test_missing_camera_404(client, operator_headers) -> None:  # noqa: ANN001
    r = await client.post(
        "/api/v1/cameras/does_not_exist/zones",
        json=ZONE_PAYLOAD,
        headers=operator_headers,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_audit_rows_written(client, seeded_camera, operator_headers, session_factory) -> None:  # noqa: ANN001
    from src.models.audit_log import AuditLog

    r = await client.post(
        f"/api/v1/cameras/{seeded_camera}/zones",
        json=ZONE_PAYLOAD,
        headers=operator_headers,
    )
    zid = r.json()["id"]
    await client.put(
        f"/api/v1/zones/{zid}",
        json={"enabled": False},
        headers=operator_headers,
    )
    await client.delete(f"/api/v1/zones/{zid}", headers=operator_headers)

    async with session_factory() as s:
        rows = (
            await s.execute(
                select(AuditLog)
                .where(AuditLog.target_type == "zone")
                .order_by(AuditLog.id)
            )
        ).scalars().all()
    assert [r.action for r in rows] == ["zone.create", "zone.update", "zone.delete"]
    assert all(r.operator_name == "pytest-operator" for r in rows)
