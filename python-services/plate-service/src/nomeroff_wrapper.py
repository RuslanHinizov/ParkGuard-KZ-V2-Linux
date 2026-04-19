"""
python-services/plate-service/src/nomeroff_wrapper.py

Thin adapter around ``nomeroff_net.pipeline('number_plate_detection_and_reading')``.
Kept intentionally small so:

  * tests can inject a fake in place of a real GPU pipeline
  * if Nomeroff 4.x changes its API we only touch this file
  * nomeroff-net + torch (~2 GB) stay out of CI / dev installs

The public contract is a single coroutine-friendly method::

    NomeroffAdapter.read(image_bgr: np.ndarray) -> list[PlateCandidate]

`read` blocks on the torch pipeline, so we offload it to a thread via
``loop.run_in_executor`` from the main consume loop — the Kafka loop
never stalls longer than it takes to hand off a crop.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    import numpy as np

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PlateCandidate:
    """One plate Nomeroff found in the frame."""

    text: str
    confidence: float
    region_name: str                          # "kz" / "kz_box" / "ru" / "eu" / ...
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)   # x, y, w, h in image space
    plate_crop_bgr: "Any | None" = field(default=None, repr=False)


# --------------------------------------------------------------------------- #
# Protocol — lets tests provide a fake without inheriting anything
# --------------------------------------------------------------------------- #
@runtime_checkable
class PlatePipeline(Protocol):
    """Minimal interface the main loop depends on."""

    def read(self, image_bgr: "np.ndarray") -> list[PlateCandidate]: ...


# --------------------------------------------------------------------------- #
# Real adapter (only imported in gpu extra)
# --------------------------------------------------------------------------- #
class NomeroffAdapter:
    """
    Wrap Nomeroff-net's ``number_plate_detection_and_reading`` pipeline.

    The pipeline is lazily constructed on first use so `pip install` in
    CI (without `[gpu]`) does not need torch. Callers should build one
    instance per process and share it across the consume loop (model
    load is ~4s and ~2 GB of VRAM).
    """

    def __init__(self, device: str = "cuda"):
        self._device = device
        self._pipe: Any = None
        self._lock = asyncio.Lock()

    async def ensure_ready(self) -> None:
        if self._pipe is not None:
            return
        async with self._lock:
            if self._pipe is not None:
                return

            # Import inside the method so unit tests that never call
            # `ensure_ready` don't need torch on the PYTHONPATH.
            from nomeroff_net import pipeline  # type: ignore[import-not-found]

            log.info("nomeroff_pipeline_loading device=%s", self._device)
            self._pipe = pipeline(
                "number_plate_detection_and_reading",
                image_loader="opencv",
            )
            log.info("nomeroff_pipeline_ready")

    def read(self, image_bgr: "np.ndarray") -> list[PlateCandidate]:
        """Sync entrypoint — callers wrap in `run_in_executor`."""
        assert self._pipe is not None, "call await ensure_ready() first"
        # Nomeroff 4.0.1 accepts a list of numpy BGR images and returns
        # a tuple of (texts, regions, confidences, plate_coords, plate_images).
        (images, bboxes, zones, region_names,
         region_probs, _mline_boxes, texts) = self._pipe([image_bgr])

        out: list[PlateCandidate] = []
        # The pipeline may detect multiple plates in one image; iterate
        # every (text, region, prob) triple and collect non-empty results.
        for text, region_name, prob, bbox in zip(
            _first_or_empty(texts),
            _first_or_empty(region_names),
            _first_or_empty(region_probs),
            _first_or_empty(bboxes),
            strict=False,
        ):
            if not text:
                continue
            try:
                x1, y1, x2, y2 = (int(v) for v in bbox[:4])
                w, h = max(0, x2 - x1), max(0, y2 - y1)
            except Exception:  # noqa: BLE001 — bbox shape drift from Nomeroff versions
                x1, y1, w, h = 0, 0, 0, 0
            out.append(
                PlateCandidate(
                    text=str(text),
                    confidence=float(prob),
                    region_name=str(region_name),
                    bbox=(x1, y1, w, h),
                )
            )
        return out


def _first_or_empty(xs: Any) -> list[Any]:
    """Nomeroff wraps per-image outputs in a list-of-list; we pass one image."""
    if not xs:
        return []
    try:
        return list(xs[0]) if xs[0] is not None else []
    except Exception:  # noqa: BLE001 — defensive: pipeline output shape varies
        return []


__all__ = ["NomeroffAdapter", "PlateCandidate", "PlatePipeline"]
