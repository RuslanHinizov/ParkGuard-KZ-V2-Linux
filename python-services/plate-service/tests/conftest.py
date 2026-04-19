"""
python-services/plate-service/tests/conftest.py

A fake PlatePipeline lets us unit-test the full consume→process→produce
path without pulling torch / Nomeroff onto the dev box (Windows, no CUDA).
Tests enqueue scripted responses and the processor treats them exactly
as it would a real Nomeroff result.
"""

from __future__ import annotations

import base64
import sys
from collections import deque
from pathlib import Path

import cv2
import numpy as np
import pytest

# Make `src` and `shared` importable without installing the package.
REPO_ROOT = Path(__file__).resolve().parents[3]
for p in (REPO_ROOT / "python-services", REPO_ROOT / "python-services" / "plate-service"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from src.nomeroff_wrapper import PlateCandidate, PlatePipeline  # noqa: E402


class FakePipeline:
    """Scripted stand-in for :class:`NomeroffAdapter`."""

    def __init__(self) -> None:
        self._script: deque[list[PlateCandidate]] = deque()
        self.calls: list[np.ndarray] = []

    def enqueue(self, candidates: list[PlateCandidate]) -> None:
        self._script.append(candidates)

    def read(self, image_bgr: np.ndarray) -> list[PlateCandidate]:
        self.calls.append(image_bgr)
        if not self._script:
            return []
        return self._script.popleft()


def _assert_protocol() -> None:
    # Runtime sanity — if FakePipeline ever diverges from the Protocol
    # the processor uses, tests should fail loudly here, not deep in a
    # flaky downstream assertion.
    assert isinstance(FakePipeline(), PlatePipeline)


_assert_protocol()


@pytest.fixture
def fake_pipeline() -> FakePipeline:
    return FakePipeline()


@pytest.fixture
def jpeg_crop_b64() -> str:
    """A tiny valid JPEG — passes through cv2.imdecode cleanly."""
    img = np.full((48, 96, 3), 200, dtype=np.uint8)
    # Draw a dark strip to give JPEG something to encode (pure white
    # occasionally produces a degenerate buffer cv2 won't redecode).
    img[18:30, 12:84] = 20
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return base64.b64encode(buf.tobytes()).decode("ascii")
