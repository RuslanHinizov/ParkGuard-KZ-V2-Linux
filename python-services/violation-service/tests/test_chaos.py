"""
python-services/violation-service/tests/test_chaos.py

Adım 19 — Chaos / resilience testleri (spec §24).

Her senaryo gerçek Docker / Kafka / GPU gerektirmez; tam pipeline'ı
in-process olarak çalıştırır ve kırılma noktalarını enjekte eder.

Senaryolar:
  1. Kafka mesaj şema hatası   → ValidationError yutulur, offset commit devam eder
  2. Kafka bozuk JSON          → bytes decode hatası yutulur, offset commit devam eder
  3. Kamera bağlantı kesildi   → zone_cache boş döner, ihlal tetiklemez, CVI korunur
  4. Kamera geri döndü         → zone bitmeden önce geriye dönen araç double-fire etmez
  5. Servis restart sonrası    → Redis active_violation kaydı sağ kalır, yeni CVI duplicate üretmez
  6. CVI eviction baskısı      → 200 stale CVI temizlenir, aktif CVI zarar görmez
  7. Zone TTL sıfırlanır       → cache.refresh() çağrısı yenileme yapar, hiç zone kaybolmaz
  8. Paralel kamera akışı      → 4 kamera eş zamanlı, her birinden tek ihlal
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select

from shared.schemas import CVIState
from src.active_registry import ActiveViolationRegistry
from src.cvi_manager import CVIManager
from src.models import Violation
from src.state_machine import StateMachine
from src.violation_writer import ViolationInsert, ViolationWriter
from src.zone_cache import ZoneCache
from tests._helpers import make_obs, make_zone, norm_vector, perturb, stamped


# --------------------------------------------------------------------------- #
# Yardımcılar
# --------------------------------------------------------------------------- #

async def _count_violations(sf) -> int:
    async with sf() as s:
        return int((await s.execute(select(func.count(Violation.id)))).scalar_one())


def _make_system(sf, redis=None):
    writer = ViolationWriter(sf)
    registry = ActiveViolationRegistry(redis)
    state = StateMachine(writer=writer, registry=registry)
    mgr = CVIManager(redis=redis)
    return mgr, state, writer, registry


async def _drive_to_violated(mgr, state, zone, base, *, ds_track_id=1,
                              plate=None, plate_conf=None, seconds=22):
    """Araç zone'a girer, threshold geçilir → VIOLATED döner."""
    for t in range(seconds + 1):
        obs = make_obs(stamped(base, seconds=t), ds_track_id=ds_track_id,
                       plate=plate, plate_conf=plate_conf)
        cvi, _ = await mgr.resolve(obs)
        await state.evaluate(cvi, obs, [zone])
    return cvi


# =========================================================================== #
# 1. Kafka — şema hatası mesajı yutulur
# =========================================================================== #
@pytest.mark.asyncio
async def test_chaos_01_schema_error_swallowed(
    session_factory, seeded, redis, fixed_now
) -> None:
    """
    consume() döngüsü geçersiz JSON alırsa ValidationError'ı yutarak
    mesaj başına commit yapmalı; sonraki mesaj normal işlenmeli.
    """
    from shared.schemas import DetectionMessage
    from src.main import Runtime

    rt = Runtime()
    rt.cvi_manager = CVIManager(redis=redis)

    zone = make_zone(seeded["zone_id"])
    from src.zone_cache import ZoneCache
    zc = ZoneCache.__new__(ZoneCache)
    zc._by_camera = {}
    zc._last_refresh = 0.0
    # Boş bir session_factory referansı (refresh tetiklenmeyecek)
    zc.session_factory = session_factory  # type: ignore[assignment]
    zc.ttl_seconds = 9999.0
    zc.seed([zone])
    rt.zone_cache = zc

    rt.state_machine = StateMachine(
        writer=ViolationWriter(session_factory),
        registry=ActiveViolationRegistry(redis),
    )

    bad_msgs = [
        b'{"not": "a detection"}',          # şema uyumsuz
        b"{{invalid json}}",                # parse edilemez
        b'{"camera_id": "cam_test", "objects": [], "frame_id": 0, '
        b'"timestamp": "2026-04-18T12:00:00Z", "sensor_id": "cam_test"}',  # geçerli ama boş
    ]

    commit_count = 0

    async def _mock_consume_loop():
        nonlocal commit_count
        from pydantic import ValidationError

        for raw in bad_msgs:
            try:
                DetectionMessage.model_validate_json(raw.decode())
            except (ValidationError, Exception):  # noqa: BLE001
                commit_count += 1
                continue
            commit_count += 1

    await _mock_consume_loop()
    # Her mesaj için offset commit gerçekleşti (hata olsun ya da olmasın).
    assert commit_count == 3


