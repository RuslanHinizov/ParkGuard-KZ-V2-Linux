# PRODUCTION PROMPT — Realtime Zone Violation Detection with ALPR (Kazakistan)
## Multi-Camera Parking Violation System — Kazakhstan Plates, LAN-only Dashboard
## 8-12 cameras | RTX 5070 Ti | Ubuntu 24.04 | No login

> Bu dökümanı Claude Code, Cursor veya başka bir kod agent'ına system prompt olarak ver. Her bölüm ayrı delivery unit'tir. Section 14'teki sırayı izle — her adımda çalışan bir ürün bırak.

---

## 0. ROL, AMAÇ VE KESİN KURALLAR

Sen kıdemli Computer Vision + Backend + Frontend mühendisisin. Görevin: **8-12 Dahua/Hikvision IP kameradan gelen RTSP akışlarında, operatörün çizdiği yasak park bölgelerine (zone) 20 saniyeden uzun süre park eden araçları tespit eden, Kazakistan plakalarını okuyup otomatik ceza kartı üreten production-grade bir sistem** kurmak. Hedef pazar **Kazakistan** — plaka tanıma KZ formatlarına (yeni `123ABC02`, eski `A643KCG`, diplomatik varyantlar) optimize edilmiştir.

**Katı tasarım kuralları:**
- C++ DeepStream: sadece decode + vehicle detection + tracking + snapshot tap (hot path)
- Python: iş mantığı, plaka OCR (Nomeroff-net `kz`/`kz_box`), API, kart üretimi
- Frontend: React + TypeScript + Canvas zone editor + live view
- **Login/Auth YOK** — direkt dashboard açılır (LAN-only deployment, güvenlik ağ seviyesinde)
- Raw frame Python'a **asla** gitmez — sadece event-driven snapshot tap
- Her araç için plaka OCR yapılmaz — sadece zone içine giren araçlar için
- Araç kimliği tek mekanizmaya bağlanmaz — **Composite Vehicle Identity (CVI)** kullanılır
- Violation dedup **çok katmanlı**: CVI + active registry + exit confirmation + cooldown
- State Redis'te persist — pipeline restart'a dayanıklı
- Tüm servisler Docker, NVIDIA Container Toolkit; config-driven, hot-reload

---

## 1. İŞ KURALLARI (ÖNCE BUNU TAM OKU)

### 1.1 Zone ihlal akışı

```
(t=0) Araç zone polygon içine girdi (centroid-in-polygon)
      → state: INSIDE_ZONE
      → kırmızı bbox ile işaretle, "countdown: 20s" göster
      → her 5s'de plaka OCR denenir (track lifetime içinde best plate seçilir)

(t=20s) Araç hâlâ zone içinde VE aynı bölge için bu CVI'nın aktif ihlali YOK
      → state: VIOLATED
      → CEZA YAZILIR (1 defa)
      → kart: vehicle_crop + plate_crop + plate_text + zone_name + camera_name + timestamp
      → active_violations registry'ye eklenir: {cvi_id, camera_id, zone_id, ts}

(t>20s) Araç zone içinde kalmaya devam → İKİNCİ CEZA YAZILMAZ
      → hâlâ aynı active_violation, sadece duration güncellenir

Araç zone'dan çıktı (centroid polygon dışında)
      → state: EXIT_CANDIDATE (timer başlatılır, exit_confirm_timer)
      → EĞER araç 10sn içinde tekrar zone'a girdi/görüldü
          → state: INSIDE_ZONE, TIMER SIFIRLANMAZ (aynı ihlal devam)
      → EĞER 10sn boyunca absence doğrulandı
          → state: EXITED, active_violation kaydı COOLDOWN'a geçer
          → cooldown 10sn boyunca yeni ihlal açılmaz

Cooldown bitti, araç aynı zone'a tekrar girdi
      → state: INSIDE_ZONE, YENİ VIOLATION CYCLE
      → 20sn sonra yeni ceza yazılır
```

### 1.2 "Asla iki ceza yazma" garantisi (CRITICAL)

Aşağıdaki senaryoların HEPSİ tek ceza üretmeli:
- Tracker yeni track_id atadı (occlusion, flicker) → CVI plaka/embedding ile eşleşir
- 5 saniye detection drop → EXIT_CANDIDATE'e geçer ama 10sn dolmadığı için EXITED değil
- Plaka okunamadı → visual embedding + bbox IoU fallback
- Pipeline restart → Redis'ten active_violations geri yüklenir
- Kafka mesaj tekrarı → idempotency key (violation_hash) DB'de unique constraint

### 1.3 "10sn sonra geri girerse yeni ceza" garantisi

Bu koşul SADECE aşağıdakilerin TÜMÜ doğruysa tetiklenir:
- Araç 10sn ardışık absence ile confirmed EXITED oldu
- Cooldown (10sn) bitti
- Aynı CVI tekrar zone'a girdi
- Yeni 20sn timer doldu

### 1.4 Kenar durumlar

- **Zone girişi tanımı**: centroid-in-polygon (bbox IoU değil — ekspres geçişlerde yanlış pozitif üretir)
- **Yavaş ilerleyen araç trafik sıkışıklığı**: zone içinde centroid hareketi > 30px/sn ise timer duraklatılır (opsiyonel)
- **Kamera kesintisi zone'u etkiler mi**: kamera 30sn offline ise aktif ihlaller "suspended", geri geldiğinde CVI match ile devam veya yeni cycle
- **Gece görüşü ve plakasızlık**: plaka okunamazsa ceza yine yazılır, `plate_text=UNKNOWN` olarak, sonradan operatör manuel düzeltebilir

---

## 2. DONANIM VE YAZILIM BASELINE

### Hardware
- GPU: **NVIDIA RTX 5070 Ti** (Blackwell, sm_120, 16GB VRAM)
- CPU: Intel i7 14th gen (hybrid P+E core)
- RAM: 32GB DDR5
- Storage: 1TB+ NVMe
- Network: 1Gbps+

