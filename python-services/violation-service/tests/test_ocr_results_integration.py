"""
python-services/violation-service/tests/test_ocr_results_integration.py

Adım 7 — OCR result feedback loop. The plate-service publishes an
`OCRResult` keyed by cvi_id; the violation-service folds it into the
target CVI's plate_votes Counter via CVIManager.apply_ocr_result.
These tests cover the three interesting states:

  * Vote-only (below majority) — CVI records the vote but keeps
    `plate_text` pristine.
  * Stabilised (crosses majority) — `plate_text` gets set.
  * Missing CVI — graceful no-op; no exception, no bogus row.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.cvi_manager import CVIManager
from tests._helpers import make_obs, stamped


NOW = datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_ocr_vote_below_majority_is_recorded_not_stabilised():
    mgr = CVIManager(redis=None)
    cvi, _ = await mgr.resolve(make_obs(NOW, ds_track_id=7))

    stabilised = await mgr.apply_ocr_result(
        cvi_id=cvi.cvi_id,
        plate_text="123ABC02",
        plate_confidence=0.9,
    )
    assert stabilised is False
    assert cvi.plate_text is None        # under majority
    assert cvi.plate_votes["123ABC02"] == 1


@pytest.mark.asyncio
async def test_three_votes_stabilise_plate_text():
    mgr = CVIManager(redis=None)
    cvi, _ = await mgr.resolve(make_obs(NOW, ds_track_id=7))

    # Three matching OCR results crossing the majority threshold.
    results = [False, False, False]
    for i in range(3):
        results[i] = await mgr.apply_ocr_result(
            cvi_id=cvi.cvi_id,
            plate_text="123ABC02",
            plate_confidence=0.9,
        )

    assert results == [False, False, True]
    assert cvi.plate_text == "123ABC02"
    assert cvi.plate_confidence == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_low_confidence_ocr_result_is_discarded():
    mgr = CVIManager(redis=None)
    cvi, _ = await mgr.resolve(make_obs(NOW, ds_track_id=7))

    # Below PLATE_MIN_CONFIDENCE — plate-service sends it anyway so we
    # can *see* the signal in logs, but CVI must ignore it.
    for _ in range(5):
        stabilised = await mgr.apply_ocr_result(
            cvi_id=cvi.cvi_id,
            plate_text="garbage",
            plate_confidence=0.3,
        )
        assert stabilised is False

    assert cvi.plate_text is None
    assert cvi.plate_votes == {}


@pytest.mark.asyncio
async def test_ocr_result_for_missing_cvi_is_noop():
    mgr = CVIManager(redis=None)

    stabilised = await mgr.apply_ocr_result(
        cvi_id=uuid4(),
        plate_text="123ABC02",
        plate_confidence=0.9,
    )
    assert stabilised is False     # no CVI, no stabilisation
    # Not an error — the CVI may simply have been evicted.


@pytest.mark.asyncio
async def test_ocr_then_subsequent_observation_merges_correctly():
    mgr = CVIManager(redis=None)
    cvi, _ = await mgr.resolve(make_obs(NOW, ds_track_id=7))

    # Two OCR votes…
    await mgr.apply_ocr_result(cvi_id=cvi.cvi_id, plate_text="123ABC02", plate_confidence=0.9)
    await mgr.apply_ocr_result(cvi_id=cvi.cvi_id, plate_text="123ABC02", plate_confidence=0.9)

    # …then a detection with a bundled plate: the 3rd vote stabilises.
    await mgr.resolve(make_obs(
        stamped(NOW, seconds=1.0),
        ds_track_id=7,
        plate="123ABC02",
        plate_conf=0.9,
    ))

    assert cvi.plate_text == "123ABC02"
    assert cvi.plate_votes["123ABC02"] >= 3
