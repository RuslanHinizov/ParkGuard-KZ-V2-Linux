"""
python-services/plate-service/src/processor.py

Pure OCR processing: take an ``OCRRequest`` + a ``PlatePipeline`` and
return an ``OCRResult``. No Kafka / no logging framework so it is
trivially unit-testable — the async main loop just wires this into
an aiokafka producer.

Flow:

    OCRRequest
      -> base64 decode → numpy BGR image
      -> pipeline.read(image)   # blocking; caller offloads to executor
      -> pick best candidate (highest confidence)
      -> kz_postprocess.process_candidate()
      -> OCRResult

A single request always produces exactly one ``OCRResult``, even on
failure (so the violation-service's `request_id` -> `cvi_id` lookup
never leaks). Rejected requests produce a nulled result.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass

import cv2
import numpy as np

from shared.schemas import OCRRequest, OCRResult

from src.kz_postprocess import process_candidate
from src.metrics import OCR_REJECTED, OCR_RESULTS
from src.nomeroff_wrapper import PlateCandidate, PlatePipeline

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProcessorConfig:
    min_confidence: float = 0.5
    accepted_regions: frozenset[str] = frozenset({"kz", "kz_box"})


class PlateProcessor:
    def __init__(self, pipeline: PlatePipeline, config: ProcessorConfig | None = None) -> None:
        self._pipe = pipeline
        self._cfg = config or ProcessorConfig()

    async def handle(self, req: OCRRequest) -> OCRResult:
        img = _decode_b64_image(req.vehicle_crop_b64)
        if img is None:
            OCR_REJECTED.labels(reason="decode_error").inc()
            return _null_result(req)

        loop = asyncio.get_running_loop()
        candidates: list[PlateCandidate] = await loop.run_in_executor(
            None, self._pipe.read, img
        )
        if not candidates:
            OCR_REJECTED.labels(reason="no_plate").inc()
            return _null_result(req)

        # Pick the most confident plate.
        best = max(candidates, key=lambda c: c.confidence)

        processed = process_candidate(
            text=best.text,
            confidence=best.confidence,
            region_name=best.region_name,
            accepted_regions=self._cfg.accepted_regions,
            min_confidence=self._cfg.min_confidence,
        )
        if processed is None:
            OCR_REJECTED.labels(reason="low_confidence").inc()
            return _null_result(req, region_name=best.region_name)

        if best.region_name not in self._cfg.accepted_regions:
            OCR_REJECTED.labels(reason="region_not_accepted").inc()
            # Fall through — we still emit the downgraded result so the
            # violation-service can *see* the signal even if it does not
            # count toward the plate-votes majority.

        OCR_RESULTS.labels(
            format=processed.format_type,
            valid=str(processed.valid_format).lower(),
        ).inc()

        return OCRResult(
            request_id=req.request_id,
            cvi_id=req.cvi_id,
            camera_id=req.camera_id,
            plate_text=processed.plate_text,
            plate_confidence=processed.plate_confidence,
            plate_crop_b64=None,  # TODO(Adım 8+): emit plate_crop when snapshot taps it
            region_name=processed.region_name,
            format_type=processed.format_type,
            region_code=processed.region_code,
            valid_format=processed.valid_format,
            is_diplomatic=processed.is_diplomatic,
        )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _decode_b64_image(b64: str) -> np.ndarray | None:
    """Return a BGR numpy image, or None on decode failure."""
    if not b64:
        return None
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception:  # noqa: BLE001 — bad b64 is expected occasionally
        log.warning("ocr_request_b64_decode_failed")
        return None
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None or img.size == 0:
        log.warning("ocr_request_image_decode_failed")
        return None
    return img


def _null_result(req: OCRRequest, *, region_name: str | None = None) -> OCRResult:
    return OCRResult(
        request_id=req.request_id,
        cvi_id=req.cvi_id,
        camera_id=req.camera_id,
        plate_text=None,
        plate_confidence=0.0,
        plate_crop_b64=None,
        region_name=region_name,
        format_type="unknown",
        region_code=None,
        valid_format=False,
        is_diplomatic=False,
    )


__all__ = ["PlateProcessor", "ProcessorConfig"]
