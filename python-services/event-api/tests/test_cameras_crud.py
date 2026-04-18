"""
Cameras CRUD round-trip tests. Each mutation also verifies an audit_log
row was written by the service-layer helper.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select


CAM_PAYLOAD = {
    "id": "cam_01",
    "name": "Entrance",
    "rtsp_substream_url": "rtsp://demo:demo@127.0.0.1:554/sub",
    "rtsp_mainstream_url": "rtsp://demo:demo@127.0.0.1:554/main",
    "location_description": "Building A entrance",
    "enabled": True,
    "config": {"fps_cap": 15},
}


@pytest.mark.asyncio
async def test_post_then_get(client, operator_headers) -> None:  # noqa: ANN001
    r = await client.post("/api/v1/cameras", json=CAM_PAYLOAD, headers=operator_headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"] == "cam_01"
    assert body["config"] == {"fps_cap": 15}

    r2 = await client.get("/api/v1/cameras/cam_01")
    assert r2.status_code == 200
    assert r2.json()["name"] == "Entrance"


@pytest.mark.asyncio
async def test_post_conflict_on_duplicate(client, operator_headers) -> None:  # noqa: ANN001
    r = await client.post("/api/v1/cameras", json=CAM_PAYLOAD, headers=operator_headers)
    assert r.status_code == 201
    r2 = await client.post("/api/v1/cameras", json=CAM_PAYLOAD, headers=operator_headers)
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_put_creates_if_missing(client, operator_headers) -> None:  # noqa: ANN001
    r = await client.put(
        "/api/v1/cameras/cam_new",
        json={**CAM_PAYLOAD, "id": "cam_new", "name": "New"},
        headers=operator_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "New"


@pytest.mark.asyncio
async def test_put_updates_if_present(client, operator_headers) -> None:  # noqa: ANN001
    await client.post("/api/v1/cameras", json=CAM_PAYLOAD, headers=operator_headers)
    r = await client.put(
        "/api/v1/cameras/cam_01",
        json={"name": "Renamed", "enabled": False},
        headers=operator_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Renamed"
    assert body["enabled"] is False
    # unchanged fields stay intact
    assert body["rtsp_substream_url"] == CAM_PAYLOAD["rtsp_substream_url"]


@pytest.mark.asyncio
async def test_delete_removes_and_404s(client, operator_headers) -> None:  # noqa: ANN001
    await client.post("/api/v1/cameras", json=CAM_PAYLOAD, headers=operator_headers)
    r = await client.delete("/api/v1/cameras/cam_01", headers=operator_headers)
    assert r.status_code == 204
    r2 = await client.get("/api/v1/cameras/cam_01")
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_list_filter_by_enabled(client, operator_headers) -> None:  # noqa: ANN001
    await client.post(
        "/api/v1/cameras",
        json={**CAM_PAYLOAD, "id": "cam_on", "enabled": True},
        headers=operator_headers,
    )
    await client.post(
        "/api/v1/cameras",
        json={**CAM_PAYLOAD, "id": "cam_off", "enabled": False},
        headers=operator_headers,
    )
    r = await client.get("/api/v1/cameras?enabled=true")
    ids = {c["id"] for c in r.json()}
    assert ids == {"cam_on"}


@pytest.mark.asyncio
async def test_audit_row_written(client, operator_headers, session_factory) -> None:  # noqa: ANN001
    """Every mutation records an audit_log row with the operator name."""
    from src.models.audit_log import AuditLog  # late import so models are registered

    await client.post("/api/v1/cameras", json=CAM_PAYLOAD, headers=operator_headers)
    await client.put(
        "/api/v1/cameras/cam_01",
        json={"name": "Renamed"},
        headers=operator_headers,
    )
    await client.delete("/api/v1/cameras/cam_01", headers=operator_headers)

    async with session_factory() as s:
        rows = (await s.execute(select(AuditLog).order_by(AuditLog.id))).scalars().all()

    actions = [r.action for r in rows]
    assert actions == ["camera.create", "camera.update", "camera.delete"]
    assert all(r.operator_name == "pytest-operator" for r in rows)
    assert all(r.target_type == "camera" and r.target_id == "cam_01" for r in rows)


@pytest.mark.asyncio
async def test_id_pattern_rejected(client, operator_headers) -> None:  # noqa: ANN001
    bad = {**CAM_PAYLOAD, "id": "bad id!"}
    r = await client.post("/api/v1/cameras", json=bad, headers=operator_headers)
    assert r.status_code == 422
