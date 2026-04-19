"""
python-services/violation-service/tests/test_duplicate_prevention.py

The 8 scenarios from spec §1.2 + §14 step 5 — each one must produce
*exactly one* DB row. If any of these regress the entire idempotency
contract of the service is broken.

Numbering matches the spec headings:
    1. Plate match survives tracker reset            → layer 1 (CVI)
    2. Spatial match inside 3 s survives tracker     → layer 1 (CVI)
    3. Embedding match after plate OCR lost          → layer 1 (CVI)
    4. Stable ds_track_id                            → layer 1 (CVI)
    5. Exit candidate re-entry inside 10 s window    → state machine
    6. Redis active_violation prevents double-fire   → layer 2
    7. DB unique constraint swallows INSERT race     → layer 4
    8. Cycle_id increments only after cooldown       → layer 3 cycle rule
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from shared.schemas import CVIState
from src.active_registry import ActiveViolationRegistry
from src.cvi_manager import CVIManager
from src.models import Violation
from src.state_machine import StateMachine
from src.violation_writer import ViolationInsert, ViolationWriter
from tests._helpers import make_obs, make_zone, norm_vector, perturb, stamped


async def _count_violations(sf) -> int:
    async with sf() as s:
        return int((await s.execute(select(func.count(Violation.id)))).scalar_one())


def _make_system(session_factory, redis=None):
    writer = ViolationWriter(session_factory)
    registry = ActiveViolationRegistry(redis)
    state = StateMachine(writer=writer, registry=registry)
    mgr = CVIManager(redis=redis)
    return mgr, state


async def _run_until_violated(mgr, state, zone, base, *,
                              ds_track_id=1, plate=None, plate_conf=None,
                              embedding=None, seconds=25):
    """
    Fire one observation per second for `seconds`; state machine should
    mature into VIOLATED at t=20 (default threshold).
    """
    for t in range(seconds + 1):
        obs = make_obs(
            stamped(base, seconds=t),
            ds_track_id=ds_track_id,
            plate=plate,
            plate_conf=plate_conf,
            embedding=embedding,
        )
        cvi, _ = await mgr.resolve(obs)
        await state.evaluate(cvi, obs, [zone])
    return cvi


# --------------------------------------------------------------------------- #
# 1. Plate match (tracker reset) — single violation
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_01_plate_match_across_tracker_reset_single_violation(
    session_factory, seeded, redis, fixed_now
) -> None:
    mgr, state = _make_system(session_factory, redis=redis)
    zone = make_zone(seeded["zone_id"])

    # First 25 s drive to VIOLATED with ds_track_id=1, plate 999XYZ02.
    await _run_until_violated(
        mgr, state, zone, fixed_now,
        ds_track_id=1, plate="999XYZ02", plate_conf=0.9, seconds=25,
    )
    count_after_first = await _count_violations(session_factory)
    assert count_after_first == 1

    # Tracker resets → different ds_track_id, same plate. Stays in zone, no new violation.
    for t in range(26, 40):
        obs = make_obs(
            stamped(fixed_now, seconds=t),
            ds_track_id=777,
            plate="999XYZ02",
            plate_conf=0.9,
        )
        cvi, _ = await mgr.resolve(obs)
        await state.evaluate(cvi, obs, [zone])

    assert await _count_violations(session_factory) == 1
    assert mgr.size() == 1  # plate match reattached to existing CVI


# --------------------------------------------------------------------------- #
# 2. Spatial match (tracker reset, no plate) — single violation
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_02_spatial_match_across_tracker_reset_single_violation(
    session_factory, seeded, redis, fixed_now
) -> None:
    mgr, state = _make_system(session_factory, redis=redis)
    zone = make_zone(seeded["zone_id"])
    emb = norm_vector(seed=2)

    # Same ds_track_id until just before the threshold fires.
    for t in range(0, 18):
        obs = make_obs(stamped(fixed_now, seconds=t), ds_track_id=1, embedding=emb)
        cvi, _ = await mgr.resolve(obs)
        await state.evaluate(cvi, obs, [zone])

    # Tracker reassigns at t=18 & t=19, same pixel location, embedding continues.
    for t, tid in ((18, 502), (19, 502), (20, 502)):
        obs = make_obs(stamped(fixed_now, seconds=t), ds_track_id=tid, embedding=emb)
        cvi, match = await mgr.resolve(obs)
        assert match.reason in ("spatial", "embedding")
        await state.evaluate(cvi, obs, [zone])

    assert await _count_violations(session_factory) == 1
    assert mgr.size() == 1


# --------------------------------------------------------------------------- #
# 3. Embedding match after plate OCR lost — single violation
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_03_embedding_match_after_plate_lost_single_violation(
    session_factory, seeded, redis, fixed_now
) -> None:
    mgr, state = _make_system(session_factory, redis=redis)
    zone = make_zone(seeded["zone_id"])
    emb = norm_vector(seed=3)

    # Plate observed early, then lost for the rest of the residence.
    obs1 = make_obs(fixed_now, ds_track_id=1, plate="AAA111", plate_conf=0.9, embedding=emb)
    c1, _ = await mgr.resolve(obs1)
    await state.evaluate(c1, obs1, [zone])
    obs2 = make_obs(stamped(fixed_now, seconds=1), ds_track_id=1,
                    plate="AAA111", plate_conf=0.9, embedding=emb)
    c2, _ = await mgr.resolve(obs2)
    await state.evaluate(c2, obs2, [zone])

    # Frame-drop gap of 20 s. Then tracker reset; plate unreadable, embedding survives.
    later = make_obs(
        stamped(fixed_now, seconds=22),
        ds_track_id=999,
        embedding=perturb(emb, noise=0.02, seed=30),
    )
    c3, match3 = await mgr.resolve(later)
    assert match3.reason == "embedding"
    await state.evaluate(c3, later, [zone])
    assert mgr.size() == 1

    # Stay in zone long enough for threshold to mature.
    for t in range(23, 45):
        obs = make_obs(stamped(fixed_now, seconds=t), ds_track_id=999,
                       embedding=perturb(emb, noise=0.02, seed=t))
        cvi, _ = await mgr.resolve(obs)
        await state.evaluate(cvi, obs, [zone])

    assert await _count_violations(session_factory) == 1


# --------------------------------------------------------------------------- #
# 4. Stable ds_track_id — single violation
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_04_stable_track_id_single_violation(
    session_factory, seeded, redis, fixed_now
) -> None:
    mgr, state = _make_system(session_factory, redis=redis)
    zone = make_zone(seeded["zone_id"])

    cvi = await _run_until_violated(
        mgr, state, zone, fixed_now, ds_track_id=9, seconds=40
    )
    assert await _count_violations(session_factory) == 1
    # The self-loop stays in VIOLATED, identity_key recorded on entry.
    entry = cvi.zone_state(zone.id)
    assert entry.state == CVIState.VIOLATED


# --------------------------------------------------------------------------- #
# 5. Exit-candidate re-entry during 10 s grace — single violation
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_05_exit_candidate_reentry_does_not_double_fire(
    session_factory, seeded, redis, fixed_now
) -> None:
    mgr, state = _make_system(session_factory, redis=redis)
    zone = make_zone(seeded["zone_id"])

    # Drive to VIOLATED at t=20.
    await _run_until_violated(mgr, state, zone, fixed_now,
                              ds_track_id=1, plate="555AAA02", plate_conf=0.9,
                              seconds=22)
    assert await _count_violations(session_factory) == 1

    # Step outside (exit candidate). No observation on t=23..26.
    outside = make_obs(stamped(fixed_now, seconds=23),
                       ds_track_id=1, centroid=(500, 500))
    cvi_out, _ = await mgr.resolve(outside)
    await state.evaluate(cvi_out, outside, [zone])

    # Re-enter at t=27 (inside 10 s grace, but as same CVI via spatial hop).
    reentry = make_obs(stamped(fixed_now, seconds=27),
                       ds_track_id=1, centroid=(50, 50),
                       plate="555AAA02", plate_conf=0.9)
    cvi_back, _ = await mgr.resolve(reentry)
    events = await state.evaluate(cvi_back, reentry, [zone])

    # Entry after EXIT_CANDIDATE must be "re_entry" (no new violation).
    assert any(e.reason == "re_entry" for e in events)
    assert await _count_violations(session_factory) == 1


# --------------------------------------------------------------------------- #
# 6. Redis active_violations prevents double-fire on re-entry to VIOLATED
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_06_redis_active_registry_blocks_duplicate(
    session_factory, seeded, redis, fixed_now
) -> None:
    mgr, state = _make_system(session_factory, redis=redis)
    zone = make_zone(seeded["zone_id"])

    await _run_until_violated(
        mgr, state, zone, fixed_now, ds_track_id=1,
        plate="888BBB02", plate_conf=0.9, seconds=21,
    )
    assert await _count_violations(session_factory) == 1

    # Force the state machine back into INSIDE_ZONE with a stale entered_at
    # that is already past the threshold. This mirrors a race where a flush
    # of the state machine re-fires the same zone (bug scenario).
    cvi = mgr.all_for_camera("cam_test")[0]
    entry = cvi.zone_state(zone.id)
    entry.state = CVIState.INSIDE_ZONE
    entry.entered_at = fixed_now             # threshold long expired

    obs = make_obs(stamped(fixed_now, seconds=30), ds_track_id=1,
                   plate="888BBB02", plate_conf=0.9)
    await mgr.resolve(obs)  # refreshes last_seen
    events = await state.evaluate(cvi, obs, [zone])

    # Redis active_violation layer prevents a second write.
    assert any(e.reason == "threshold_met_but_active" for e in events)
    assert await _count_violations(session_factory) == 1


# --------------------------------------------------------------------------- #
# 7. DB unique constraint swallows a direct INSERT race
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_07_db_unique_constraint_swallows_race(
    session_factory, seeded, fixed_now
) -> None:
    """
    Two callers racing the exact same (camera, zone, identity, cycle) —
    the second INSERT hits the unique index. The writer swallows it and
    returns the existing violation id.
    """
    writer = ViolationWriter(session_factory)
    row = ViolationInsert(
        camera_id=seeded["camera_id"],
        zone_id=seeded["zone_id"],
        cvi_id=None,
        identity_key="plate:123ABC02",
        cycle_id=0,
        plate_text="123ABC02",
        plate_confidence=0.9,
        plate_format="kz_new",
        plate_region_code="02",
        plate_valid_format=True,
        is_diplomatic=False,
        vehicle_class="car",
        first_seen_in_zone=fixed_now,
        violation_time=fixed_now + timedelta(seconds=20),
        bbox={"x": 10, "y": 10, "w": 40, "h": 40},
    )
    r1 = await writer.insert(row)
    r2 = await writer.insert(row)

    assert r1.created is True
    assert r2.created is False
    assert r1.violation_id == r2.violation_id
    assert await _count_violations(session_factory) == 1


# --------------------------------------------------------------------------- #
# 8. cycle_id increments only after cooldown → new violation
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_08_reentry_after_cooldown_writes_new_violation(
    session_factory, seeded, redis, fixed_now
) -> None:
    mgr, state = _make_system(session_factory, redis=redis)
    zone = make_zone(seeded["zone_id"])

    # First violation cycle.
    await _run_until_violated(mgr, state, zone, fixed_now,
                              ds_track_id=1, plate="111ZZZ02", plate_conf=0.9,
                              seconds=21)
    assert await _count_violations(session_factory) == 1

    # Leave the zone. From t=22 through t=35 the vehicle is outside —
    # 10 s exit_confirm + 10 s cooldown all cleanly elapse.
    for t in range(22, 35):
        out = make_obs(stamped(fixed_now, seconds=t), ds_track_id=1,
                       centroid=(500, 500),
                       plate="111ZZZ02", plate_conf=0.9)
        cvi_out, _ = await mgr.resolve(out)
        await state.evaluate(cvi_out, out, [zone])

    # Re-enter at t=36 — fresh cycle. Drive to new violation.
    # Cooldown expires at t=42 (32 + 10 s cooldown); the new cycle's
    # entered_at is then 42, so threshold_met fires at t=62. We run
    # through t=64 inclusive to leave a little slack either side.
    for t in range(36, 65):
        obs = make_obs(stamped(fixed_now, seconds=t), ds_track_id=1,
                       centroid=(50, 50),
                       plate="111ZZZ02", plate_conf=0.9)
        cvi, _ = await mgr.resolve(obs)
        await state.evaluate(cvi, obs, [zone])

    count = await _count_violations(session_factory)
    assert count == 2, f"expected two violations after cooldown, got {count}"

    # Both rows must have the same identity_key but differ in cycle_id.
    async with session_factory() as s:
        rows = (
            await s.execute(
                select(Violation.identity_key, Violation.cycle_id).order_by(
                    Violation.cycle_id
                )
            )
        ).all()
    assert [r.cycle_id for r in rows] == [0, 1]
    assert {r.identity_key for r in rows} == {"plate:111ZZZ02"}