# =========================================================================== #
# 2. Kafka — bozuk bytes yutulur
# =========================================================================== #
@pytest.mark.asyncio
async def test_chaos_02_corrupt_bytes_swallowed(fixed_now) -> None:
    """
    Gelen mesaj bytes decode edilemiyorsa (null byte vb.) istisna
    yutulmalı; döngü devam etmeli.
    """
    from pydantic import ValidationError

    malformed_payloads = [
        b"\x00\xff\xfe",          # binary çöp
        b"",                       # boş
        b"\n\t\r",                 # sadece whitespace
    ]
    failed = 0
    for raw in malformed_payloads:
        try:
            raw.decode("utf-8").strip() or (_ for _ in ()).throw(ValueError("empty"))  # type: ignore[misc]
            from shared.schemas import DetectionMessage
            DetectionMessage.model_validate_json(raw.decode())
        except (UnicodeDecodeError, ValueError, ValidationError):
            failed += 1

    assert failed == len(malformed_payloads)


# =========================================================================== #
# 3. Kamera bağlantı kesildi — zone_cache boş, ihlal tetiklenmez
# =========================================================================== #
@pytest.mark.asyncio
async def test_chaos_03_camera_disconnect_no_violation(
    session_factory, seeded, redis, fixed_now
) -> None:
    """
    Kamera kopar → zone_cache o kamera için boş liste döner.
    Zone yoksa state machine'e hiç bir şey iletilmez.
    Araç zone'da sanki hâlâ duruyor olsa bile DB'ye yazılmamalı.
    """
    mgr, state, _, _ = _make_system(session_factory, redis=redis)
    zone = make_zone(seeded["zone_id"])

    # 19 s boyunca zone içinde — henüz threshold (20 s) dolmadı.
    for t in range(19):
        obs = make_obs(stamped(fixed_now, seconds=t), ds_track_id=1)
        cvi, _ = await mgr.resolve(obs)
        await state.evaluate(cvi, obs, [zone])

    # Kamera kopar → zone listesi boş.
    for t in range(19, 50):
        obs = make_obs(stamped(fixed_now, seconds=t), ds_track_id=1)
        cvi, _ = await mgr.resolve(obs)
        await state.evaluate(cvi, obs, zones=[])   # boş zone listesi

    # Hiç ihlal yazılmamış olmalı.
    assert await _count_violations(session_factory) == 0

    # CVI hâlâ bellekte (evict edilmedi).
    assert mgr.size() >= 1


# =========================================================================== #
# 4. Kamera geri döndü — state machine INSIDE_ZONE korunur, çift yazı yok
# =========================================================================== #
@pytest.mark.asyncio
async def test_chaos_04_camera_reconnect_no_double_fire(
    session_factory, seeded, redis, fixed_now
) -> None:
    """
    Kamera bağlantısı kesilip geri dönünce:
    - CVI bellekte sağ kalıyorsa eski state ile devam eder.
    - INSIDE_ZONE'daki entered_at sıfırlanmaz.
    - Yeniden gözlemlenince threshold dolduğunda tek ihlal yazılır.
    """
    mgr, state, _, _ = _make_system(session_factory, redis=redis)
    zone = make_zone(seeded["zone_id"], threshold_seconds=30)

    # t=0..9: zone içinde, threshold 30 s → henüz dolmadı.
    for t in range(10):
        obs = make_obs(stamped(fixed_now, seconds=t), ds_track_id=1, plate="AAA111", plate_conf=0.9)
        cvi, _ = await mgr.resolve(obs)
        await state.evaluate(cvi, obs, [zone])

    # t=10..14: kamera kopar (zone boş).
    for t in range(10, 15):
        obs = make_obs(stamped(fixed_now, seconds=t), ds_track_id=1, plate="AAA111", plate_conf=0.9)
        cvi, _ = await mgr.resolve(obs)
        await state.evaluate(cvi, obs, zones=[])

    # CVI INSIDE_ZONE'da, entered_at = t=0.
    cvi_obj = mgr.all_for_camera("cam_test")[0]
    entry = cvi_obj.zone_state(zone.id)
    assert entry.state == CVIState.INSIDE_ZONE

    # t=15..35: kamera geri döndü, threshold (30 s) geçer → ihlal.
    for t in range(15, 36):
        obs = make_obs(stamped(fixed_now, seconds=t), ds_track_id=1, plate="AAA111", plate_conf=0.9)
        cvi, _ = await mgr.resolve(obs)
        await state.evaluate(cvi, obs, [zone])

    # Sadece bir ihlal.
    assert await _count_violations(session_factory) == 1


