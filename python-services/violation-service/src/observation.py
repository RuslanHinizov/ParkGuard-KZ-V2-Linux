"""
python-services/violation-service/src/observation.py

`Observation` is what one DetectedObject becomes once the Kafka envelope
has been validated + decoded (base64 embedding → numpy) and augmented
with whatever the most recent ocr_result hop gave us. It is the value
type the CVI manager and the state machine consume.

Kept outside `cvi.py` so unit tests can build Observations without
importing the whole Redis-backed CVI module.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from shared.geometry import BBox
from shared.schemas import DetectedObject, DetectionMessage

EMBEDDING_DIM = 128


@dataclass(slots=True)
class Observation:
    camera_id: str
    ds_track_id: int
    frame_id: int
    timestamp: datetime
    bbox: BBox
    centroid: tuple[int, int]
    class_name: str
    confidence: float
    embedding: np.ndarray | None = None
    plate_text: str | None = None
    plate_conf: float | None = None
    plate_format: str | None = None
    plate_region_code: str | None = None
    plate_valid_format: bool = False
    is_diplomatic: bool = False

    # Convenience — callers sometimes attach the DB zone row before
    # handing the observation to the state machine.
    zone_hits: list[int] = field(default_factory=list)

    @classmethod
    def from_detection(
        cls,
        msg: DetectionMessage,
        obj: DetectedObject,
    ) -> Observation:
        """Build an Observation from a (message, object) pair."""
        emb = _decode_embedding(obj.embedding_b64)
        bbox = BBox(x=obj.bbox.x, y=obj.bbox.y, w=obj.bbox.w, h=obj.bbox.h)
        return cls(
            camera_id=msg.sensor_id,
            ds_track_id=obj.ds_track_id,
            frame_id=msg.frame_id,
            timestamp=msg.timestamp,
            bbox=bbox,
            centroid=(obj.centroid.x, obj.centroid.y),
            class_name=obj.class_name,
            confidence=obj.confidence,
            embedding=emb,
        )


def _decode_embedding(b64: str | None) -> np.ndarray | None:
    """Decode 128-D float32 OSNet embedding from base64. Returns None on any error."""
    if not b64:
        return None
    try:
        raw = base64.b64decode(b64)
        arr = np.frombuffer(raw, dtype=np.float32)
    except Exception:  # noqa: BLE001 — malformed payload; callers treat as missing
        return None
    if arr.size != EMBEDDING_DIM:
        return None
    n = float(np.linalg.norm(arr))
    if n <= 0.0:
        return None
    return (arr / n).astype(np.float32, copy=False)


__all__ = ["EMBEDDING_DIM", "Observation"]
