"""
load-tests/locustfile.py

Spec §25 — ParkGuard KZ event-api yük testi.

Çalıştırma:
    cd load-tests
    pip install locust
    locust -f locustfile.py --host http://localhost:8000 \
           --users 50 --spawn-rate 5 --run-time 3600s \
           --headless --csv results/load_$(date +%Y%m%d_%H%M)

Başarı kriterleri (spec §25):
  • 50 eş zamanlı kullanıcı @ 12 kamera
  • P95 yanıt süresi ≤ 200 ms (REST)
  • Hata oranı < %1
  • WS kopma sayısı < %5 / dakika

Senaryolar (ağırlıklı):
  60% — violations listesi (paginasyonlu, filtreli)
  15% — kamera listesi
  10% — zone listesi (kamera bazlı)
   5% — tek ihlal detayı
   5% — PATCH ihlal durumu (approved)
   5% — WebSocket bağlantısı (10 s canlı tutulur)
"""

from __future__ import annotations

import json
import random
import threading
import time
from typing import Any

import websocket  # pip install websocket-client
from locust import HttpUser, TaskSet, between, constant_pacing, events, task
from locust.exception import StopUser


# ---------------------------------------------------------------------------
# Yapılandırma — gerçek kamera/zone ID'leri ile güncelle
# ---------------------------------------------------------------------------
CAMERA_IDS = [f"cam_{i:02d}" for i in range(1, 13)]  # 12 kamera
OPERATOR_HEADER = {"X-Operator-Name": "loadtest"}


# ---------------------------------------------------------------------------
# Shared state: seed edilen violation ID'leri (PATCH testi için)
# ---------------------------------------------------------------------------
_violation_ids: list[int] = []
_violation_ids_lock = threading.Lock()


def _register_violation_id(vid: int) -> None:
    with _violation_ids_lock:
        if vid not in _violation_ids:
            _violation_ids.append(vid)
            if len(_violation_ids) > 1000:
                _violation_ids.pop(0)  # sonsuz büyümeyi engelle


def _random_violation_id() -> int | None:
    with _violation_ids_lock:
        return random.choice(_violation_ids) if _violation_ids else None


# ---------------------------------------------------------------------------
# Violations endpoint görevleri
# ---------------------------------------------------------------------------
class ViolationTasks(TaskSet):
    """REST violations endpoint görevleri."""

    @task(6)
    def list_violations(self) -> None:
        """GET /api/v1/violations — paginasyonlu liste."""
        camera = random.choice(CAMERA_IDS)
        params: dict[str, Any] = {
            "camera_id": camera,
            "limit": random.choice([20, 50, 100]),
            "offset": random.choice([0, 0, 0, 50, 100]),  # ağırlıklı ilk sayfa
        }
        # Zaman zaman durum filtresi ekle
        if random.random() < 0.4:
            params["status"] = random.choice(["pending", "approved", "disputed"])

        with self.client.get(
            "/api/v1/violations",
            params=params,
            headers=OPERATOR_HEADER,
            catch_response=True,
            name="GET /api/v1/violations",
        ) as resp:
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    items = data.get("items", []) if isinstance(data, dict) else data
                    for item in items[:5]:  # ilk 5 ID'yi kaydet
                        _register_violation_id(item["id"])
                    resp.success()
                except Exception:  # noqa: BLE001
                    resp.failure("JSON parse hatası")
            else:
                resp.failure(f"HTTP {resp.status_code}")

    @task(1)
    def get_single_violation(self) -> None:
        """GET /api/v1/violations/{id} — tekil detay."""
        vid = _random_violation_id()
        if vid is None:
            return  # henüz ID yok
        with self.client.get(
            f"/api/v1/violations/{vid}",
            headers=OPERATOR_HEADER,
            catch_response=True,
            name="GET /api/v1/violations/{id}",
        ) as resp:
            if resp.status_code in (200, 404):
                resp.success()
            else:
                resp.failure(f"HTTP {resp.status_code}")

    @task(1)
    def patch_violation_status(self) -> None:
        """PATCH /api/v1/violations/{id} — durum güncelle."""
        vid = _random_violation_id()
        if vid is None:
            return
        new_status = random.choice(["approved", "disputed"])
        with self.client.patch(
            f"/api/v1/violations/{vid}",
            json={"status": new_status},
            headers=OPERATOR_HEADER,
            catch_response=True,
            name="PATCH /api/v1/violations/{id}",
        ) as resp:
            if resp.status_code in (200, 404, 422):
                resp.success()
            else:
                resp.failure(f"HTTP {resp.status_code}")


