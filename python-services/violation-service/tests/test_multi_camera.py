"""
python-services/violation-service/tests/test_multi_camera.py

Adım 11 — multi-camera isolation and CVI eviction tests.

Covered:
  - CVIs from different cameras are never matched against each other
    (priorities 2, 3, 4 are all camera-scoped)
  - Plate match (priority 1) does NOT cross camera boundaries
  - Zone cache returns independent per-camera lists
  - `evict_stale` drops only idle CVIs, leaves active ones
  - Multiple simultaneously active cameras produce independent state
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import numpy as np

from src.cvi_manager import CVIManager
from src.cvi import CVIState
from src.zone_cache import ZoneCache, ZoneEntry
from tests._helpers import make_obs, make_zone, norm_vector, perturb, stamped

T0 = datetime(2026, 4, 20, 9, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def mgr() -> CVIManager:
    return CVIManager(redis=None)


# --------------------------------------------------------------------------- #
# Cross-camera isolation
# --------------------------------------------------------------------------- #
class TestCrossCameraIsolation:
    """CVI matching must never cross camera boundaries."""

    @pytest.mark.asyncio
    async def test_track_id_not_shared_across_cameras(self, mgr: CVIManager) -> None:
        """Same DeepStream track ID on two cameras → two distinct CVIs."""
        obs_a = make_obs(T0, camera_id="cam_01", ds_track_id=42)
        obs_b = make_obs(stamped(T0, seconds=0.5), camera_id="cam_02", ds_track_id=42)

        cvi_a, match_a = await mgr.resolve(obs_a)
        cvi_b, match_b = await mgr.resolve(obs_b)

        assert cvi_a.cvi_id != cvi_b.cvi_id
        assert match_a.reason == "new"
        assert match_b.reason == "new"

    @pytest.mark.asyncio
    async def test_spatial_match_not_shared_across_cameras(self, mgr: CVIManager) -> None:
        """
        Same spatial location on two cameras → different CVIs.
        Both observations share the same centroid so IoU would be 1.0 if
        the manager wasn't scoped per camera.
        """
        obs_a = make_obs(T0, camera_id="cam_01", ds_track_id=1, centroid=(50, 50))
        obs_b = make_obs(
            stamped(T0, seconds=0.5),
            camera_id="cam_02",
            ds_track_id=2,
            centroid=(50, 50),
        )

        cvi_a, _ = await mgr.resolve(obs_a)
        cvi_b, match_b = await mgr.resolve(obs_b)

        assert cvi_a.cvi_id != cvi_b.cvi_id
        assert match_b.reason == "new"

    @pytest.mark.asyncio
    async def test_embedding_match_not_shared_across_cameras(
        self, mgr: CVIManager
    ) -> None:
        """
        Same visual embedding on two cameras → different CVIs.
        Cosine similarity would be ~1.0 if not camera-scoped.
        """
        emb = norm_vector(128, seed=7)
        obs_a = make_obs(T0, camera_id="cam_01", ds_track_id=1, embedding=emb)
        obs_b = make_obs(
            stamped(T0, seconds=1.0),
            camera_id="cam_02",
            ds_track_id=2,
            embedding=perturb(emb, noise=0.001, seed=8),
        )

        cvi_a, _ = await mgr.resolve(obs_a)
        cvi_b, match_b = await mgr.resolve(obs_b)

        assert cvi_a.cvi_id != cvi_b.cvi_id
        assert match_b.reason == "new"

    @pytest.mark.asyncio
    async def test_plate_match_not_shared_across_cameras(self, mgr: CVIManager) -> None:
        """
        Same plate text is NOT used to match across cameras.
        Plate priority 1 cross-camera match would be a false merge.
        """
        plate = "001AAA01"
        obs_a = make_obs(T0, camera_id="cam_01", ds_track_id=1,
                         plate=plate, plate_conf=0.95)
        obs_b = make_obs(
            stamped(T0, seconds=1.0),
            camera_id="cam_02",
            ds_track_id=2,
            plate=plate,
            plate_conf=0.95,
        )

        cvi_a, _ = await mgr.resolve(obs_a)
        cvi_b, match_b = await mgr.resolve(obs_b)

        # Plate match is per-camera — cross-camera plate hits produce
        # a new CVI so the same vehicle on two overlapping cameras
        # doesn't merge into one ghost CVI.
        assert cvi_a.cvi_id != cvi_b.cvi_id
        assert match_b.reason == "new"


# --------------------------------------------------------------------------- #
# Within-camera matching still works
# --------------------------------------------------------------------------- #
class TestWithinCameraMatch:
    """N>1 cameras doesn't break intra-camera matching."""

    @pytest.mark.asyncio
    async def test_spatial_match_same_camera(self, mgr: CVIManager) -> None:
        obs1 = make_obs(T0, camera_id="cam_01", ds_track_id=1, centroid=(50, 50))
        obs2 = make_obs(
            stamped(T0, seconds=0.5),
            camera_id="cam_01",
            ds_track_id=1,
            centroid=(55, 55),
        )
        # Also add a cam_02 observation so the index has both cameras.
        obs_other = make_obs(T0, camera_id="cam_02", ds_track_id=99, centroid=(50, 50))

        cvi1, _ = await mgr.resolve(obs1)
        await mgr.resolve(obs_other)
        cvi2, match2 = await mgr.resolve(obs2)

        assert cvi1.cvi_id == cvi2.cvi_id
        assert match2.reason in {"spatial", "track"}

    @pytest.mark.asyncio
    async def test_all_for_camera_returns_only_that_camera(
        self, mgr: CVIManager
    ) -> None:
        """all_for_camera must not bleed across camera boundaries."""
        # Use widely-separated centroids so spatial match doesn't merge them,
        # and spread timestamps so track-ID window also misses.
        for i in range(3):
            await mgr.resolve(
                make_obs(
                    stamped(T0, seconds=i * 10),
                    camera_id="cam_01",
                    ds_track_id=i + 10,
                    centroid=(i * 200, i * 200),  # far apart — no IoU overlap
                )
            )
        for i in range(5):
            await mgr.resolve(
                make_obs(
                    stamped(T0, seconds=i * 10),
                    camera_id="cam_02",
                    ds_track_id=i + 100,
                    centroid=(i * 200, i * 200),
                )
            )

        cam1 = mgr.all_for_camera("cam_01")
        cam2 = mgr.all_for_camera("cam_02")

        assert len(cam1) == 3
        assert len(cam2) == 5
        ids1 = {c.cvi_id for c in cam1}
        ids2 = {c.cvi_id for c in cam2}
        assert ids1.isdisjoint(ids2)


