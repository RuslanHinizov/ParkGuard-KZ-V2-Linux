"""
python-services/plate-service/src/kz_postprocess.py

Spec §9.4 — post-process a Nomeroff-net candidate into the canonical
``OCRResult`` shape the violation-service consumes.

Steps:

  1. Region filter: accept only ``kz`` / ``kz_box``. Foreign plates get
     confidence × 0.5 (spec §9.4 + §16.5 risk table).
  2. Normalize via :func:`shared.plate_normalize.normalize_kz_plate`
     (Cyrillic → Latin, position-aware OCR confusion fix, format
     detection).
  3. Reject if post-normalize confidence < threshold (default 0.5).

Returns a :class:`PostProcessed` or ``None`` — callers map ``None`` to
an ``OCRResult(plate_text=None, plate_confidence=0.0, valid_format=False)``
so the violation-service still sees a ``request_id`` round-trip.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.plate_normalize import (
    PlateFormat,
    extract_region_code,
    normalize_kz_plate,
)

ACCEPTED_REGIONS: frozenset[str] = frozenset({"kz", "kz_box"})
FOREIGN_REGION_PENALTY: float = 0.5     # spec §9.4


@dataclass(frozen=True, slots=True)
class PostProcessed:
    plate_text: str | None
    plate_confidence: float
    region_name: str
    format_type: PlateFormat
    region_code: str | None
    valid_format: bool
    is_diplomatic: bool


def process_candidate(
    *,
    text: str,
    confidence: float,
    region_name: str,
    accepted_regions: frozenset[str] = ACCEPTED_REGIONS,
    min_confidence: float = 0.5,
) -> PostProcessed | None:
    """Apply spec §9.4 post-process. Returns None if rejected."""
    # 1. Region filter — downgrade, don't drop. Foreign plates still
    #    feed into the CVI with a *signal* (useful as a weak tiebreak).
    if region_name not in accepted_regions:
        confidence *= FOREIGN_REGION_PENALTY

    # 2. Normalize.
    normalized = normalize_kz_plate(text)

    # 3. Confidence gate on the *normalized* confidence.
    if confidence < min_confidence and not normalized.is_valid:
        return None

    return PostProcessed(
        plate_text=normalized.text,
        plate_confidence=round(confidence, 4),
        region_name=region_name,
        format_type=normalized.format_type,
        region_code=extract_region_code(normalized.text, normalized.format_type),
        valid_format=normalized.is_valid,
        is_diplomatic=normalized.is_diplomatic,
    )


__all__ = ["ACCEPTED_REGIONS", "FOREIGN_REGION_PENALTY", "PostProcessed", "process_candidate"]
