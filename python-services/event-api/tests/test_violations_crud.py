"""
python-services/event-api/tests/test_violations_crud.py

Violations REST endpoint tests (Adım 10). Uses the in-memory SQLite
engine wired by conftest — no Kafka / Redis / MinIO I/O.

Covered:
  - GET  /api/v1/violations        list, filter by camera_id / status / zone_id
  - GET  /api/v1/violations/{id}   fetch one (200 + 404)
  - PATCH /api/v1/violations/{id}  status update → audit trail
  - POST /api/v1/violations/{id}/penalty-card (202 queued, idempotent)
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import insert

from src.models.violation import Violation


# --------------------------------------------------------------------------- #
# Seed helper
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def seeded(session_factory):  # noqa: ANN001
    """Insert two violation rows into the in-memory DB."""
    base_ts = datetime(2026, 4, 17, 10, 0, 0, tzinfo=timezone.utc)
    async with session_factory() as sess:
        v1 = Violation(
            id=1,
            camera_id="cam_01", zone_id=1,
            identity_key="ik_1", cycle_id=0,
            first_seen_in_zone=base_ts, violation_time=base_ts,
            status="pending",
        )
        v1.created_at = base_ts
        v2 = Violation(
            id=2,
            camera_id="cam_02", zone_id=2,
            identity_key="ik_2", cycle_id=0,
            plate_text="123ABC01",
            first_seen_in_zone=base_ts, violation_time=base_ts,
            status="approved", reviewed_by="op1", reviewed_at=base_ts,
        )
        v2.created_at = base_ts
        sess.add(v1)
        sess.add(v2)
        await sess.commit()
    return session_factory


# --------------------------------------------------------------------------- #
# GET list
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_list_all(client, seeded) -> None:  # noqa: ANN001
    r = await client.get("/api/v1/violations")
    assert r.status_code == 200
    assert len(r.json()) == 2


@pytest.mark.asyncio
async def test_list_filter_camera(client, seeded) -> None:  # noqa: ANN001
    r = await client.get("/api/v1/violations?camera_id=cam_01")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["camera_id"] == "cam_01"


@pytest.mark.asyncio
async def test_list_filter_status(client, seeded) -> None:  # noqa: ANN001
    r = await client.get("/api/v1/violations?status=approved")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["plate_text"] == "123ABC01"


@pytest.mark.asyncio
async def test_list_filter_zone(client, seeded) -> None:  # noqa: ANN001
    r = await client.get("/api/v1/violations?zone_id=2")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["camera_id"] == "cam_02"


# --------------------------------------------------------------------------- #
# GET single
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_get_existing(client, seeded) -> None:  # noqa: ANN001
    r = await client.get("/api/v1/violations/1")
    assert r.status_code == 200
    assert r.json()["id"] == 1


@pytest.mark.asyncio
async def test_get_missing_returns_404(client, seeded) -> None:  # noqa: ANN001
    r = await client.get("/api/v1/violations/9999")
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# PATCH status
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_patch_status_to_approved(client, seeded, operator_headers) -> None:  # noqa: ANN001
    r = await client.patch(
        "/api/v1/violations/1",
        json={"status": "approved"},
        headers=operator_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "approved"
    assert body["reviewed_by"] == "pytest-operator"
    assert body["reviewed_at"] is not None


@pytest.mark.asyncio
async def test_patch_missing_violation(client, seeded, operator_headers) -> None:  # noqa: ANN001
    r = await client.patch(
        "/api/v1/violations/9999",
        json={"status": "approved"},
        headers=operator_headers,
    )
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# POST penalty-card
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_penalty_card_queued(
    client, seeded, monkeypatch, operator_headers,
) -> None:  # noqa: ANN001
    import shared.kafka_client as kc

    published: list[dict] = []

    async def _mock_send(topic, value, **_kw):  # noqa: ANN001
        published.append({"topic": topic, "value": value})

    monkeypatch.setattr(kc, "send_json", _mock_send)

    r = await client.post("/api/v1/violations/1/penalty-card",
                          headers=operator_headers)
    assert r.status_code == 202
    assert r.json()["status"] == "queued"
    assert len(published) == 1
    assert published[0]["value"]["violation_id"] == 1


@pytest.mark.asyncio
async def test_penalty_card_already_issued(
    client, seeded, monkeypatch, operator_headers,
) -> None:  # noqa: ANN001
    import shared.kafka_client as kc

    async def _mock_send(*_a, **_kw) -> None:
        pass

    monkeypatch.setattr(kc, "send_json", _mock_send)

    # violation 2 already has a card url
    async with seeded() as sess:
        v = await sess.get(Violation, 2)
        v.penalty_card_url = "http://minio/penalty-cards/cam_02/2026/04/17/2.pdf"
        await sess.commit()

    r = await client.post("/api/v1/violations/2/penalty-card",
                          headers=operator_headers)
    assert r.status_code == 202
    assert r.json()["status"] == "already_issued"
    assert "url" in r.json()