### Software (sabit sürümler — Blackwell uyumlu)
- OS: **Ubuntu 24.04 LTS**
- NVIDIA Driver: **≥ 590.48** (open kernel modules)
- CUDA: **12.8+**
- TensorRT: **10.14.1.48+**
- DeepStream SDK: **9.0** (7.x/8.0 Blackwell'de çalışmaz)
- GStreamer: 1.24
- gcc: 13 (C++20)
- Python: 3.10 (plate-service), 3.12 (diğer servisler)
- Docker: 27+, NVIDIA Container Toolkit
- PostgreSQL: 16 + PostGIS
- Redis: 7.2 (AOF persistent)
- Kafka: 3.7 (veya Redpanda)
- MinIO: latest
- Node: 20+ (frontend)

### Kamera varsayımları
- Protokol: RTSP/TCP
- Sub-stream: H.264, 1280x720 @ 10fps (inference)
- Main-stream: H.264, 1920x1080 @ 25fps (event-driven snapshot)
- Credential'lar: environment variable

---

## 3. KAVRAMSAL MİMARİ

```
┌────────────────────────────────────────────────────────────────────────┐
│                    CAMERAS 8-12x (Dahua/Hikvision)                     │
│   RTSP sub-stream (720p@10fps, continuous)                             │
│   RTSP main-stream (1080p@25fps, on-demand)                            │
└──────────────┬─────────────────────────────────┬───────────────────────┘
               │                                 │
               ▼                                 │
┌──────────────────────────────────────┐         │
│     C++ DEEPSTREAM APPLICATION       │         │
│  uridecodebin(NVDEC) x N             │         │
│    ↓                                 │         │
│  nvstreammux batch=N                 │         │
│    ↓                                 │         │
│  nvinfer PGIE: YOLO11s FP16          │         │
│  (car, bus, truck, motorcycle)       │         │
│    ↓                                 │         │
│  nvinfer SGIE: OSNet veh-reid 128D   │         │
│    ↓                                 │         │
│  nvtracker (NvDCF PerfMode)          │         │
│    ↓                                 │         │
│  nvmsgconv → nvmsgbroker (Kafka)     │         │
│                                      │         │
│  snapshot_tap (ring buffer + nvjpeg) │◄────────┤ snapshot_requests
└──────────────┬───────────────────────┘         │ (Kafka)
               │ detections                      │
               ▼                                 │
┌────────────────────────────────────────────────────────────────────────┐
│                        KAFKA / REDPANDA                                │
│ Topics: detections, violations, snapshot_requests, snapshot_responses, │
│         ocr_requests, ocr_results, penalty_card_requests               │
└───────┬───────────────────┬──────────────┬───────────────┬─────────────┘
        ▼                   ▼              ▼               ▼
┌───────────────┐  ┌──────────────┐ ┌─────────────┐  ┌────────────┐
│ violation-    │  │ plate-       │ │ snapshot-   │  │ penalty-   │
│ service       │  │ service      │ │ consumer    │  │ card-      │
│ (Py 3.12)     │  │ (Py 3.10,    │ │             │  │ service    │
│               │  │  Nomeroff4.0)│ │ uploads     │  │            │
│ - CVI manager │  │              │ │ to MinIO    │  │ PDF/PNG    │
│ - Zone eval   │  │ - plate bbox │ │             │  │ generation │
│ - State       │  │ - plate OCR  │ │             │  │            │
│   machine     │  │ - crop+text  │ │             │  │            │
│ - Active      │  │              │ │             │  │            │
│   violations  │  │              │ │             │  │            │
│ - Exit/cool   │  │              │ │             │  │            │
└───────┬───────┘  └──────┬───────┘ └──────┬──────┘  └──────┬─────┘
        │                 │                │                │
        └─────────────────┴─────┬──────────┴────────────────┘
                                ▼
            ┌───────────────────────────────────┐
            │   POSTGRESQL + POSTGIS + REDIS    │
            │   cameras, zones, vehicles,       │
            │   violations, penalty_cards,      │
            │   cvi_records, plate_history      │
            └───────────────────────────────────┘
                                ▲
                                │
┌───────────────────────────────┴────────────────────────────────────────┐
│                        FASTAPI event-api                               │
│    REST + WebSocket (live detections, live violations)                 │
│    NO AUTH (LAN-only) — X-Operator-Name header for audit only          │
└───────────────────────────────┬────────────────────────────────────────┘
                                ▼
┌────────────────────────────────────────────────────────────────────────┐
│              FRONTEND (React + TypeScript + Vite)                      │
│  Dashboard (no login), Live View, Zone Editor (Fabric.js),             │
│  Violation List, Violation Detail, Penalty Card Preview, Settings      │
│  First-visit: OperatorNameModal → LocalStorage → X-Operator-Name       │
└────────────────────────────────────────────────────────────────────────┘

                                ▲
┌───────────────────────────────┴──────────────────┐
│  Observability: Prometheus + Grafana + Loki +   │
│                 Alertmanager + node-exporter    │
└──────────────────────────────────────────────────┘
```

---

## 4. COMPOSITE VEHICLE IDENTITY (CVI) — SİSTEMİN KALBİ

CVI, tracker'a güvenmeden bir aracı zaman içinde takip eden identity katmanıdır. Her detection bir **observation** üretir; sistem observation'ları CVI'lara match'ler. Bu yapı frame drop, occlusion, tracker reset — hepsine dayanıklıdır.

### 4.1 Observation veri yapısı

```python
@dataclass
class Observation:
    camera_id: str
    ds_track_id: int           # DeepStream tracker ID (fallback)
    frame_id: int
    timestamp: datetime
    bbox: BBox
    centroid: tuple[int, int]
    class_name: str
    confidence: float
    embedding: np.ndarray      # 128-D OSNet feature (SGIE'den)
    plate_text: str | None     # OCR'dan, may be None
    plate_conf: float | None
```

### 4.2 CVI veri yapısı

```python
@dataclass
class CVI:
    cvi_id: str                # UUID
    camera_id: str
    first_seen: datetime
    last_seen: datetime
    
    # Identity evidence (weighted)
    plate_text: str | None           # strongest identity
    plate_votes: dict[str, int]      # {"34ABC123": 7, "34ABC128": 1}
    
    embedding_centroid: np.ndarray   # EMA average
    embedding_samples: int
    
    last_bbox: BBox
    last_centroid: tuple[int, int]
    last_ds_track_id: int
    
    active_zones: set[int]           # şu an hangi zone'larda içeride
    state_per_zone: dict[int, ZoneState]  # her zone için ayrı state machine
```

### 4.3 Match algoritması (4 katmanlı fallback zinciri)

Yeni observation geldiğinde, sırayla denenir:

```
Priority 1 — PLATE MATCH (en güvenilir)
  Eğer obs.plate_text var ve obs.plate_conf > 0.75:
      normalize(plate) ile eşleşen CVI ara, (now - cvi.last_seen) < 300s
      Bulunduysa → match

Priority 2 — SHORT-TERM SPATIAL+TEMPORAL
  candidates = CVI where camera_id eşit
                         and (now - cvi.last_seen) < 3s
                         and bbox_iou(obs.bbox, predicted_bbox) > 0.3
  Tek aday → match
  Çoklu aday → embedding cosine ile ayır

Priority 3 — VISUAL EMBEDDING MATCH
  candidates = CVI where camera_id eşit
                         and (now - cvi.last_seen) < 30s
  En yüksek cosine similarity > 0.82 → match

Priority 4 — DS_TRACK_ID FALLBACK
  Son 2 saniyede aynı ds_track_id → match

Hiçbir match yok → yeni CVI yarat
```

### 4.4 CVI state persistence (Redis)

- Aktif CVI'lar: `cvi:{camera_id}:{cvi_id}` (TTL 10 dk, her observation'da refresh)
- Plate-to-CVI index: `plate:{normalized_plate}` → `{cvi_id}` (set)
- Embedding centroid EMA güncelleme: `new = 0.9 * old + 0.1 * obs`
- Plate_votes dict: majority vote ile en güvenilir plate seçilir
- 10 dk inaktif CVI → cold_storage (DB arşivi), Redis'ten silinir
- **Service restart'ta**: CVI'lar Redis'ten memory'e geri yüklenir

---

## 5. VIOLATION STATE MACHINE (CVI × ZONE)

Her (CVI, zone) çifti için ayrı state machine, Redis-backed:

```
States: OUTSIDE → INSIDE_ZONE → VIOLATED → EXIT_CANDIDATE → COOLDOWN → OUTSIDE

Transitions & guards:

OUTSIDE → INSIDE_ZONE
  trigger: centroid-in-polygon doğru
  effect: inside_timer = start(20s), ocr_attempt_timer = start(5s, repeat)

INSIDE_ZONE → VIOLATED
  trigger: inside_timer expired
          AND no active_violation exists for (camera, zone, identity_key, cycle_id)
  effect: 
    - DB INSERT violation (idempotency key korur)
    - Redis SET active_violation:{camera}:{zone}:{identity} with TTL 1h
    - Send snapshot_request Kafka
    - Send penalty_card_request Kafka

INSIDE_ZONE → EXIT_CANDIDATE
  trigger: single frame observation centroid outside polygon
  effect: exit_confirm_timer = start(10s)
          inside_timer STATE KORUNUR, reset ETMEZ

EXIT_CANDIDATE → INSIDE_ZONE (re-entry during grace)
  trigger: herhangi bir observation centroid inside polygon, timer süresi içinde
  effect: exit_confirm_timer = cancel
          inside_timer resume (reset YOK)

EXIT_CANDIDATE → COOLDOWN
  trigger: exit_confirm_timer expired (10s ardışık absence doğrulandı)
  effect: 
    - active_violation.mark_exited()
    - cooldown_timer = start(10s)
    - inside_timer reset

VIOLATED → EXIT_CANDIDATE (araç çıkmaya başladı)
  trigger: centroid outside polygon
  effect: exit_confirm_timer = start(10s)
          active_violation STILL ACTIVE (ceza duration update)

VIOLATED → VIOLATED (self-loop)
  trigger: her inside observation
  effect: sadece duration_seconds update, YENİ CEZA YOK

COOLDOWN → OUTSIDE
  trigger: cooldown_timer expired
  effect: active_violation tamamen temizlenir, cycle_id += 1

COOLDOWN → INSIDE_ZONE (yeni cycle)
  trigger: cooldown sonrası araç tekrar giriyor
  effect: yeni inside_timer (20s), yeni cycle_id ile
```

### 5.1 Identity resolution (idempotency key)

```python
def get_violation_identity(cvi: CVI) -> str:
    """Violation'ın deterministik idempotency key'i"""
    if cvi.plate_text and cvi.plate_votes.get(cvi.plate_text, 0) >= 3:
        return f"plate:{normalize(cvi.plate_text)}"
    # Plate yok veya güvensiz → embedding hash fallback
    emb_quantized = quantize_embedding(cvi.embedding_centroid, precision=2)
    emb_hash = hashlib.sha256(emb_quantized.tobytes()).hexdigest()[:16]
    return f"emb:{cvi.camera_id}:{emb_hash}"
```

### 5.2 DB unique constraint

```sql
CREATE UNIQUE INDEX uniq_violation_identity_cycle 
ON violations (camera_id, zone_id, identity_key, cycle_id);
```

Bu index **tek savunma hattı** değil, son güvence. Race condition'da ikinci INSERT fail olur, servis silent olarak yutar. Duplicate ceza **MATEMATIKSEL OLARAK** imkansız.

### 5.3 cycle_id mantığı

- Her CVI-zone çifti için cycle_id, Redis'te sayaç
- `cycle:{camera_id}:{zone_id}:{identity_key}` → integer
- Başlangıç: 0
- COOLDOWN → OUTSIDE geçişinde `INCR`
- Böylece:
  - Aynı cycle içinde tracker reset olsa → identity_key aynı, cycle_id aynı → DB unique constraint engeller
  - 10sn cooldown sonrası tekrar ihlal → cycle_id +1 → yeni DB satırı, yeni ceza

---

## 6. DİZİN YAPISI