# ---------------------------------------------------------------------------
# Camera/Zone endpoint görevleri
# ---------------------------------------------------------------------------
class InfrastructureTasks(TaskSet):
    """Cameras + Zones CRUD."""

    @task(3)
    def list_cameras(self) -> None:
        """GET /api/v1/cameras."""
        with self.client.get(
            "/api/v1/cameras",
            headers=OPERATOR_HEADER,
            catch_response=True,
            name="GET /api/v1/cameras",
        ) as resp:
            resp.success() if resp.status_code == 200 else resp.failure(
                f"HTTP {resp.status_code}"
            )

    @task(2)
    def list_zones_for_camera(self) -> None:
        """GET /api/v1/zones?camera_id=..."""
        camera = random.choice(CAMERA_IDS)
        with self.client.get(
            "/api/v1/zones",
            params={"camera_id": camera},
            headers=OPERATOR_HEADER,
            catch_response=True,
            name="GET /api/v1/zones",
        ) as resp:
            resp.success() if resp.status_code in (200, 404) else resp.failure(
                f"HTTP {resp.status_code}"
            )

    @task(1)
    def healthz(self) -> None:
        """GET /healthz — liveness probe."""
        with self.client.get(
            "/healthz",
            catch_response=True,
            name="GET /healthz",
        ) as resp:
            resp.success() if resp.status_code == 200 else resp.failure(
                f"HTTP {resp.status_code}"
            )


# ---------------------------------------------------------------------------
# WebSocket görevi — ayrı thread'de çalışır
# ---------------------------------------------------------------------------
class WebSocketUser(HttpUser):
    """
    WS /ws/violations bağlantısını 10 s açık tutar,
    gelen event sayısını sayar.
    """

    wait_time = between(15, 30)  # her bağlantı arası bekleme
    weight = 5  # düşük ağırlık — toplam user'ların %5'i

    def on_start(self) -> None:
        self._ws_events = 0

    @task
    def hold_ws_connection(self) -> None:
        ws_url = self.host.replace("http://", "ws://").replace("https://", "wss://")
        ws_url += "/ws/violations"
        start = time.time()
        received = 0
        error_msg: str | None = None

        try:
            ws = websocket.create_connection(ws_url, timeout=5)
            ws.settimeout(0.5)
            deadline = start + 10  # 10 s bağlı kal
            while time.time() < deadline:
                try:
                    _data = ws.recv()
                    received += 1
                except websocket.WebSocketTimeoutException:
                    pass  # timeout normal
            ws.close()
        except Exception as exc:  # noqa: BLE001
            error_msg = str(exc)

        elapsed_ms = (time.time() - start) * 1000
        events.request.fire(
            request_type="WS",
            name="WS /ws/violations (10 s)",
            response_time=elapsed_ms,
            response_length=received,
            exception=Exception(error_msg) if error_msg else None,
            context={},
        )
        self._ws_events += received


# ---------------------------------------------------------------------------
# Ana kullanıcı sınıfı — REST
# ---------------------------------------------------------------------------
class ParkGuardOperatorUser(HttpUser):
    """
    Operatör web paneli davranışı simülasyonu.
    Violations listesi + altyapı endpoint'leri.
    """

    wait_time = constant_pacing(1.0)  # saniyede 1 istek / kullanıcı
    weight = 95

    tasks = {
        ViolationTasks: 7,
        InfrastructureTasks: 3,
    }
