"""
python-services/violation-service/src/identity.py

Identity-key derivation (spec §5.1). Deterministic: the same CVI state
maps to the same key on every call, so duplicate violation INSERTs are
caught by the DB unique constraint even across process restarts.

Two tracks:
  1. plate-backed  →  `plate:{normalized_text}`
  2. embedding-backed (fallback) → `emb:{camera_id}:{quantized_sha256[:16]}`

Embedding quantization rounds to precision=2 decimals — per spec §15.1
Risk "identity_key deterministik değil" mitigation.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import numpy as np

from shared.plate_normalize import normalize_kz_plate

if TYPE_CHECKING:
    from src.cvi import CVI

PLATE_VOTE_MAJORITY_FOR_IDENTITY = 3      # spec §5.1


def get_violation_identity(cvi: CVI) -> str:
    """Return the deterministic identity_key for this CVI."""
    plate = cvi.plate_text
    if plate and cvi.plate_votes.get(plate, 0) >= PLATE_VOTE_MAJORITY_FOR_IDENTITY:
        normalised = normalize_kz_plate(plate).text or plate
        return f"plate:{normalised}"

    # Embedding-backed fallback. If we never got an embedding, anchor
    # to the ds_track_id so at least *some* identity is written — worst
    # case, this collapses to the DeepStream track id.
    if cvi.embedding_centroid is not None:
        quantized = np.round(cvi.embedding_centroid, decimals=2).astype(np.float32)
        digest = hashlib.sha256(quantized.tobytes()).hexdigest()[:16]
        return f"emb:{cvi.camera_id}:{digest}"

    return f"track:{cvi.camera_id}:{cvi.last_ds_track_id}"


__all__ = ["PLATE_VOTE_MAJORITY_FOR_IDENTITY", "get_violation_identity"]
