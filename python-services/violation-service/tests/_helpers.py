"""
python-services/violation-service/tests/_helpers.py

Factory helpers for tests. Each function produces the smallest possible
Observation / ZoneEntry that the state machine cares about, so tests
read like "enter, wait, exit" without boilerplate.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import numpy as np
from shapely import wkt

from shared.geometry import BBox
from src.observation import Observation
from src.zone_cache import ZoneEntry


def make_zone(
    zone_id: int,
    camera_id: str = "cam_test",
    *,
    threshold_seconds: int = 20,
    exit_confirm_seconds: int = 10,
    cooldown_seconds: int = 10,
    polygon_wkt: str = "POLYGON((0 0,100 0,100 100,0 100,0 0))",
) -> ZoneEntry:
    return ZoneEntry(
        id=zone_id,
        camera_id=camera_id,
        name=f"zone_{zone_id}",
        zone_type="no_parking",
        polygon=wkt.loads(polygon_wkt),
        threshold_seconds=threshold_seconds,
        exit_confirm_seconds=exit_confirm_seconds,
        cooldown_seconds=cooldown_seconds,
    )


def make_obs(
    ts: datetime,
    *,
    camera_id: str = "cam_test",
    ds_track_id: int = 1,
    frame_id: int = 0,
    centroid: tuple[int, int] = (50, 50),
    plate: str | None = None,
    plate_conf: float | None = None,
    embedding: np.ndarray | None = None,
    class_name: str = "car",
) -> Observation:
    bbox = BBox(x=centroid[0] - 20, y=centroid[1] - 20, w=40, h=40)
    return Observation(
        camera_id=camera_id,
        ds_track_id=ds_track_id,
        frame_id=frame_id,
        timestamp=ts,
        bbox=bbox,
        centroid=centroid,
        class_name=class_name,
        confidence=0.9,
        embedding=embedding,
        plate_text=plate,
        plate_conf=plate_conf,
    )


def stamped(base: datetime, *, seconds: float = 0.0) -> datetime:
    return base + timedelta(seconds=seconds)


def norm_vector(dim: int = 128, *, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def perturb(v: np.ndarray, noise: float = 0.02, *, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noisy = v + noise * rng.standard_normal(v.shape).astype(np.float32)
    return noisy / np.linalg.norm(noisy)


def obs_replace(obs: Observation, **kwargs) -> Observation:
    return replace(obs, **kwargs)


__all__ = [
    "make_obs",
    "make_zone",
    "norm_vector",
    "obs_replace",
    "perturb",
    "stamped",
]
