"""
python-services/plate-service/src/model_warmup.py

Spec §9.5 — warm up Nomeroff on process start so the first real OCR
doesn't stall. Nomeroff 4.0.1 with torch lazy-init on GPU takes 3-6s
for the first forward pass; without warmup that latency leaks into
the first real violation and violation-service's 5s retry timer
fires a duplicate.

We feed 5 synthetic images (random KZ-ish pixel patterns) since the
spec doesn't mandate real plate shots and we don't want to bake a
binary fixture into the repo.
"""

from __future__ import annotations

import asyncio
import logging
import time

import numpy as np

from src.nomeroff_wrapper import PlatePipeline

log = logging.getLogger(__name__)

WARMUP_FRAMES: int = 5


async def warmup(pipeline: PlatePipeline, *, frames: int = WARMUP_FRAMES) -> float:
    """Run `frames` inferences on synthetic images. Returns total seconds."""
    loop = asyncio.get_running_loop()
    started = time.perf_counter()
    for i in range(frames):
        img = _synthetic_kz_ish_frame(seed=i)
        await loop.run_in_executor(None, pipeline.read, img)
    elapsed = time.perf_counter() - started
    log.info("nomeroff_warmup_done frames=%d elapsed=%.2fs", frames, elapsed)
    return elapsed


def _synthetic_kz_ish_frame(seed: int, width: int = 640, height: int = 480) -> np.ndarray:
    """Make a deterministic BGR frame that has *some* structure —
    pure noise occasionally confuses Nomeroff's detector so badly it
    skips the warmup forward pass entirely."""
    rng = np.random.default_rng(seed)
    img = rng.integers(40, 200, size=(height, width, 3), dtype=np.uint8)
    # Draw a crude plate-shaped rectangle so the detector branch runs.
    y0, x0 = height // 2 - 30, width // 2 - 80
    img[y0:y0 + 60, x0:x0 + 160] = 240  # white-ish plate bg
    return img


__all__ = ["WARMUP_FRAMES", "warmup"]
