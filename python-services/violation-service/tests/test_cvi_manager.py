"""
python-services/violation-service/tests/test_cvi_manager.py

Unit-tests for the 4-tier CVI match algorithm (spec §4.3). The fake-
redis fixture lets us assert plate→cvi indexing side-effects.
"""

from __future__ import annotations

import pytest

from src.cvi_manager import CVIManager
from tests._helpers import make_obs, norm_vector, perturb, stamped


@pytest.mark.asyncio
async def test_first_observation_creates_cvi(fixed_now) -> None:
    mgr = CVIManager()
    cvi, match = await mgr.resolve(make_obs(fixed_now))
    assert match.reason == "new"
    assert mgr.size() == 1
    assert cvi.camera_id == "cam_test"


@pytest.mark.asyncio
async def test_priority1_plate_match(fixed_now) -> None:
    """Same plate, new ds_track_id → match by PLATE (priority 1)."""
    mgr = CVIManager()
    first = make_obs(fixed_now, ds_track_id=1, plate="123ABC02", plate_conf=0.9)
    # Drive plate votes high enough for plate_text to stick on the CVI.
    for i in range(4):
        await mgr.resolve(make_obs(stamped(fixed_now, seconds=i), ds_track_id=1,
                                   plate="123ABC02", plate_conf=0.9))
    assert mgr.size() == 1

    later = make_obs(
        stamped(fixed_now, seconds=60),
        ds_track_id=999,                 # tracker reset
        plate="123ABC02",
        plate_conf=0.9,
        centroid=(400, 400),             # spatial match can't possibly apply
    )
    cvi, match = await mgr.resolve(later)
    assert match.reason == "plate"
    assert match.priority == 1
    assert mgr.size() == 1
    _ = first  # retain for readability


@pytest.mark.asyncio
async def test_priority2_spatial_match(fixed_now) -> None:
    """No plate; <3s gap; IoU > 0.3 → SPATIAL (priority 2)."""
    mgr = CVIManager()
    await mgr.resolve(make_obs(fixed_now, ds_track_id=1, centroid=(50, 50)))
    later = make_obs(
        stamped(fixed_now, seconds=0.5),
        ds_track_id=42,                  # tracker reassigned within 3s
        centroid=(52, 50),
    )
    _, match = await mgr.resolve(later)
    assert match.priority == 2
    assert mgr.size() == 1


@pytest.mark.asyncio
async def test_priority3_embedding_match(fixed_now) -> None:
    """No plate, big spatial gap, similar embedding → EMBEDDING (priority 3)."""
    mgr = CVIManager()
    base_emb = norm_vector(seed=7)
    await mgr.resolve(
        make_obs(
            fixed_now, ds_track_id=1, centroid=(10, 10), embedding=base_emb
        )
    )
    later = make_obs(
        stamped(fixed_now, seconds=20),
        ds_track_id=99,
        centroid=(400, 400),             # fails spatial
        embedding=perturb(base_emb, noise=0.02, seed=11),
    )
    _, match = await mgr.resolve(later)
    assert match.priority == 3
    assert mgr.size() == 1


@pytest.mark.asyncio
async def test_priority4_track_id_match(fixed_now) -> None:
    """Same ds_track_id within 2s, no other signal → TRACK (priority 4)."""
    mgr = CVIManager()
    await mgr.resolve(
        make_obs(fixed_now, ds_track_id=7, centroid=(10, 10))
    )
    later = make_obs(
        stamped(fixed_now, seconds=1.5),
        ds_track_id=7,
        centroid=(400, 400),             # fails spatial (IoU 0)
    )
    _, match = await mgr.resolve(later)
    assert match.priority == 4
    assert mgr.size() == 1


@pytest.mark.asyncio
async def test_no_match_creates_new(fixed_now) -> None:
    """Different ds_track_id, >3s later, no embedding → new CVI."""
    mgr = CVIManager()
    await mgr.resolve(make_obs(fixed_now, ds_track_id=1, centroid=(10, 10)))
    later = make_obs(
        stamped(fixed_now, seconds=60),
        ds_track_id=2,
        centroid=(400, 400),
    )
    _, match = await mgr.resolve(later)
    assert match.reason == "new"
    assert mgr.size() == 2


@pytest.mark.asyncio
async def test_plate_indexed_in_redis(fixed_now, redis) -> None:
    """When redis is wired, CVI plate goes into the plate:* set (spec §4.4)."""
    mgr = CVIManager(redis=redis)
    for i in range(3):
        await mgr.resolve(
            make_obs(
                stamped(fixed_now, seconds=i),
                plate="999XYZ02",
                plate_conf=0.9,
            )
        )
    members = await redis.smembers(b"plate:999XYZ02")
    assert len(members) == 1