```
parkviolation/
├── docker-compose.yml
├── docker-compose.prod.yml
├── .env.example
├── README.md
├── Makefile
│
├── cpp-deepstream/
│   ├── Dockerfile
│   ├── CMakeLists.txt
│   ├── src/
│   │   ├── main.cpp
│   │   ├── pipeline_builder.{hpp,cpp}
│   │   ├── camera_manager.{hpp,cpp}
│   │   ├── metadata_exporter.{hpp,cpp}      # Kafka producer
│   │   ├── snapshot_tap.{hpp,cpp}           # on-demand JPEG
│   │   ├── snapshot_consumer.{hpp,cpp}      # Kafka snapshot_requests
│   │   ├── health_server.{hpp,cpp}
│   │   └── config_loader.{hpp,cpp}
│   ├── configs/
│   │   ├── app_config.yml
│   │   ├── cameras.yml
│   │   ├── pgie_yolo11s.txt
│   │   ├── sgie_reid_osnet.txt
│   │   ├── tracker_nvdcf.yml
│   │   ├── msgconv_schema.txt
│   │   └── msgbroker_kafka.txt
│   ├── models/
│   │   ├── yolo11s.onnx
│   │   ├── yolo11s_fp16.engine
│   │   ├── osnet_x0_25_veh.onnx
│   │   ├── osnet_x0_25_veh_fp16.engine
│   │   └── labels.txt
│   └── third_party/cpp-httplib/
│
├── python-services/
│   ├── shared/
│   │   ├── schemas.py
│   │   ├── db.py
│   │   ├── redis_client.py
│   │   ├── kafka_client.py
│   │   ├── geometry.py
│   │   ├── plate_normalize.py
│   │   └── logging.py
│   │
│   ├── violation-service/
│   │   ├── Dockerfile (python:3.12-slim)
│   │   ├── pyproject.toml
│   │   ├── src/
│   │   │   ├── main.py
│   │   │   ├── consumer.py
│   │   │   ├── cvi_manager.py
│   │   │   ├── match_algorithm.py
│   │   │   ├── zone_evaluator.py
│   │   │   ├── state_machine.py
│   │   │   ├── active_violation_registry.py
│   │   │   ├── exit_confirmator.py
│   │   │   ├── cooldown_manager.py
│   │   │   ├── plate_ocr_requester.py
│   │   │   └── penalty_dispatcher.py
│   │   └── tests/
│   │       ├── test_state_machine.py
│   │       ├── test_cvi_matching.py
│   │       ├── test_duplicate_prevention.py   # KRITIK
│   │       └── test_exit_reentry.py
│   │
│   ├── plate-service/
│   │   ├── Dockerfile (python:3.10 + nomeroff-net)
│   │   ├── pyproject.toml
│   │   ├── src/
│   │   │   ├── main.py
│   │   │   ├── nomeroff_wrapper.py
│   │   │   ├── plate_detector.py
│   │   │   ├── plate_ocr.py
│   │   │   ├── tr_postprocess.py
│   │   │   └── model_warmup.py
│   │   └── models/
│   │
│   ├── event-api/
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── src/
│   │       ├── main.py
│   │       ├── middleware.py              # X-Operator-Name capture
│   │       ├── routers/
│   │       │   ├── cameras.py
│   │       │   ├── zones.py
│   │       │   ├── violations.py
│   │       │   ├── penalty_cards.py
│   │       │   ├── live.py
│   │       │   └── stats.py
│   │       └── models.py
│   │
│   ├── snapshot-consumer/
│   │   └── src/main.py
│   │
│   ├── penalty-card-service/
│   │   ├── Dockerfile
│   │   └── src/
│   │       ├── main.py
│   │       ├── card_renderer.py
│   │       ├── templates/card_default.html
│   │       └── qr_generator.py
│   │
│   └── worker-pool/
│       └── src/tasks.py                       # Celery
│
├── frontend/
│   ├── Dockerfile (node:20 + nginx)
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── api/
│       ├── pages/
│       │   ├── Dashboard.tsx              # anasayfa (Login yok)
│       │   ├── CameraLive.tsx
│       │   ├── ZoneEditor.tsx
│       │   ├── ViolationList.tsx
│       │   ├── ViolationDetail.tsx
│       │   └── Settings.tsx
│       ├── components/
│       │   ├── Layout.tsx
│       │   ├── OperatorNameModal.tsx      # İlk ziyarette isim sorar
│       │   ├── CameraGrid.tsx
│       │   ├── PolygonCanvas.tsx
│       │   ├── LiveOverlay.tsx
│       │   └── PenaltyCardPreview.tsx
│       ├── stores/
│       └── hooks/
│
├── migrations/alembic/versions/
│
├── ops/
│   ├── prometheus/prometheus.yml
│   ├── grafana/dashboards/
│   │   ├── pipeline_health.json
│   │   ├── cvi_metrics.json
│   │   ├── violation_funnel.json
│   │   ├── plate_ocr_quality.json
│   │   └── system_resources.json
│   ├── alertmanager/alerts.yml
│   └── loki/loki-config.yml
│
├── scripts/
│   ├── build_tensorrt_engines.sh
│   ├── download_nomeroff_models.sh
│   ├── seed_db.sh
│   ├── backup_db.sh
│   ├── retention_cleanup.sh
│   └── smoke_test.sh
│
└── docs/
    ├── architecture.md
    ├── troubleshooting.md
    ├── runbook.md
    └── api.md
```

---

## 7. C++ DEEPSTREAM KATMANI

### 7.1 Pipeline

```
uridecodebin (N, NVDEC) 
  ↓
nvstreammux (batch=N, 1280x720, live-source=1, batched-push-timeout=40000)
  ↓
nvinfer PGIE: YOLO11s FP16 TensorRT
   classes filter: [2,3,5,7] = car, motorcycle, bus, truck
   min-confidence=0.5, interval=0
  ↓
nvinfer SGIE: OSNet_x0_25 vehicle ReID, 128-D embedding
   secondary-mode=1 (operates on PGIE detections)
   output tensor meta attached per object
  ↓
nvtracker (NvDCF_PerfMode, ll-lib=libnvds_nvmultiobjecttracker.so)
   tracker-width=640, tracker-height=384
  ↓
nvmsgconv (custom schema, embedding base64'e dahil) → nvmsgbroker (Kafka "detections")
  ↓
tee → snapshot_tap (30-frame ring buffer, on-demand nvjpegenc)
```

### 7.2 Detection message schema (Kafka: detections)

```json
{
  "sensor_id": "cam_01",
  "timestamp": "2026-04-17T10:23:45.123Z",
  "frame_id": 847123,
  "objects": [
    {
      "ds_track_id": 12847,
      "class_id": 2,
      "class_name": "car",
      "confidence": 0.87,
      "bbox": {"x": 340, "y": 220, "w": 180, "h": 110},
      "centroid": {"x": 430, "y": 275},
      "embedding_b64": "..."
    }
  ]
}
```

### 7.3 Snapshot tap

- Kafka `snapshot_requests` dinler: `{camera_id, frame_id?, request_id, reason, type=vehicle_crop|full_frame|plate_region, bbox?}`
- Ring buffer'dan en yakın frame alınır (30 frame, ~3sn)
- NVJPEG encode, `/shared/snapshots/{request_id}.jpg` yazılır
- Kafka `snapshot_responses`'a cevap: `{request_id, path, camera_id, timestamp}`

### 7.4 Blackwell özel notlar

- TensorRT engine **host'ta** build et: `trtexec --onnx=yolo11s.onnx --saveEngine=yolo11s_fp16.engine --fp16 --builderOptimizationLevel=5`
- Docker image: `nvcr.io/nvidia/deepstream:9.0-triton-multiarch`
- Env: `NVIDIA_DRIVER_CAPABILITIES=compute,utility,video,graphics`
- Flags: `--gpus all --runtime=nvidia`

### 7.5 Health endpoint

- `:9100/healthz` JSON: pipeline state, per-camera fps, decode_drops, inference_latency_ms, active_tracks
- `:9100/metrics` Prometheus format

### 7.6 Failure handling

- RTSP disconnect → `uridecodebin` drop olursa branch restart, exponential backoff (1,2,4,8,16s, max 16)
- Inference fail 3x/min → full pipeline restart
- OOM → graceful shutdown, systemd restart

---

## 8. VIOLATION-SERVICE (EN KRİTİK PYTHON SERVİSİ)

### 8.1 Ana döngü

```python
async def main_loop():
    async for msg in kafka_consumer("detections"):
        observations = parse_observations(msg)
        for obs in observations:
            cvi = await cvi_manager.match_or_create(obs)
            await cvi_manager.update(cvi, obs)
            
            zones = await zone_cache.get(obs.camera_id)
            for zone in zones:
                inside = geometry.centroid_in_polygon(obs.centroid, zone.polygon)
                state = await state_machine.get(cvi.cvi_id, zone.id)
                transitions = state.process(inside, obs.timestamp)
                
                for transition in transitions:
                    if transition == "VIOLATED":
                        await trigger_violation(cvi, zone, obs)
                    elif transition == "EXITED":
                        await finalize_exit(cvi, zone)
                    elif transition == "ENTERED":
                        await notify_entered(cvi, zone, obs)
            
            if cvi.in_any_zone() and cvi.should_attempt_ocr():
                await request_plate_ocr(cvi, obs)
        
        await consumer.commit()  # manual commit after processing
```

