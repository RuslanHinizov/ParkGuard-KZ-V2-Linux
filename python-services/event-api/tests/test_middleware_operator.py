"""
Tests for OperatorContextMiddleware — spec §11 invariant:
  * mutations require X-Operator-Name (400 if missing)
  * GET/HEAD/OPTIONS tolerate its absence
  * /healthz /readyz /metrics /docs are exempt
  * response always carries X-Trace-Id
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_healthz_exempt_without_header(client) -> None:  # noqa: ANN001
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert "x-trace-id" in {k.lower() for k in r.headers}


@pytest.mark.asyncio
async def test_get_allows_missing_operator(client) -> None:  # noqa: ANN001
    r = await client.get("/api/v1/cameras")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_post_without_operator_rejected(client) -> None:  # noqa: ANN001
    r = await client.post(
        "/api/v1/cameras",
        json={
            "id": "cam_a",
            "name": "A",
            "rtsp_substream_url": "rtsp://x/a",
        },
    )
    assert r.status_code == 400
    assert "X-Operator-Name" in r.json()["detail"]


@pytest.mark.asyncio
async def test_trace_id_echoed(client) -> None:  # noqa: ANN001
    r = await client.get("/healthz", headers={"X-Trace-Id": "deadbeef"})
    assert r.headers["X-Trace-Id"] == "deadbeef"
