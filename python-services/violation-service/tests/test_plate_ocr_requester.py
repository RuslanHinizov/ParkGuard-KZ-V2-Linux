"""
python-services/violation-service/tests/test_plate_ocr_requester.py

Focused unit tests for the candidate-selection side of PlateOCRRequester
(Adım 8). The Kafka I/O is wrapped by the published helpers, so here we
just check the filter matrix:

    - plate already stabilised     → skipped
    - no last_bbox                 → skipped
    - OUTSIDE every zone           → skipped
    - inside attempt-gap window    → skipped
    - max attempts reached         → skipped
    - qualifying CVI               → picked

Full end-to-end wiring (send_json / minio round-trip) needs live Kafka +
MinIO; those land in the dockerised integration test suite (spec §12.5).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from shared.geometry import BBox
from shared.schemas import CVIState
from src.cvi import CVI, ZoneStateEntry
from src.plate_ocr_requester import PlateOCRRequester


def _make_cvi(
    *,
    plate_text: str | None = None,
    bbox: BBox | None = BBox(x=100, y=200, w=80, h=60),
    zone_state: CVIState | None = CVIState.INSIDE_ZONE,
) -> CVI:
    now = datetime.now(tz=timezone.utc)
    c = CVI(
        cvi_id=uuid4(),
        camera_id="cam_01",
        first_seen=now,
        last_seen=now,
        plate_text=plate_text,
        last_bbox=bbox,
    )
    if zone_state is not None:
        c.state_per_zone[1] = ZoneStateEntry(state=zone_state)
    return c


def _requester(cvis: list[CVI]) -> PlateOCRRequester:
    manager = SimpleNamespace(_cvis={c.cvi_id: c for c in cvis})
    # shutdown_event is only consulted by loops, not the selector.
    return PlateOCRRequester(
        cvi_manager=manager,          # type: ignore[arg-type]
        shutdown_event=asyncio.Event(),
        interval_seconds=0.01,
        attempt_gap_seconds=5,
        max_attempts_per_cvi=3,
    )


def test_candidate_selection_happy_path() -> None:
    cvi = _make_cvi()
    req = _requester([cvi])
    picked = req._select_candidates(datetime.now(tz=timezone.utc))
    assert [c.cvi_id for c in picked] == [cvi.cvi_id]


def test_candidate_skipped_when_plate_stabilised() -> None:
    cvi = _make_cvi(plate_text="123ABC01")
    req = _requester([cvi])
    assert req._select_candidates(datetime.now(tz=timezone.utc)) == []


def test_candidate_skipped_when_no_bbox() -> None:
    cvi = _make_cvi(bbox=None)
    req = _requester([cvi])
    assert req._select_candidates(datetime.now(tz=timezone.utc)) == []


@pytest.mark.parametrize("state", [CVIState.OUTSIDE,
                                    CVIState.EXIT_CANDIDATE,
                                    CVIState.COOLDOWN])
def test_candidate_skipped_when_not_in_zone(state: CVIState) -> None:
    cvi = _make_cvi(zone_state=state)
    req = _requester([cvi])
    assert req._select_candidates(datetime.now(tz=timezone.utc)) == []


def test_candidate_included_when_violated() -> None:
    # A CVI can stay in VIOLATED without a plate (no OCR hit yet) — we must
    # keep asking so the penalty card eventually gets a plate.
    cvi = _make_cvi(zone_state=CVIState.VIOLATED)
    req = _requester([cvi])
    picked = req._select_candidates(datetime.now(tz=timezone.utc))
    assert [c.cvi_id for c in picked] == [cvi.cvi_id]


def test_candidate_rate_limited_by_attempt_gap() -> None:
    cvi = _make_cvi()
    req = _requester([cvi])
    now = datetime.now(tz=timezone.utc)
    # Pretend we just asked 2 seconds ago (< 5s gap).
    req._last_attempt_at[cvi.cvi_id] = now - timedelta(seconds=2)
    assert req._select_candidates(now) == []
    # Advance past the gap → picked again.
    assert [c.cvi_id for c in req._select_candidates(now + timedelta(seconds=6))] == [cvi.cvi_id]


def test_candidate_capped_by_max_attempts() -> None:
    cvi = _make_cvi()
    req = _requester([cvi])
    req._attempts_count[cvi.cvi_id] = 3   # hit the cap
    assert req._select_candidates(datetime.now(tz=timezone.utc)) == []
