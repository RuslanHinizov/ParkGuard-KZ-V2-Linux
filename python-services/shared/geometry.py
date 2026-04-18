"""
python-services/shared/geometry.py

Geometric primitives for zone evaluation. All polygon operations go through
Shapely — hand-rolled ray casting is banned (spec §15.2). Coordinates are
always in **image-space pixels** (spec §15.5, Risk 5) — the camera frame
coordinate. Frontend stores/edits in image-space and applies zoom-aware
rendering.
"""

from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry import Point, Polygon


@dataclass(frozen=True, slots=True)
class BBox:
    """Axis-aligned bounding box in image-space (pixels)."""

    x: int
    y: int
    w: int
    h: int

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h

    @property
    def centroid(self) -> tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)

    @property
    def area(self) -> int:
        return self.w * self.h

    def as_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


def bbox_iou(a: BBox, b: BBox) -> float:
    """Intersection-over-Union for two bboxes."""
    inter_x1 = max(a.x, b.x)
    inter_y1 = max(a.y, b.y)
    inter_x2 = min(a.x2, b.x2)
    inter_y2 = min(a.y2, b.y2)

    inter_w = max(0, inter_x2 - inter_x1)
    inter_h = max(0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h

    union_area = a.area + b.area - inter_area
    return inter_area / union_area if union_area > 0 else 0.0


def centroid_in_polygon(
    centroid: tuple[int, int],
    polygon_points: list[tuple[float, float]],
) -> bool:
    """
    Zone entry test. Spec §1.4: centroid-in-polygon (not bbox-IoU)
    — avoids false positives on fast pass-through.
    """
    if len(polygon_points) < 3:
        return False
    poly = Polygon(polygon_points)
    if not poly.is_valid:
        poly = poly.buffer(0)  # fix self-intersection
    return poly.contains(Point(*centroid))


def polygon_is_valid_and_closed(points: list[tuple[float, float]]) -> tuple[bool, str]:
    """
    Validate a polygon drawn by the Zone Editor:
      - at least 3 distinct vertices
      - no self-intersection (after .buffer(0) attempt)
      - non-zero area
      - first/last coordinate optional (Shapely auto-closes)

    Returns (ok, reason). Reason is a human-readable error when ok=False.
    """
    if len(points) < 3:
        return False, "polygon_needs_at_least_3_points"

    # Remove consecutive duplicates
    dedup: list[tuple[float, float]] = []
    for p in points:
        if not dedup or dedup[-1] != p:
            dedup.append(p)
    if len(dedup) < 3:
        return False, "polygon_has_too_many_duplicate_points"

    poly = Polygon(dedup)
    if poly.area <= 0:
        return False, "polygon_has_zero_area"

    if not poly.is_valid:
        poly = poly.buffer(0)
        if poly.is_empty or poly.area <= 0:
            return False, "polygon_self_intersects"

    return True, ""


def predicted_bbox_center(
    last: BBox,
    velocity: tuple[float, float],
    dt_seconds: float,
) -> BBox:
    """
    Project a bbox forward by `dt_seconds` given a pixel/sec velocity —
    used by CVI match priority 2 (short-term spatial) to compensate for
    motion between observations.
    """
    dx = int(round(velocity[0] * dt_seconds))
    dy = int(round(velocity[1] * dt_seconds))
    return BBox(x=last.x + dx, y=last.y + dy, w=last.w, h=last.h)


__all__ = [
    "BBox",
    "bbox_iou",
    "centroid_in_polygon",
    "polygon_is_valid_and_closed",
    "predicted_bbox_center",
]