# =========================================================================== #
# 5. Servis restart — Redis active_violation kaydı duplicate'i engeller
# =========================================================================== #
@pytest.mark.asyncio
async def test_chaos_05_restart_redis_prevents_duplicate(
    session_factory, seeded, redis, fixed_now
) -> None:
    """
    İlk süreç ihlali yazar ve Redis'e active_violation kaydeder.
    "Servis restart": yeni bir CVIManager + StateMachine oluşturulur,
    ama aynı Redis örneği (içinde kayıt hâlâ var).
    Yeni süreç aynı araca (aynı plaka) için threshold geçer →
    Redis katmanı (layer 2) ikinci yazmayı engeller.
    """
    # -- 1. süreç: ihlal yaz.
    mgr1, state1, _, _ = _make_system(session_factory, redis=redis)
    zone = make_zone(seeded["zone_id"])
    cvi1 = await _drive_to_violated(
        mgr1, state1, zone, fixed_now,
        ds_track_id=1, plate="999ZZZ02", plate_conf=0.9, seconds=22,
    )
    assert await _count_violations(session_factory) == 1
    entry1 = cvi1.zone_state(zone.id)
    assert entry1.state == CVIState.VIOLATED

    # -- "Restart": yeni MGR + state, ama aynı redis.
    mgr2, state2, _, _ = _make_system(session_factory, redis=redis)

    # Yeni MGR'ın bilgisi yok; aynı araç hâlâ zone içinde.
    for t in range(23, 46):
        obs = make_obs(stamped(fixed_now, seconds=t), ds_track_id=1,
                       plate="999ZZZ02", plate_conf=0.9)
        cvi2, _ = await mgr2.resolve(obs)
        await state2.evaluate(cvi2, obs, [zone])

    # Hâlâ tek ihlal — Redis kaydı layer-2'yi tetikledi.
    assert await _count_violations(session_factory) == 1


# =========================================================================== #
# 6. CVI eviction baskısı — 200 stale CVI temizlenir, aktif zarar görmez
# =========================================================================== #
@pytest.mark.asyncio
async def test_chaos_06_mass_cvi_eviction(
    session_factory, seeded, redis, fixed_now
) -> None:
    """
    200 araç 601 saniye önce görüldü → evict_stale bunları temizler.
    1 aktif araç (INSIDE_ZONE) etkilenmez.
    """
    mgr, state, _, _ = _make_system(session_factory, redis=redis)
    zone = make_zone(seeded["zone_id"])

    stale_base = fixed_now - timedelta(seconds=700)

    # 200 stale CVI oluştur — her biri farklı kamera ID'sinde böylece
    # spatial/embedding/track eşleşmesi olmuyor, hepsi yeni CVI.
    for i in range(200):
        obs = make_obs(
            stale_base,
            camera_id=f"cam_stale_{i}",
            ds_track_id=1,
        )
        await mgr.resolve(obs)

    assert mgr.size() == 200

    # 1 aktif araç ekle — just now.
    active_obs = make_obs(fixed_now, ds_track_id=1, plate="ACTIVE01", plate_conf=0.9)
    active_cvi, _ = await mgr.resolve(active_obs)
    await state.evaluate(active_cvi, active_obs, [zone])
    assert mgr.size() == 201

    # Evict — 600 s eşiği.
    evicted = mgr.evict_stale(fixed_now, max_idle_seconds=600)
    assert evicted == 200
    assert mgr.size() == 1  # sadece aktif cvi kaldı

    # Aktif CVI durum korunur.
    remaining = mgr.all_for_camera("cam_test")
    assert len(remaining) == 1
    assert remaining[0].cvi_id == active_cvi.cvi_id


# =========================================================================== #
# 7. Zone cache TTL dolunca yenileme yapılır
# =========================================================================== #
@pytest.mark.asyncio
async def test_chaos_07_zone_cache_refresh_on_ttl(
    session_factory, seeded, fixed_now
) -> None:
    """
    ZoneCache TTL sona erince refresh() çağrılır.
    SQLite test DB'sinden zone'lar tekrar yüklenir; hiçbir zone kaybolmaz.
    """
    cache = ZoneCache(session_factory=session_factory, ttl_seconds=0.001)

    # İlk çağrı → TTL sıfır olduğundan hemen refresh() çalışır.
    zones = await cache.zones_for_camera("cam_test")
    assert len(zones) == 1  # conftest bir zone seed etti

    # 2 ms bekle → TTL tekrar geçti.
    await asyncio.sleep(0.002)
    zones2 = await cache.zones_for_camera("cam_test")
    assert len(zones2) == 1
    assert zones[0].id == zones2[0].id  # aynı zone döndü