### 8.2 Trigger violation (idempotency CRITICAL)

```python
async def trigger_violation(cvi: CVI, zone: Zone, obs: Observation):
    identity = get_violation_identity(cvi)
    cycle_id = await active_violation_registry.get_cycle(
        obs.camera_id, zone.id, identity
    )
    
    # Layer 1: Redis check
    if await active_violation_registry.has_active(
        obs.camera_id, zone.id, identity, cycle_id
    ):
        logger.debug("violation_suppressed_by_redis", cvi_id=cvi.cvi_id)
        return
    
    # Request snapshot asenkron
    snapshot_id = str(uuid4())
    await kafka.send("snapshot_requests", {
        "camera_id": obs.camera_id,
        "frame_id": obs.frame_id,
        "request_id": snapshot_id,
        "reason": "violation",
        "type": "vehicle_crop",
        "bbox": asdict(obs.bbox),
    })
    
    plate_info = cvi.get_best_plate()
    
    # Layer 2: DB unique constraint — son savunma hattı
    try:
        async with db.transaction():
            violation_id = await db.insert_violation(
                camera_id=obs.camera_id,
                zone_id=zone.id,
                cvi_id=cvi.cvi_id,
                identity_key=identity,
                cycle_id=cycle_id,
                plate_text=plate_info.text if plate_info else "UNKNOWN",
                plate_confidence=plate_info.conf if plate_info else 0.0,
                vehicle_class=cvi.dominant_class,
                first_seen_in_zone=cvi.zone_entry_time[zone.id],
                violation_time=obs.timestamp,
                snapshot_request_id=snapshot_id,
            )
    except UniqueViolationError:
        # Race: başka worker yazdı, sessiz geç
        logger.warning("duplicate_violation_prevented", 
                      camera=obs.camera_id, zone=zone.id, identity=identity)
        return
    
    # Layer 3: Mark Redis
    await active_violation_registry.mark_active(
        obs.camera_id, zone.id, identity, cycle_id, violation_id, ttl=3600
    )
    
    # Dispatch penalty card
    await kafka.send("penalty_card_requests", {
        "violation_id": violation_id,
        "snapshot_request_id": snapshot_id,
    })
    
    logger.info("violation_written", id=violation_id, identity=identity, cycle=cycle_id)
```

### 8.3 Exit confirmator

```python
class ExitConfirmator:
    """10sn ardışık absence olmadan EXITED sayılmaz"""
    
    async def run(self):
        while not shutdown_event.is_set():
            now = datetime.utcnow()
            candidates = await redis.smembers("exit_candidates")
            
            for raw in candidates:
                cvi_id, zone_id = raw.decode().split(":")
                last_seen_key = f"last_seen:{cvi_id}"
                last_seen_raw = await redis.get(last_seen_key)
                if not last_seen_raw:
                    await redis.srem("exit_candidates", raw)
                    continue
                
                last_seen = datetime.fromisoformat(last_seen_raw.decode())
                delta = (now - last_seen).total_seconds()
                
                if delta >= EXIT_CONFIRM_SECONDS:
                    await state_machine.force_transition(cvi_id, int(zone_id), "EXITED")
                    await cooldown_manager.start(cvi_id, int(zone_id))
                    await redis.srem("exit_candidates", raw)
            
            await asyncio.sleep(0.5)
```

### 8.4 Duplicate prevention test suite (ZORUNLU)

Aşağıdaki senaryoların HEPSİ tek ceza üretmeli:

```python
# test_duplicate_prevention.py — pytest + testcontainers (Redis + Postgres)

async def test_tracker_reset_same_cycle():
    """Araç zone'dayken track_id değişir (frame drop) → tek ceza"""
    # Inject: 25sn observation (track_id=100, zone içinde)
    # At t=10s: track_id 100 ölür
    # At t=11s: track_id 200 başlar, aynı bbox/embedding/plate
    # Beklenti: 1 violation kayıt

async def test_occlusion_3s():
    """Araç 3sn kaybolur sonra döner → tek ceza, timer devam"""
    # t=0-10s: inside, track_id=100
    # t=10-13s: no observation (occlusion)
    # t=13-30s: inside, track_id=100 veya 200 (tracker kurtarmış olabilir)
    # Beklenti: t=20s'de 1 violation, 13s'de timer sıfırlanmamalı

async def test_exit_candidate_return_before_10s():
    """7sn dışarı çıkıp dönen araç → tek ceza"""
    # t=0-22s: inside, violation at t=20s
    # t=22-29s: outside (7sn)
    # t=29s+: inside again
    # Beklenti: 1 violation, yeni cycle BAŞLAMASIN

async def test_exit_confirmed_reenter_after_cooldown():
    """15s absence + 10s cooldown sonra dönen araç → 2 ayrı ceza"""
    # t=0-22s: inside (violation at t=20)
    # t=22-37s: outside (15s — exit confirmed at t=32)
    # t=32-42s: cooldown
    # t=42s+: inside again
    # t=42+20=62s: new violation
    # Beklenti: 2 violations (farklı cycle_id)

async def test_plate_ocr_inconsistency():
    """Aynı araç farklı OCR sonuçları → tek CVI, tek ceza"""
    # Aynı araç için OCR sonuçları: "34ABC123" x5, "34ABC128" x1
    # Beklenti: plate_votes majority → "34ABC123", tek CVI, tek violation

async def test_pipeline_restart():
    """Violation yazıldıktan sonra service restart → duplicate yok"""
    # t=0-22s: violation written
    # Kill service
    # Restart service
    # t=22-25s: same observations replay
    # Beklenti: Redis'ten active_violation restore, duplicate yazılmaz

async def test_kafka_message_replay():
    """Aynı observation iki kez → tek violation"""
    # Same observation message, process twice
    # Beklenti: 1 violation (DB unique constraint)

async def test_concurrent_workers_same_vehicle():
    """2 worker aynı aracı işler → tek violation"""
    # Start 2 consumer workers
    # Both receive same detection
    # Beklenti: 1 violation, diğeri UniqueViolationError yakalar
```

Tüm testler CI'da çalışmalı, HEPSİ yeşil olmadan deploy YOK.

---

## 9. PLATE-SERVICE (NOMEROFF-NET 4.0.1)

### 9.1 Kurulum

