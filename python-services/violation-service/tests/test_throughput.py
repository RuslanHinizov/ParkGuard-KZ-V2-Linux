"""
python-services/violation-service/tests/test_throughput.py

Adım 20 — Pipeline throughput testi (spec §25).

12 kamera × 2 zone, 30 fps × 60 saniyelik simüle gözlem akışı.
asyncio.gather ile tam paralel. Başarı kriterleri:

  • Toplam 12 ihlal yazılır (her kamera 1 araç, 1 ihlal)
  • Throughput ≥ 3 000 gözlem/s  (async simülasyon, gerçek zamandan hızlı)
  • Simülasyon süresi ≤ 30 s (makine bağımsız eşik)
  • Hiç çökme / beklenmedik istisna yok

Gerçek load test araçları (Locust) için bkz. load-tests/locustfile.py
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from shapely import wkt as shapely_wkt
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.geometry import BBox
from src.active_registry import ActiveViolationRegistry
from src.cvi_manager import CVIManager
from src.models import Violation
from src.observation import Observation
from src.state_machine import StateMachine
from src.violation_writer import ViolationWriter
from src.zone_cache import ZoneEntry

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------
NUM_CAMERAS = 12
ZONES_PER_CAMERA = 2
FPS = 30
TOTAL_SECONDS = 60         # simüle edilen zaman aralığı
THRESHOLD_SECONDS = 20
_POLY_WKT = "POLYGON((0 0,200 0,200 200,0 200,0 0))"
_BASE_TS = datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures — engine + session_factory conftest'ten gelir
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def multi_cam_seeded(session_factory) -> dict[str, list[int]]:  # noqa: ANN001
    """
    12 kamera × 2 zone SQLite'a eklenir.
    {camera_id: [zone_id, zone_id]} sözlüğü döner.
    """
    zone_ids: dict[str, list[int]] = {}
    async with session_factory() as sess:
        for i in range(NUM_CAMERAS):
            cam_id = f"cam_{i:02d}"
            await sess.execute(
                text(
                    "INSERT OR IGNORE INTO cameras (id, name, rtsp_url) "
                    "VALUES (:id, :name, :rtsp)"
                ),
                {"id": cam_id, "name": f"Cam {i:02d}", "rtsp": f"rtsp://host/{i}"},
            )
        await sess.commit()

        for i in range(NUM_CAMERAS):
            cam_id = f"cam_{i:02d}"
            for z in range(ZONES_PER_CAMERA):
                await sess.execute(
                    text(
                        "INSERT INTO zones "
                        "(camera_id, name, zone_type, polygon_wkt, "
                        " threshold_seconds, exit_confirm_seconds, cooldown_seconds, enabled) "
                        "VALUES (:cam, :name, 'no_parking', :poly, :thr, 10, 10, 1)"
                    ),
                    {
                        "cam": cam_id,
                        "name": f"zone_{i}_{z}",
                        "poly": _POLY_WKT,
                        "thr": THRESHOLD_SECONDS,
                    },
                )
            await sess.commit()
            result = await sess.execute(
                text("SELECT id FROM zones WHERE camera_id = :cam"),
                {"cam": cam_id},
            )
            zone_ids[cam_id] = [int(r[0]) for r in result.all()]

    return zone_ids


# ---------------------------------------------------------------------------
# Simülasyon yardımcısı
# ---------------------------------------------------------------------------
def _zone_entries(cam_id: str, zids: list[int]) -> list[ZoneEntry]:
    poly = shapely_wkt.loads(_POLY_WKT)
    return [
        ZoneEntry(
            id=zid,
            camera_id=cam_id,
            name=f"z_{zid}",
            zone_type="no_parking",
            polygon=poly,
            threshold_seconds=THRESHOLD_SECONDS,
            exit_confirm_seconds=10,
            cooldown_seconds=10,
        )
        for zid in zids
    ]


async def _run_camera(
    cam_id: str,
    zones: list[ZoneEntry],
    state: StateMachine,
    redis,  # noqa: ANN001
    plate: str,
) -> None:
    """
    Bir kamera için FPS × TOTAL_SECONDS gözlem üretir.
    Her kameraın kendi CVIManager'ı var (process isolasyonu simülasyonu).
    """
    mgr = CVIManager(redis=redis)
    total_frames = FPS * TOTAL_SECONDS
    for frame in range(total_frames):
        t_sec = frame / FPS
        ts = _BASE_TS + timedelta(seconds=t_sec)
        obs = Observation(
            camera_id=cam_id,
            ds_track_id=1,
            frame_id=frame,
            timestamp=ts,
            bbox=BBox(x=80, y=80, w=40, h=40),
            centroid=(100, 100),  # zone içinde
            class_name="car",
            confidence=0.9,
            embedding=None,
            plate_text=plate,
            plate_conf=0.91,
        )
        cvi, _ = await mgr.resolve(obs)
        await state.evaluate(cvi, obs, zones)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_throughput_12_cameras(
    session_factory, multi_cam_seeded, redis
) -> None:
    """
    12 kamera paralel, her biri 30fps × 60s = 1 800 frame.
    Toplam: 21 600 gözlem → 12 ihlal beklenir.
    """
    writer = ViolationWriter(session_factory)
    state = StateMachine(
        writer=writer,
        registry=ActiveViolationRegistry(redis),
    )

    t0 = time.perf_counter()

    await asyncio.gather(
        *[
            _run_camera(
                cam_id=f"cam_{i:02d}",
                zones=_zone_entries(f"cam_{i:02d}", multi_cam_seeded[f"cam_{i:02d}"]),
                state=state,
                redis=redis,
                plate=f"{i+1:03d}AAA{(i % 20) + 1:02d}",
            )
            for i in range(NUM_CAMERAS)
        ]
    )

    elapsed = time.perf_counter() - t0
    total_frames = NUM_CAMERAS * FPS * TOTAL_SECONDS
    throughput = total_frames / elapsed

    # Toplam ihlal sayısı
    async with session_factory() as sess:
        total_v = int(
            (await sess.execute(select(func.count(Violation.id)))).scalar_one()
        )

    print(
        f"\n"
        f"  Cameras         : {NUM_CAMERAS}\n"
        f"  Zones/cam       : {ZONES_PER_CAMERA}\n"
        f"  Total frames    : {total_frames:,}\n"
        f"  Elapsed         : {elapsed:.2f} s\n"
        f"  Throughput      : {throughput:,.0f} obs/s\n"
        f"  Violations      : {total_v}"
    )

    # -- Doğruluk -------------------------------------------------------
    expected = NUM_CAMERAS * ZONES_PER_CAMERA
    assert total_v == expected, (
        f"Expected {expected} violations, got {total_v}"
    )

    # -- Throughput eşiği -----------------------------------------------
    assert throughput >= 3_000, (
        f"Throughput çok düşük: {throughput:,.0f} gözlem/s (min: 3 000)"
    )

    # -- Süre eşiği -----------------------------------------------------
    assert elapsed <= 30.0, (
        f"Simülasyon çok uzun sürdü: {elapsed:.1f} s (max: 30 s)"
    )