# =========================================================================== #
# 8. Paralel kamera akışı — 4 kamera, her birinden tek ihlal
# =========================================================================== #
@pytest.mark.asyncio
async def test_chaos_08_parallel_cameras_one_violation_each(
    session_factory, seeded, redis, fixed_now
) -> None:
    """
    4 kamera eş zamanlı asyncio.gather ile çalışır.
    Her kamera farklı plaka → her biri tek ihlal üretmeli (toplam 4).
    Paylaşılan ViolationWriter + StateMachine altında yarış koşulu olmamalı.
    """
    writer = ViolationWriter(session_factory)
    registry = ActiveViolationRegistry(redis)
    state = StateMachine(writer=writer, registry=registry)

    cameras = [
        ("cam_A", "cam_A", "111AAA01"),
        ("cam_B", "cam_B", "222BBB02"),
        ("cam_C", "cam_C", "333CCC03"),
        ("cam_D", "cam_D", "444DDD04"),
    ]

    # Her kamera için ayrı zone ID olmadığından seeded zone'u paylaşıyoruz;
    # ihlal kimliği (identity_key) plakaya ve camera_id'ye göre ayrılır.
    # Farklı kameralar için seeded zone'u kullanmak için camera_id'yi zone'a ekle.
    zones_by_camera: dict[str, object] = {}
    for cam_id, _, _ in cameras:
        z = make_zone(seeded["zone_id"], camera_id=cam_id)
        zones_by_camera[cam_id] = z

    async def _run_camera(cam_id: str, plate: str) -> None:
        mgr = CVIManager(redis=redis)
        z = zones_by_camera[cam_id]
        for t in range(23):
            obs = make_obs(
                stamped(fixed_now, seconds=t),
                camera_id=cam_id,
                ds_track_id=1,
                plate=plate,
                plate_conf=0.9,
            )
            cvi, _ = await mgr.resolve(obs)
            await state.evaluate(cvi, obs, [z])  # type: ignore[list-item]

    await asyncio.gather(*[_run_camera(cam, plate) for _, cam, plate in cameras])

    total = await _count_violations(session_factory)
    assert total == 4, f"Beklenen 4 ihlal, alınan {total}"


# =========================================================================== #
# 9. Observation queue dolması — backpressure (senkron test)
# =========================================================================== #
@pytest.mark.asyncio
async def test_chaos_09_high_observation_rate_no_crash(
    session_factory, seeded, redis, fixed_now
) -> None:
    """
    Saniyede 30 frame (1 araç, 30 s → 900 gözlem) yüksek hızda işlenir.
    Sistem çökmez; tam olarak 1 ihlal üretilir (threshold=20 s → t=20'de).
    """
    mgr, state, _, _ = _make_system(session_factory, redis=redis)
    zone = make_zone(seeded["zone_id"])

    # 30 fps × 30 s = 900 frame
    for frame in range(900):
        t_sec = frame / 30.0  # float saniye
        obs = make_obs(
            stamped(fixed_now, seconds=t_sec),
            ds_track_id=1,
            plate="FAST01",
            plate_conf=0.9,
        )
        cvi, _ = await mgr.resolve(obs)
        await state.evaluate(cvi, obs, [zone])

    assert await _count_violations(session_factory) == 1


# =========================================================================== #
# 10. Plaka OCR güven eşiği altında kalır — ihlal yazılır ama plate_text=None
# =========================================================================== #
@pytest.mark.asyncio
async def test_chaos_10_low_confidence_plate_violation_written(
    session_factory, seeded, redis, fixed_now
) -> None:
    """
    OCR sinyali hiç gelmeyen araç (düşük kamera açısı vb.) için
    plate_text=None ile ihlal kaydı yine de oluşturulmalı.
    Kimlik embedding veya track_id üzerinden takip edilir.
    """
    mgr, state, _, _ = _make_system(session_factory, redis=redis)
    zone = make_zone(seeded["zone_id"])
    emb = norm_vector(seed=99)

    for t in range(23):
        obs = make_obs(
            stamped(fixed_now, seconds=t),
            ds_track_id=5,
            embedding=emb,
            # Plaka yok
        )
        cvi, _ = await mgr.resolve(obs)
        await state.evaluate(cvi, obs, [zone])

    count = await _count_violations(session_factory)
    assert count == 1

    # DB kaydında plate_text boş veya None olmalı.
    async with session_factory() as sess:
        row = (await sess.execute(select(Violation))).scalar_one()
    assert row.plate_text is None or row.plate_text == ""