# --------------------------------------------------------------------------- #
# CVI eviction
# --------------------------------------------------------------------------- #
class TestCVIEviction:
    @pytest.mark.asyncio
    async def test_evict_stale_removes_idle_cvis(self, mgr: CVIManager) -> None:
        """CVIs last seen >600 s ago should be evicted."""
        obs = make_obs(T0, camera_id="cam_01", ds_track_id=1)
        await mgr.resolve(obs)
        assert mgr.size() == 1

        # Evict with now = T0 + 601 s  →  should drop the CVI.
        evicted = mgr.evict_stale(stamped(T0, seconds=601))
        assert evicted == 1
        assert mgr.size() == 0

    @pytest.mark.asyncio
    async def test_evict_stale_keeps_recent_cvis(self, mgr: CVIManager) -> None:
        obs = make_obs(T0, camera_id="cam_01", ds_track_id=1)
        await mgr.resolve(obs)

        # Only 60 s have passed — CVI is still fresh.
        evicted = mgr.evict_stale(stamped(T0, seconds=60))
        assert evicted == 0
        assert mgr.size() == 1

    @pytest.mark.asyncio
    async def test_evict_partial_stale_across_cameras(self, mgr: CVIManager) -> None:
        """
        Per-camera CVIs expire independently.

        Strategy: create cam_01 CVI at T0, cam_02 CVI at T0+60 s (a fresh
        observation that produces a new CVI — spatial/track windows have
        expired so it won't merge). Evict at T0+110 s with max_idle=100:
          - cam_01 CVI age = 110 s  → evicted
          - cam_02 CVI age =  50 s  → kept
        """
        max_idle = 100

        obs_a = make_obs(T0, camera_id="cam_01", ds_track_id=1)
        # cam_02 first observation at T0.
        await mgr.resolve(make_obs(T0, camera_id="cam_02", ds_track_id=2))
        await mgr.resolve(obs_a)

        # 60 s later: cam_02 gets a brand-new vehicle (different track_id,
        # different position → no match → new CVI with last_seen = T0+60).
        await mgr.resolve(
            make_obs(
                stamped(T0, seconds=60),
                camera_id="cam_02",
                ds_track_id=99,
                centroid=(800, 800),  # far from previous — no spatial overlap
            )
        )
        # Now we have 3 CVIs:
        #   cam_01: 1 CVI last_seen=T0
        #   cam_02: 2 CVIs — one last_seen=T0, one last_seen=T0+60
        assert mgr.size() == 3

        # Evict at T0+110 s, max_idle=100:
        #   cam_01 CVI (T0):    age 110 s → evicted
        #   cam_02 CVI (T0):    age 110 s → evicted
        #   cam_02 CVI (T0+60): age  50 s → kept
        evicted = mgr.evict_stale(stamped(T0, seconds=110), max_idle_seconds=max_idle)
        assert evicted == 2
        assert mgr.size() == 1
        remaining = mgr.all_for_camera("cam_02")
        assert len(remaining) == 1


# --------------------------------------------------------------------------- #
# ZoneCache multi-camera isolation
# --------------------------------------------------------------------------- #
class TestZoneCacheMultiCamera:
    def _cache(self, zones: list[ZoneEntry]) -> ZoneCache:
        from unittest.mock import MagicMock
        sf = MagicMock()
        cache = ZoneCache(session_factory=sf)
        cache.seed(zones)
        return cache

    @pytest.mark.asyncio
    async def test_zones_partitioned_by_camera(self) -> None:
        zones = [
            make_zone(1, camera_id="cam_01"),
            make_zone(2, camera_id="cam_01"),
            make_zone(3, camera_id="cam_02"),
        ]
        cache = self._cache(zones)

        cam1_zones = await cache.zones_for_camera("cam_01")
        cam2_zones = await cache.zones_for_camera("cam_02")
        cam3_zones = await cache.zones_for_camera("cam_99")

        assert len(cam1_zones) == 2
        assert len(cam2_zones) == 1
        assert len(cam3_zones) == 0

    @pytest.mark.asyncio
    async def test_unknown_camera_returns_empty_list(self) -> None:
        cache = self._cache([make_zone(1, camera_id="cam_01")])
        result = await cache.zones_for_camera("cam_unknown")
        assert result == []
