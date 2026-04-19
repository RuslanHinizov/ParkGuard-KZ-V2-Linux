"""
python-services/plate-service/tests/test_processor.py

End-to-end (pure Python, no Kafka, no torch) test of
:class:`PlateProcessor` with a scripted Nomeroff stand-in. Covers the
full OCRRequest → OCRResult contract the violation-service depends on.
"""

from __future__ import annotations

import pytest
from uuid import uuid4

from shared.schemas import OCRRequest

from src.nomeroff_wrapper import PlateCandidate
from src.processor import PlateProcessor, ProcessorConfig


def _make_request(crop_b64: str) -> OCRRequest:
    return OCRRequest(
        request_id=uuid4(),
        cvi_id=uuid4(),
        camera_id="cam_01",
        attempt_no=1,
        vehicle_crop_b64=crop_b64,
    )


@pytest.mark.asyncio
async def test_happy_path_produces_valid_ocr_result(fake_pipeline, jpeg_crop_b64):
    fake_pipeline.enqueue([
        PlateCandidate(text="123ABC02", confidence=0.91, region_name="kz"),
    ])
    proc = PlateProcessor(fake_pipeline)

    req = _make_request(jpeg_crop_b64)
    result = await proc.handle(req)

    assert result.plate_text == "123ABC02"
    assert result.format_type == "kz_new"
    assert result.region_code == "02"
    assert result.region_name == "kz"
    assert result.valid_format is True
    assert result.request_id == req.request_id
    assert result.cvi_id == req.cvi_id
    assert result.camera_id == "cam_01"
    assert len(fake_pipeline.calls) == 1


@pytest.mark.asyncio
async def test_decode_error_returns_nulled_result(fake_pipeline):
    proc = PlateProcessor(fake_pipeline)
    # Valid base64 but not an image — round-trip with a null result
    # rather than dropping the message (violation-service depends on
    # request_id round-trip).
    req = _make_request("bm9uLWltYWdlLWRhdGE=")  # "non-image-data"
    result = await proc.handle(req)

    assert result.plate_text is None
    assert result.plate_confidence == 0.0
    assert result.valid_format is False
    assert result.request_id == req.request_id
    assert fake_pipeline.calls == []  # pipeline never invoked


@pytest.mark.asyncio
async def test_no_plate_detected_returns_nulled_result(fake_pipeline, jpeg_crop_b64):
    fake_pipeline.enqueue([])  # Nomeroff found no plates
    proc = PlateProcessor(fake_pipeline)

    req = _make_request(jpeg_crop_b64)
    result = await proc.handle(req)

    assert result.plate_text is None
    assert result.format_type == "unknown"
    assert result.request_id == req.request_id


@pytest.mark.asyncio
async def test_best_candidate_wins_when_multiple_plates_detected(fake_pipeline, jpeg_crop_b64):
    fake_pipeline.enqueue([
        PlateCandidate(text="777XYZ03", confidence=0.62, region_name="kz"),
        PlateCandidate(text="123ABC02", confidence=0.95, region_name="kz"),
    ])
    proc = PlateProcessor(fake_pipeline)

    result = await proc.handle(_make_request(jpeg_crop_b64))
    assert result.plate_text == "123ABC02"


@pytest.mark.asyncio
async def test_foreign_region_still_emitted_with_halved_confidence(fake_pipeline, jpeg_crop_b64):
    # Nomeroff misclassifies a valid KZ plate as "ru" — we emit the
    # downgraded result so the violation-service has a signal, but
    # the confidence drop prevents it from dominating plate_votes.
    fake_pipeline.enqueue([
        PlateCandidate(text="123ABC02", confidence=0.9, region_name="ru"),
    ])
    proc = PlateProcessor(fake_pipeline)

    result = await proc.handle(_make_request(jpeg_crop_b64))
    assert result.plate_text == "123ABC02"
    assert result.plate_confidence == pytest.approx(0.45, abs=1e-6)
    assert result.region_name == "ru"
    assert result.valid_format is True


@pytest.mark.asyncio
async def test_custom_min_confidence_threshold(fake_pipeline, jpeg_crop_b64):
    fake_pipeline.enqueue([
        PlateCandidate(text="garbage", confidence=0.10, region_name="kz"),
    ])
    proc = PlateProcessor(fake_pipeline, config=ProcessorConfig(min_confidence=0.5))

    result = await proc.handle(_make_request(jpeg_crop_b64))
    # Below threshold and invalid format → null result
    assert result.plate_text is None
    assert result.plate_confidence == 0.0