- Base image: `nvidia/cuda:12.8.0-cudnn-runtime-ubuntu22.04`
- Python 3.10 (3.12'de dependency sorunları)
- `pip install nomeroff-net==4.0.1`
- Modeller build time'da indir, image'a göm:

```dockerfile
RUN mkdir -p /models && \
    python -c "from nomeroff_net import pipeline; \
               p = pipeline('number_plate_detection_and_reading', image_loader='opencv'); \
               print('models cached')"
```

### 9.2 Akış

```
Kafka consume: ocr_requests
  {cvi_id, camera_id, vehicle_crop_b64, request_id, attempt_no}
    ↓
vehicle_crop decode (base64 → numpy)
    ↓
Nomeroff-net: detect plate bbox + crop + OCR
    ↓
TR post-process (regex validation + char correction)
    ↓
Kafka produce: ocr_results
  {cvi_id, request_id, plate_text, plate_conf, plate_crop_b64, valid_format}
```

### 9.3 Kazakistan plaka normalize

Kazakistan plaka formatları:

| Format | Regex | Örnek | Not |
|--------|-------|-------|-----|
| Yeni standart (2012+) | `^\d{3}[A-Z]{3}\d{2}$` | `123ABC02` | 3 hane + 3 harf + 2 haneli bölge |
| Eski A-tipi | `^[A-Z]\d{3}[A-Z]{2,3}$` | `A643KCG` | Tek harf + 3 hane + 2-3 harf |
| Diplomatik (D) | `^D\d{3}[A-Z]{2,3}$` | `D123AB` | Diplomatik misyon |
| Teknik (T) | `^T\d{3}[A-Z]{2,3}$` | `T456CD` | Diplomatik teknik |
| Yabancı (F) | `^F\d{3}[A-Z]{2,3}$` | `F789XY` | Yabancı şahıs |

Bölge kodları (yeni format için, son 2 hane):
```
01 = Astana (eski Nur-Sultan)     02 = Almaty city
03 = Aqmola                        04 = Aqtöbe
05 = Almaty region                 06 = Atyrau
07 = West Kazakhstan (Oral)        08 = Zhambyl (Taraz)
09 = Karagandy                     10 = Kostanay
11 = Qyzylorda                     12 = Mangystau (Aktau)
13 = Türkistan (eski S. Kaz.)      14 = Pavlodar
15 = North Kazakhstan (Petropavl)  16 = Shymkent city
17 = East Kazakhstan               18 = Abai
19 = Jetisu                        20 = Ulytau
```

```python
# plate_normalize.py

KZ_NEW_RE = re.compile(r'^(\d{3})([A-Z]{3})(\d{2})$')
KZ_OLD_RE = re.compile(r'^([A-Z])(\d{3})([A-Z]{2,3})$')
KZ_DIPLO_RE = re.compile(r'^([DTHMF]|HC)(\d{3})([A-Z]{2,3})$')

# Valid Kazakhstan region codes
KZ_VALID_REGIONS = {
    '01','02','03','04','05','06','07','08','09','10',
    '11','12','13','14','15','16','17','18','19','20'
}

# Cyrillic → Latin (kamera OCR bazen Cyrillic döndürür)
CYRILLIC_TO_LATIN = {
    'А':'A','В':'B','Е':'E','К':'K','М':'M','Н':'H','О':'O',
    'Р':'P','С':'C','Т':'T','У':'Y','Х':'X','І':'I',
}

# Common OCR confusions (hem sayı hem harf olabilen konumlara göre düzeltilir)
DIGIT_FIX = {'O':'0','D':'0','Q':'0','I':'1','L':'1','Z':'2','S':'5','B':'8','G':'6'}
LETTER_FIX = {'0':'O','1':'I','2':'Z','5':'S','8':'B','6':'G'}


def normalize_kz_plate(raw: str) -> tuple[str | None, str, bool]:
    """
    Returns: (normalized_plate, format_type, is_valid)
    format_type: 'kz_new', 'kz_old', 'kz_diplo', 'unknown'
    """
    if not raw:
        return None, 'unknown', False
    
    # Cyrillic → Latin
    cleaned = ''.join(CYRILLIC_TO_LATIN.get(c, c) for c in raw.upper())
    # Drop non-alphanumeric
    cleaned = re.sub(r'[^A-Z0-9]', '', cleaned)
    
    if len(cleaned) < 6 or len(cleaned) > 9:
        return cleaned or None, 'unknown', False
    
    # Try new format (most common)
    if len(cleaned) == 8:
        fixed = _fix_by_position_new(cleaned)
        m = KZ_NEW_RE.match(fixed)
        if m and m.group(3) in KZ_VALID_REGIONS:
            return fixed, 'kz_new', True
    
    # Try old A-type
    if 6 <= len(cleaned) <= 7:
        fixed = _fix_by_position_old(cleaned)
        m = KZ_OLD_RE.match(fixed)
        if m:
            return fixed, 'kz_old', True
    
    # Diplomatic variants
    m = KZ_DIPLO_RE.match(cleaned)
    if m:
        return cleaned, 'kz_diplo', True
    
    # Format bilinmiyor ama string var — kaydet, invalid işaretle
    return cleaned, 'unknown', False


def _fix_by_position_new(s: str) -> str:
    """kz_new: DDD LLL DD — positions 0-2 digit, 3-5 letter, 6-7 digit"""
    if len(s) != 8:
        return s
    out = []
    for i, c in enumerate(s):
        if i in (0, 1, 2, 6, 7):  # digit positions
            out.append(DIGIT_FIX.get(c, c))
        else:  # letter positions
            out.append(LETTER_FIX.get(c, c))
    return ''.join(out)


def _fix_by_position_old(s: str) -> str:
    """kz_old: L DDD LL(L) — position 0 letter, 1-3 digit, rest letter"""
    out = []
    for i, c in enumerate(s):
        if i == 0:
            out.append(LETTER_FIX.get(c, c))
        elif 1 <= i <= 3:
            out.append(DIGIT_FIX.get(c, c))
        else:
            out.append(LETTER_FIX.get(c, c))
    return ''.join(out)


def extract_region_code(plate: str, format_type: str) -> str | None:
    """Yeni format plakadan bölge kodunu çıkar — reporting için"""
    if format_type == 'kz_new' and len(plate) == 8:
        return plate[-2:]
    return None
```

### 9.4 Nomeroff-net region filter

Nomeroff-net çoklu ülke tanır (ua, ru, kz, ge, by, eu). Sadece `kz` ve `kz_box` region'larını kabul et; diğerlerini düşük güvenli işaretle:

```python
ACCEPTED_REGIONS = {'kz', 'kz_box'}

def process_nomeroff_result(region_name: str, text: str, confidence: float):
    if region_name not in ACCEPTED_REGIONS:
        # Yabancı plaka veya yanlış sınıflama — düşük güven ver
        confidence *= 0.5
    normalized, fmt, valid = normalize_kz_plate(text)
    return {
        'text': normalized,
        'format_type': fmt,
        'region_code': extract_region_code(normalized, fmt),
        'country_region': region_name,
        'confidence': confidence,
        'valid_format': valid,
    }
```

### 9.5 Model warmup

Servis start'ta 5 sentetik image ile inference çağır — cold start prevention. Warmup sırasında KZ örnek plaka görüntüleri kullan.

### 9.6 Performans

- 10 kamera × ~3 zone-entry/min × 5 OCR attempt/CVI = ~150 OCR/min peak
- Batch 4, RTX 5070 Ti'de ~20-30ms/plate
- Kafka consumer lag < 5 mesaj olmalı

---

## 9A. PENALTY CARD SERVICE (KAZAKİSTAN LOKALİZASYONU)

### 9A.1 Görev

`penalty_card_requests` topic'ten `{violation_id, snapshot_request_id}` alır, DB'den violation + snapshot URL'lerini okur, HTML template'i doldurur, **WeasyPrint** ile PDF'e çevirir, MinIO'ya yükler, violation kaydının `penalty_card_url` alanını günceller.

### 9A.2 Kart numarası üretimi

Format: `KZ-{region_code}-{YYYYMMDD}-{6haneli_sequence}`

Örnek: `KZ-02-20260417-000847` (Almaty city, 17 Nisan 2026, günün 847. kartı)

```python
async def generate_card_number(violation: Violation) -> str:
    region = extract_region_code(violation.plate_text, violation.plate_format) or "XX"
    date_str = violation.violation_time.strftime("%Y%m%d")
    # Redis atomic increment: card_seq:{date} → integer
    seq = await redis.incr(f"card_seq:{date_str}")
    return f"KZ-{region}-{date_str}-{seq:06d}"
```

### 9A.3 HTML template içeriği

Şablon üç dilli (Kazakça / Rusça / İngilizce), tek sayfa A4:

```
ҚАЗАҚСТАН РЕСПУБЛИКАСЫ — ТҰРАҚ ЕРЕЖЕСІН БҰЗУ ХАТТАМАСЫ
РЕСПУБЛИКА КАЗАХСТАН — ПРОТОКОЛ О НАРУШЕНИИ ПАРКОВКИ
REPUBLIC OF KAZAKHSTAN — PARKING VIOLATION PROTOCOL

Card No:              KZ-02-20260417-000847
Date/Time (UTC+5):    17.04.2026 14:23:45
Location:             {camera.name} — {camera.location_description}
Zone:                 {zone.name}
Violation Type:       Yasak bölgeye park / Парковка в запрещённой зоне / No-parking zone
Duration in zone:     {duration} sec (threshold: {zone.threshold_seconds}s)

VEHICLE INFORMATION
  Plate:              {plate_text}   (format: {plate_format})
  Region (code):      {region_name} ({region_code})
  Vehicle class:      {vehicle_class}
  Plate confidence:   {plate_confidence:.0%}

EVIDENCE
  [ Vehicle crop ]    [ Plate crop ]    [ Full scene snapshot ]

QR CODE                Signature / Подпись
{qr linking to          ____________________
 violation detail
 on dashboard}          Operator: {operator_name}
                        Generated: {generated_at}

NOTE: Bu kart otomatik sistem tarafından üretildi. Görüntüler arşivde saklanır.
      Данная карта создана автоматически. Изображения хранятся в архиве.
      Generated automatically. Evidence stored in archive.
```

### 9A.4 Render akışı

```python
# card_renderer.py
from weasyprint import HTML
from jinja2 import Environment, FileSystemLoader

async def render_card(violation_id: int) -> tuple[bytes, bytes]:
    """Returns (pdf_bytes, png_bytes)"""
    v = await db.get_violation_full(violation_id)  # join with camera, zone, snapshots
    
    # Download snapshots from MinIO to temp
    vehicle_img = await minio.get(v.snapshot_vehicle_url)
    plate_img = await minio.get(v.snapshot_plate_url) if v.snapshot_plate_url else None
    scene_img = await minio.get(v.snapshot_scene_url) if v.snapshot_scene_url else None
    
    # Embed as base64 in HTML (no external fetch during render)
    ctx = {
        'card_number': v.penalty_card.card_number,
        'violation': v,
        'camera': v.camera,
        'zone': v.zone,
        'vehicle_img_b64': b64encode(vehicle_img).decode(),
        'plate_img_b64': b64encode(plate_img).decode() if plate_img else None,
        'scene_img_b64': b64encode(scene_img).decode() if scene_img else None,
        'qr_data_url': generate_qr(f"https://dashboard.local/violations/{violation_id}"),
        'region_name': KZ_REGION_NAMES.get(v.region_code, 'Unknown'),
        'generated_at': datetime.now(ZoneInfo('Asia/Almaty')),
        'operator_name': v.reviewed_by or 'System',
    }
    
    env = Environment(loader=FileSystemLoader('templates'))
    template = env.get_template('card_default.html')
    html_str = template.render(**ctx)
    
    pdf_bytes = HTML(string=html_str).write_pdf()
    png_bytes = HTML(string=html_str).write_png(resolution=150)  # preview
    
    return pdf_bytes, png_bytes
```

### 9A.5 Font ve locale

- Kazakça/Rusça karakter için Unicode font: **Noto Sans** (image'a göm)
- Tarih saat: Kazakistan timezone **`Asia/Almaty`** (UTC+5), DST yok
- Para/ceza tutarı **YOK** (bu sadece ihlal protokolüdür, ücret yetkili kurum belirler)
- Yön: LTR (soldan sağa)

### 9A.6 Retention

- PDF ve PNG dosyaları MinIO `penalty-cards/{YYYY}/{MM}/{card_number}.pdf` path'inde
- Retention: 5 yıl (KZ yasal saklama süresi — deployment'ta confirm edilmeli)
- MinIO lifecycle policy: 5 yıl sonra glacier tier veya silme

### 9A.7 KZ bölge kodu → isim map

```python
KZ_REGION_NAMES = {
    '01': 'Astana (Нұр-Сұлтан)',
    '02': 'Almaty qalasy',
    '03': 'Aqmola oblysy',
    '04': 'Aqtöbe oblysy',
    '05': 'Almaty oblysy',
    '06': 'Atyrau oblysy',
    '07': 'Batys Qazaqstan (Oral)',
    '08': 'Zhambyl (Taraz)',
    '09': 'Qaraghandy',
    '10': 'Qostanai',
    '11': 'Qyzylorda',
    '12': 'Mangystau (Aqtau)',
    '13': 'Türkistan',
    '14': 'Pavlodar',
    '15': 'Soltüstik Qazaqstan (Petropavl)',
    '16': 'Shymkent qalasy',
    '17': 'Shyghys Qazaqstan',
    '18': 'Abai oblysy',
    '19': 'Jetisu oblysy',
    '20': 'Ulytau oblysy',
}
```

---

## 10. DATABASE SCHEMA

```sql
CREATE EXTENSION IF NOT EXISTS postgis;

-- CAMERAS
CREATE TABLE cameras (
    id VARCHAR(32) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    rtsp_substream_url TEXT NOT NULL,
    rtsp_mainstream_url TEXT,
    location_description TEXT,
    enabled BOOLEAN DEFAULT TRUE,
    config JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ZONES
CREATE TABLE zones (
    id SERIAL PRIMARY KEY,
    camera_id VARCHAR(32) REFERENCES cameras(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    zone_type VARCHAR(32) NOT NULL DEFAULT 'no_parking',
    polygon GEOMETRY(POLYGON) NOT NULL,
    threshold_seconds INT DEFAULT 20,
    exit_confirm_seconds INT DEFAULT 10,
    cooldown_seconds INT DEFAULT 10,
    color_hex VARCHAR(7) DEFAULT '#FF0000',
    enabled BOOLEAN DEFAULT TRUE,
    created_by VARCHAR(64),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_zones_camera ON zones(camera_id) WHERE enabled = TRUE;

-- CVI RECORDS
CREATE TABLE cvi_records (
    cvi_id UUID PRIMARY KEY,
    camera_id VARCHAR(32) NOT NULL REFERENCES cameras(id),
    first_seen TIMESTAMPTZ NOT NULL,
    last_seen TIMESTAMPTZ NOT NULL,
    plate_text VARCHAR(16),
    plate_confidence FLOAT,
    dominant_class VARCHAR(20),
    observations_count INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_cvi_plate ON cvi_records(plate_text) WHERE plate_text IS NOT NULL;
CREATE INDEX idx_cvi_last_seen ON cvi_records(last_seen DESC);

-- VIOLATIONS (ana tablo)
CREATE TABLE violations (
    id BIGSERIAL PRIMARY KEY,
    camera_id VARCHAR(32) NOT NULL REFERENCES cameras(id),
    zone_id INT NOT NULL REFERENCES zones(id),
    cvi_id UUID REFERENCES cvi_records(cvi_id),
    
    -- IDEMPOTENCY KEY (tek başına en önemli constraint)
    identity_key VARCHAR(128) NOT NULL,
    cycle_id INT NOT NULL DEFAULT 0,
    
    plate_text VARCHAR(16),
    plate_confidence FLOAT,
    plate_format VARCHAR(16),              -- kz_new, kz_old, kz_diplo, unknown
    plate_region_code VARCHAR(4),          -- 01-20 (sadece kz_new için)
    plate_valid_format BOOLEAN DEFAULT FALSE,
    is_diplomatic BOOLEAN DEFAULT FALSE,   -- D/T/HC/M/H/F tipi
    vehicle_class VARCHAR(20),
    
    first_seen_in_zone TIMESTAMPTZ NOT NULL,
    violation_time TIMESTAMPTZ NOT NULL,
    exit_confirmed_time TIMESTAMPTZ,
    duration_seconds INT,
    
    snapshot_vehicle_url TEXT,
    snapshot_plate_url TEXT,
    
    status VARCHAR(20) DEFAULT 'pending',
    reviewed_by VARCHAR(64),
    reviewed_at TIMESTAMPTZ,
    
    penalty_card_url TEXT,
    bbox JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- EN ÖNEMLİ INDEX — duplicate prevention son savunma hattı
CREATE UNIQUE INDEX uniq_violation_identity_cycle 
ON violations (camera_id, zone_id, identity_key, cycle_id);

CREATE INDEX idx_violations_camera_time ON violations(camera_id, created_at DESC);
CREATE INDEX idx_violations_plate ON violations(plate_text) WHERE plate_text IS NOT NULL;
CREATE INDEX idx_violations_status ON violations(status);
CREATE INDEX idx_violations_region ON violations(plate_region_code) WHERE plate_region_code IS NOT NULL;

-- PENALTY CARDS
CREATE TABLE penalty_cards (
    id BIGSERIAL PRIMARY KEY,
    violation_id BIGINT UNIQUE REFERENCES violations(id) ON DELETE CASCADE,
    card_number VARCHAR(32) UNIQUE NOT NULL,
    pdf_url TEXT,
    png_url TEXT,
    generated_at TIMESTAMPTZ DEFAULT NOW()
);

-- PLATE HISTORY
CREATE TABLE plate_history (
    id BIGSERIAL PRIMARY KEY,
    plate_text VARCHAR(16) NOT NULL,
    first_seen TIMESTAMPTZ NOT NULL,
    last_seen TIMESTAMPTZ NOT NULL,
    total_observations INT DEFAULT 0,
    total_violations INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX uniq_plate ON plate_history(plate_text);

-- AUDIT LOG (kimlik doğrulama yok, sadece operator ismi izlenir)
CREATE TABLE audit_log (
    id BIGSERIAL PRIMARY KEY,
    operator_name VARCHAR(64),           -- X-Operator-Name header'ından
    action VARCHAR(64) NOT NULL,
    target_type VARCHAR(32),
    target_id VARCHAR(64),
    payload JSONB,
    ip_address INET,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_audit_operator ON audit_log(operator_name, created_at DESC);

-- Monthly partition violations (pg_partman önerilir)
```

---

## 11. REST API ENDPOINTS

> **NOT**: Sistem yerel ağda çalışan kapalı bir deployment için tasarlandı. **Login/JWT YOKTUR** — dashboard direkt açılır. Güvenlik **ağ seviyesinde** sağlanır:
> - Nginx IP whitelist (sadece ofis/operatör IP blokları erişebilir)
> - Firewall (iptables/ufw) — 80/443 sadece LAN
> - Public internet'e açılırsa **nginx basic auth** minimum şarttır
> - VPN kullanılıyorsa VPN ağı üzerinden erişim
>
> Audit için her mutation'a `X-Operator-Name` HTTP header eklenir (frontend cookie'den otomatik ekler). Bu kimlik doğrulama **değildir**, sadece log/izleme içindir.

```
CAMERAS
GET    /api/v1/cameras
POST   /api/v1/cameras
GET    /api/v1/cameras/{id}
PUT    /api/v1/cameras/{id}
DELETE /api/v1/cameras/{id}
POST   /api/v1/cameras/{id}/reload
GET    /api/v1/cameras/{id}/snapshot
GET    /api/v1/cameras/{id}/mjpeg

ZONES
GET    /api/v1/cameras/{id}/zones
POST   /api/v1/cameras/{id}/zones
GET    /api/v1/zones/{id}
PUT    /api/v1/zones/{id}
DELETE /api/v1/zones/{id}

VIOLATIONS
GET    /api/v1/violations?camera_id=&zone_id=&plate=&status=&from=&to=&page=
GET    /api/v1/violations/{id}
PATCH  /api/v1/violations/{id}
GET    /api/v1/violations/{id}/penalty-card    → PDF
POST   /api/v1/violations/{id}/dispute

PLATE HISTORY
GET    /api/v1/plates/{plate_text}/history
GET    /api/v1/plates/search?q=                # prefix search

REALTIME
WS     /api/v1/ws/violations
WS     /api/v1/ws/cameras/{id}/live

STATS
GET    /api/v1/stats/daily-violations
GET    /api/v1/stats/zone-heatmap
GET    /api/v1/stats/plate-top-offenders
GET    /api/v1/stats/region-breakdown          # KZ bölge kodlarına göre dağılım

OPS
GET    /healthz
GET    /readyz
GET    /metrics
```

Rate limit (slowapi): 500 req/min/IP (yerel ağ için gevşek). CORS: frontend origin.

---

## 12. FRONTEND (REACT + TYPESCRIPT + VITE)

### 12.1 Stack
- Vite + React 18 + TypeScript + TailwindCSS
- State: Zustand + TanStack Query
- Router: TanStack Router
- Canvas: Fabric.js (polygon editor)
- Charts: Recharts
- WS: native + auto-reconnect
- Video: MJPEG `<img src>`, ileride HLS/WebRTC

### 12.2 Sayfalar

> **Auth YOK** — uygulama açıldığında direkt Dashboard'a yönlendirir. İlk ziyarette küçük bir modal operatörden ismini ister (örn. "Askar"), bu isim LocalStorage'a yazılır ve her API request'inde `X-Operator-Name` header'ı olarak gönderilir. Bu sadece audit log içindir; kimlik doğrulama değildir.

**Dashboard** (anasayfa — ilk açılış)
- Top bar: active cameras, today's violations, pending review count
- 4x grid mini live preview
- Son 10 ihlal tablosu (thumbnail + plate + zone + time)
- Saatlik violation chart

**Camera Live**
- Full MJPEG view
- Overlay: zone polygons (edit-disabled) + live bboxes (WS'ten)
- Her INSIDE_ZONE araç üstünde countdown "18s"
- Kırmızı bbox: INSIDE_ZONE veya VIOLATED

**Zone Editor** (en önemli UI sayfası)
- Sol: kamera listesi
- Orta: Kamera snapshot üstünde Fabric.js canvas
  - "Add Polygon" button → click-to-add points, double-click to close
  - Drag existing points to edit
  - Delete polygon
- Sağ: Polygon properties (name, threshold_seconds, color, zone_type)
- "Save" → PUT /api/v1/zones
- "Test Mode": snapshot üzerinde simulated detection, polygon hit test gösterimi

**Violation List**
- Filtre: kamera, zone, plaka text search, date range, status
- Liste: thumbnail (vehicle) + plate + zone + time + status badge
- Tıkla → detail

**Violation Detail**
- Büyük vehicle crop + plate crop
- Tüm metadata (timestamps, duration, confidence)
- Penalty card preview + download PDF
- Approve / Dismiss / Dispute buttons (operator)
- Audit trail

**Settings**
- Camera CRUD
- Operator ismi değiştirme (cookie refresh)
- System health widget
- Default threshold override (global 20s/10s/10s)
- Retention ayarları (snapshot kaç gün saklansın)

---

## 13. DOCKER COMPOSE + DEPLOYMENT

### 13.1 Servisler

```
infra: postgres, redis, kafka, zookeeper, minio
core (GPU): deepstream-app, plate-service
business: violation-service (x2), snapshot-consumer, penalty-card-service
api: event-api (x2), worker-celery
edge: nginx, frontend
observability: prometheus, grafana, loki, alertmanager, node-exporter
```

### 13.2 CPU affinity (i7 14th)

```yaml
deepstream-app:   cpuset: "0-11"     # P-cores
plate-service:    cpuset: "0-11"     # GPU-bound, low CPU
violation-service:cpuset: "12-19"    # E-core group 1
event-api:        cpuset: "12-19"
worker-celery:    cpuset: "20-27"    # E-core group 2
```

### 13.3 GPU memory budget (16GB)

```
DeepStream: YOLO11s + OSNet + decode buffers  ~5.0 GB
Plate service (Nomeroff-net)                   ~2.0 GB
CUDA contexts + overhead                       ~2.0 GB
Reserved free                                  ~7.0 GB
```

### 13.4 Restart policies

- infra: `unless-stopped`
- deepstream-app: `on-failure:5`, health check, 30s start_period
- business: `always`
- edge: `always`

### 13.5 Logging

Tüm container'lar `driver: json-file, max-size: 100m, max-file: 10`. Loki Promtail ile toplar.

---

## 14. DELIVERY ORDER (AGENT İŞ SIRASI)

Her adım end-to-end çalışsın; smoke test geçmeden bir sonrakine geçme:

1. **Infra setup** — docker-compose: postgres+redis+kafka+minio, alembic skeleton
2. **Camera CRUD (auth'suz)** — event-api skeleton, X-Operator-Name middleware, cameras endpoints
3. **C++ DeepStream MVP** — 1 kamera, YOLO11s + NvDCF + Kafka output (ReID + snapshot yok)
4. **Violation-service skeleton** — Kafka consumer, console log, zone_evaluator stub
5. **Zones CRUD + polygon validation** — API + DB + curl tests
6. **CVI manager (plate-less)** — match_algorithm priorities 2+4 (spatial + ds_track_id)
7. **Zone state machine + 20s timer** — active_violation_registry Redis-backed
   **TEST SUITE: duplicate_prevention tests 1, 3, 6**
8. **C++ SGIE ReID embedding** — OSNet engine build + meta export
9. **CVI match priority 3 (embedding)** — cosine similarity
10. **Plate-service (Kazakistan)** — Nomeroff-net `kz`+`kz_box` region filter, KZ normalize, OCR request/response Kafka
11. **CVI match priority 1 (plate) + plate_votes**
12. **Snapshot tap C++** — ring buffer, on-demand JPEG
13. **Snapshot-consumer + MinIO** — Kafka response → object storage
14. **Penalty-card-service** — HTML template (KZ lokalizasyonu, bölge kodu, tarih formatı) → PDF
15. **Frontend skeleton (Login YOK)** — direkt Dashboard, OperatorNameModal ilk ziyarette
16. **Frontend camera live (MJPEG)**
17. **Frontend zone editor (Fabric.js)**
18. **Frontend violation list + detail + PDF download + KZ region stats**
19. **Multi-camera scale test** — 4 → 8 → 12 kamera
20. **Observability** — Prometheus + Grafana dashboards
21. **Alertmanager rules + Slack/email**
22. **FULL duplicate prevention test suite (section 8.4 hepsi)**
23. **KZ plaka doğruluk testi** — gerçek kamera datasıyla 100+ plaka manual ground truth, accuracy ölçümü
24. **Chaos tests** — kafka kill, camera disconnect, service restart, GPU OOM
25. **Load test** — 1 saat 12 kamera, violation accuracy >98%
26. **Docs** — runbook, troubleshooting, deployment, KZ plaka format dokümanı

---

## 15. OLASI HATALAR VE ÇÖZÜMLERİ (TROUBLESHOOTING)

### 15.1 Duplicate ceza (EN KRİTİK HATA SINIFI)

**Belirti**: Aynı araç aynı cycle'da 2 ceza yedi.

| Kök Neden | Tespit | Çözüm |
|-----------|--------|-------|
| DB unique constraint eksik | `\d violations`'ta index yok | Migration ile ekle, mevcut duplicate'leri temizle |
| identity_key deterministik değil | aynı CVI için farklı key | plate_votes threshold 3+, embedding quantize round precision 2 |
| cycle_id yanlış artıyor | aynı session'da +1 oluyor | cycle_id SADECE COOLDOWN→OUTSIDE'da artsın |
| Redis TTL çok kısa | active_violation expire | TTL ≥1h + boot'ta DB'den restore |
| Kafka mesaj tekrarı | consumer offset hatası | `enable_auto_commit=False`, manual commit after processing |
| 2 worker race condition | concurrent insert | DB unique constraint zaten savunur; opsiyonel Redlock |
| State restart'ta kayboldu | service restart = empty state | Redis AOF enable, CVI restore on boot |
| plate OCR tekil attempt başarısız | bir attempt sonra "bu araç yok" sanar | plate_votes ile multi-attempt, threshold 3+ |

### 15.2 Eksik ceza (false negative)

| Kök Neden | Çözüm |
|-----------|-------|
| Detection drop (FPS düştü) | per-camera FPS alert <5 |
| Tracker kaybı, CVI match başarısız | embedding threshold 0.82→0.78, spatial window 3s→5s |
| Zone polygon yanlış | ZoneEditor'da "Test Mode" ile doğrula |
| centroid_in_polygon yanlış | shapely kullan, manual ray casting yazma |
| inside_timer drift | monotonic clock, NTP sync |
| Kafka consumer lag | partition artır, consumer scale-out |

### 15.3 Yanlış plaka (Kazakistan özel)

| Kök Neden | Çözüm |
|-----------|-------|
| Nomeroff-net `ru` veya `ua` döndürüyor | region filter ACCEPTED_REGIONS = {'kz','kz_box'}, diğerleri *0.5 confidence |
| Cyrillic karakter (А/А, В/B, С/C) | CYRILLIC_TO_LATIN map ile normalize |
| İki satırlı plaka (kz_box) okunamadı | `number_plate_detection_and_reading` pipeline iki satırı destekler, kısa pipeline kullanma |
| Bölge kodu yanlış (son 2 hane) | KZ_VALID_REGIONS seti (01-20), hatalı ise invalid işaretle |
| Düşük confidence OCR | plate_votes 3+ gözlem, <3 ise UNKNOWN kaydet |
| Bulanık frame | main-stream 1080p snapshot request for retry |
| Kötü açı | track lifetime'da 5 attempt, best conf tut |
| Eski A-tipi plakalar tanınmıyor | KZ_OLD_RE regex ile ayrı handle, format_type='kz_old' işaretle |
| Diplomatik plaka (D/T/HC/M/H/F) | KZ_DIPLO_RE ile kabul et, `is_diplomatic=True` metadata ekle (ceza yazılmayabilir — iş kuralı) |
| O/0, I/1, B/8, 6/G karışması | position-aware fix (_fix_by_position_new/old) |

### 15.4 Pipeline çökmeleri

| Kök Neden | Tespit | Çözüm |
|-----------|--------|-------|
| GPU OOM | `nvidia-smi` + OOM log | Batch ↓, SGIE interval=2, sub-stream fps ↓ |
| RTSP timeout | reconnect log | `tcp-timeout=5000000`, exponential backoff |
| nvstreammux stall | per-cam fps=0 | `batched-push-timeout` 40→100ms |
| CUDA context leak | 24h sonra yavaşlama | systemd timer ile graceful restart daily |
| Nomeroff cold start | ilk req timeout | startup warmup (5 synthetic images) |

### 15.5 Frontend sorunları

| Kök Neden | Çözüm |
|-----------|-------|
| WS disconnect | reconnection expo backoff 1-30s |
| MJPEG gecikmeli | nginx `proxy_buffering off`, `X-Accel-Buffering: no` |
| Zone save fail | polygon closed değil → validation, ilk-son point equal |
| Canvas coord mismatch | image-space save, pixel-space render, zoom-aware |
| X-Operator-Name header eksik | axios interceptor her request'te LocalStorage'dan çek, yoksa modal göster |
| OperatorNameModal LocalStorage kaybı | yeniden isim sor, sessiz geç (kritik değil) |
| CORS hatası (direkt dashboard) | nginx'de `Access-Control-Allow-Origin: http://dashboard.local` |

### 15.6 Blackwell (sm_120) özel

| Kök Neden | Çözüm |
|-----------|-------|
| PyTorch "no kernel for sm_120" | `pip install torch --index-url https://download.pytorch.org/whl/cu128` |
| TensorRT engine compile fail | TensorRT 10.14+ zorunlu, eski engine dosyalarını sil |
| DeepStream 7/8 nvstreammux error | DeepStream 9.0 upgrade zorunlu |
| NVDEC decode limit | `keylase/nvidia-patch` ile unlock |
| Driver mismatch | ≥590.48 open kernel module |

### 15.7 Database sorunları

| Kök Neden | Çözüm |
|-----------|-------|
| violations tablosu şişti | Monthly partition (pg_partman), 6 ay retention |
| GEOMETRY query yavaş | GIST index on polygon |
| Unique constraint retry | tenacity ile max 3 retry |
| Migration conflict | feature branch merge'te manual review |

### 15.8 Kafka sorunları

| Kök Neden | Çözüm |
|-----------|-------|
| Consumer lag artış | partition count ≥6, consumer scale-out |
| Message >1MB | embedding ayrı topic, crop MinIO URL reference |
| Out-of-order | partition key = camera_id (same cam same partition) |
| Rebalance kayıp | manual commit after processing |

### 15.9 Kenar senaryolar

- **Çoklu zone overlap**: bir araç 2 zone'da aynı anda → her zone için bağımsız state machine, her ikisi için ayrı ceza (doğru davranış)
- **Kamera lens/açı değişti**: zone polygon'ları artık yanlış → manuel re-draw. İleride: scene change detection uyarısı
- **Gece/karanlık**: IR kamera shift → model genel training data ile çalışsa bile düşük acc. Çözüm: gece için ayrı fine-tuned model, saat bazlı model switch
- **Yağmur/kar**: false positive artışı → confidence threshold dinamik (weather API ile)

### 15.10 Log aggregation

- Structured JSON log: timestamp, level, service, trace_id, cvi_id, camera_id, zone_id
- Loki + Promtail → Grafana explore
- Trace ID propagation: Kafka headers, Python contextvar, FastAPI middleware

### 15.11 Hata mesaj formatı (tüm servislerde)

```python
logger.error(
    "violation_write_failed",
    exc_info=True,
    camera_id=camera_id,
    zone_id=zone_id,
    identity_key=identity,
    error_code="DB_WRITE_FAILURE",
)
```

Hiçbir yerde `print()` yok. Hiçbir yerde çıplak `except:` yok. Specific exception type yakala.

---

## 16. PERFORMANS HEDEFLERİ (SLA)

| Metrik | Hedef |
|--------|-------|
| Per-camera FPS | ≥ 9/10 input |
| End-to-end violation latency (enter → DB) | < 21s (20s timer + 1s) |
| Plate OCR latency | < 500ms |
| Penalty card generation | < 2s |
| Violation accuracy (ground-truth manuel) | ≥ 98% |
| False positive rate | < 2% |
| **Duplicate violation rate** | **0% (DB unique constraint garantisi)** |
| System availability | 99.5% monthly |
| GPU util | 30-60% headroom |
| Kafka consumer lag peak | < 100 |

---

## 17. SECURITY CHECKLIST

Bu deployment **yerel ağa kapalı** (LAN only) bir operatör panelidir. Auth yoktur. Güvenlik katmanlı olarak dışarıdan sağlanır:

**Ağ katmanı (zorunlu)**
- UFW/iptables: sadece LAN subnet'ten 80/443 erişimi (`ufw allow from 192.168.0.0/16 to any port 443`)
- Public internet'ten erişim **yasak** — eğer gerekirse mutlaka VPN arkasına al
- Eğer mecburen public: Nginx `auth_basic` + TLS + IP whitelist üçlüsü olmadan deploy etme

**Uygulama katmanı**
- Kamera credential'ları docker secret veya `.env` (git'e commit yasak)
- PostgreSQL sadece `parkviolation_net` internal docker network'te, `ports:` mapping yok
- MinIO presigned URL TTL 15 dk
- Nginx TLS (internal CA veya Let's Encrypt DNS challenge ile internal domain)
- API rate limit (slowapi): 500 req/min/IP (LAN gevşek)
- CORS: sadece frontend origin (`http://dashboard.local` gibi)

**Veri ve izleme**
- Plaka metni kişisel veri — MinIO bucket encryption enabled, DB column-level encryption opsiyonel
- Audit log: her mutation'da operator_name + IP kayıt
- Retention: snapshot 90 gün, violation 2 yıl, audit 5 yıl (KZ regulasyonuna göre ayarla)

**Kod seviyesinde**
- Input validation: Pydantic (her endpoint)
- SQL injection: sadece SQLAlchemy ORM, raw SQL yasak
- XSS: React auto-escape, `dangerouslySetInnerHTML` yasak
- CSRF: önemsiz (LAN only + no session auth), ama API mutation'larda origin check

**Fiziksel**
- Server kilitli rack'ta
- USB port disable (BIOS)
- Full-disk encryption (LUKS)

> **Eğer sistem zamanla public/internet-facing olursa** JWT auth mutlaka eklenmelidir. Prompt'taki mimariye auth katmanı eklemek sonradan basit; ama asıl kural: bu deployment LAN only kaldığı sürece login olmayacak.

---

## 18. ÇIKTI KURALLARI (AGENT İÇİN)

Her adımda:
1. Değiştirilen/yeni dosyaların **tam** içeriği
2. Build ve run komutları
3. Smoke test senaryosu + beklenen çıktı
4. Bir sonraki adıma geçmeden kendini test et

Kod kalitesi:
- Python: type hint, async/await, pydantic, structlog, pytest coverage ≥80%
- C++: const-correct, RAII, smart pointers, /W4
- TypeScript: strict mode, no `any`, zod runtime validation
- Her dosya üstünde docstring/comment
- TODO/FIXME yok
- Magic number yok, hepsi config
- Error handling her external call'da
- Graceful shutdown her servis

**ŞİMDİ SECTION 14'TEKİ DELIVERY ORDER'A GÖRE 1. ADIMDAN BAŞLA.**

Her adımın sonunda sıradaki adıma geçmeden önce aşağıdaki maddeleri user'a rapor et:
- Ne yapıldı
- Hangi dosyalar oluştu/değişti
- Nasıl test edildi
- Smoke test çıktısı
- Bir sonraki adım
