# ParkGuard KZ V2

> **Система видеоаналитики для выявления нарушений парковки — Казахстан**  
> **Görüntü analitiği tabanlı yasak park ihlali tespit sistemi — Казахстан**

---

## Оглавление / İçindekiler

- [Описание системы / Sistem Açıklaması](#описание-системы--sistem-açıklaması)
- [Архитектура / Mimari](#архитектура--mimari)
- [Компоненты / Bileşenler](#компоненты--bileşenler)
- [Требования к оборудованию / Donanım Gereksinimleri](#требования-к-оборудованию--donanım-gereksinimleri)
- [Требования к программному обеспечению / Yazılım Gereksinimleri](#требования-к-программному-обеспечению--yazılım-gereksinimleri)
- [Структура проекта / Proje Yapısı](#структура-проекта--proje-yapısı)
- [Полная установка / Tam Kurulum](#полная-установка--tam-kurulum)
- [Запуск системы / Sistemi Başlatma](#запуск-sistemы--sistemi-başlatma)
- [Первоначальная настройка / İlk Yapılandırma](#первоначальная-настройка--ilk-yapılandırma)
- [Проверка работоспособности / Sistem Doğrulama](#проверка-работоспособности--sistem-doğrulama)
- [Форматы номерных знаков КЗ / KZ Plaka Formatları](#форматы-номерных-знаков-кз--kz-plaka-formatları)
- [Мониторинг / İzleme](#мониторинг--izleme)
- [Тестирование / Test](#тестирование--test)
- [Устранение неполадок / Sorun Giderme](#устранение-неполадок--sorun-giderme)

---

## Описание системы / Sistem Açıklaması

### RU — Русский

ParkGuard KZ V2 — production-ready система для обнаружения нарушений правил парковки в реальном времени с использованием 8–12 IP-камер (RTSP). Система:

- Получает видеопоток от камер Dahua/Hikvision через RTSP
- Обнаруживает автомобили (YOLO11s) и отслеживает их (NvDCF tracker) через NVIDIA DeepStream 9.0
- Определяет нарушителей: автомобиль, простоявший в запрещённой зоне более 20 секунд
- Распознаёт казахстанские номерные знаки через Nomeroff-net 4.0.1 (форматы: `123ABC02`, `A643BCG`, дипломатические)
- Создаёт PDF-штрафные карточки с фото, номером, зоной, временем — на KZ/RU/EN
- Обеспечивает **гарантию "ровно один штраф" на инцидент** через многоуровневую дедупликацию (CVI + Redis + DB unique constraint)
- Показывает операторский веб-дашборд: живое видео, редактор зон, список нарушений, статистика по регионам КЗ
- Полностью работает в локальной сети (LAN-only), без авторизации

### TR — Türkçe

ParkGuard KZ V2, 8–12 IP kameradan (RTSP) gerçek zamanlı yasak park ihlali tespit eden production-grade bir sistemdir. Sistem:

- Dahua/Hikvision kameralardan RTSP üzerinden video alır
- NVIDIA DeepStream 9.0 ile araçları tespit eder (YOLO11s) ve takip eder (NvDCF)
- 20 saniyeden uzun yasak bölgede duran araçları ihlalci olarak işaretler
- Nomeroff-net 4.0.1 ile Kazakistan plakalarını tanır (`123ABC02`, `A643BCG`, diplomatik)
- Fotoğraf, plaka, bölge, saat bilgilerini içeren PDF ceza kartı üretir (KZ/RU/EN)
- Çok katmanlı tekilleştirme (CVI + Redis + DB unique constraint) ile **"her ihlale tam 1 ceza"** garantisi
- Operatör web paneli: canlı görüntü, zone editörü, ihlal listesi, KZ bölge istatistikleri
- Tamamen yerel ağda (LAN-only) çalışır, giriş sayfası yoktur

---

## Архитектура / Mimari

```
+------------------------------------------------------------------+
|  8-12 IP Cameras (Dahua/Hikvision) -- RTSP                       |
+----------------------------+-------------------------------------+
                             | RTSP
                             v
+------------------------------------------------------------------+
|  cpp-deepstream  (C++ / DeepStream 9.0 / NVIDIA RTX 5070 Ti)    |
|  - YOLO11s vehicle detection (TensorRT fp16)                    |
|  - NvDCF multi-object tracker                                   |
|  - OSNet_x0_25 ReID visual embedding (SGIE)                     |
|  - 30-frame snapshot ring buffer                                |
|  -> Kafka topic: detections, snapshots                          |
+-------+------------------------------------------+--------------+
        | Kafka                                    | Kafka
        v                                          v
+-------------------+                  +------------------------------+
| violation-service |                  | plate-service                |
| (Python 3.12)     |<-----------------| (Python 3.10 + Nomeroff-net) |
| - CVI Manager     |  ocr_results     | - KZ normalize               |
| - State Machine   |                  | - Region filter (kz, kz_box) |
| - Redis registry  |                  +------------------------------+
| - ViolationWriter |-----> PostgreSQL/PostGIS
| - Zone Cache      |<----- Redis
+-------------------+
        | Kafka (violation events)
        v
+------------------------------------------------------------------+
|  event-api  (Python 3.12 / FastAPI)                             |
|  - REST: /api/v1/cameras, /zones, /violations                   |
|  - WebSocket: /ws/violations  (real-time push)                  |
|  - MJPEG proxy: /api/v1/cameras/{id}/stream                     |
|  - PDF: /api/v1/violations/{id}/penalty-card                    |
|  - Prometheus /metrics                                          |
+---------------------------+--------------------------------------+
                            | HTTP / WS
                            v
+------------------------------------------------------------------+
|  Frontend  (React + TypeScript + Vite)                          |
|  - CamerasPage   -- live MJPEG grid (12 cameras)                |
|  - ZonesPage     -- SVG polygon zone editor (no external deps)  |
|  - ViolationsPage -- filters, detail panel, PDF, KZ region stats|
|  - WebSocket live updates, 5s auto-reconnect                    |
+------------------------------------------------------------------+

Infrastructure:
  PostgreSQL 16 + PostGIS  |  Redis 7  |  Kafka + Zookeeper
  MinIO (snapshots + PDF)  |  Prometheus + Grafana + Loki + Alertmanager
```

---

## Компоненты / Bileşenler

| Компонент / Bileşen | Путь / Yol | Язык / Dil | Порт / Port |
|---------------------|-----------|-----------|------------|
| event-api | `python-services/event-api/` | Python 3.12 | 8000 |
| violation-service | `python-services/violation-service/` | Python 3.12 | 9200 |
| plate-service | `python-services/plate-service/` | Python 3.10 | 9300 |
| shared (utils) | `python-services/shared/` | Python 3.12 | — |
| cpp-deepstream | `cpp-deepstream/` | C++17 | — |
| frontend | `frontend/` | TypeScript/React | 5173 (dev) |
| PostgreSQL + PostGIS | docker-compose | — | 5432 |
| Redis | docker-compose | — | 6379 |
| Kafka | docker-compose | — | 9092 |
| MinIO | docker-compose | — | 9000 |
| Prometheus | `ops/prometheus/` | — | 9090 |
| Grafana | `ops/grafana/` | — | 3000 |
| Loki | `ops/loki/` | — | 3100 |
| Alertmanager | `ops/alertmanager/` | — | 9093 |

---

## Требования к оборудованию / Donanım Gereksinimleri

| Компонент / Bileşen | Минимум / Minimum | Рекомендуется / Önerilen |
|---------------------|-------------------|--------------------------|
| GPU | NVIDIA RTX 4000+ (sm_89+) | **RTX 5070 Ti** (sm_120, 16 GB VRAM) |
| CPU | Intel i7 8th gen | Intel i7 14th gen |
| RAM | 16 GB DDR4 | **32 GB DDR5** |
| SSD | 256 GB NVMe | **1 TB NVMe** |
| Network | 100 Mbps | **1 Gbps** |
| OS | Ubuntu 22.04 LTS | **Ubuntu 24.04 LTS** |

---

## Требования к программному обеспечению / Yazılım Gereksinimleri

| Программа / Yazılım | Версия / Sürüm | Zorunlu |
|--------------------|---------------|---------|
| Ubuntu | 24.04 LTS | EVET |
| NVIDIA Driver | >= 590.48 (open kernel modules) | EVET |
| CUDA Toolkit | 12.8+ | EVET |
| TensorRT | 10.14.1.48+ | EVET |
| DeepStream SDK | **9.0** (7.x/8.x Blackwell ile çalışmaz!) | EVET |
| Docker Engine | >= 27.x | EVET |
| Docker Compose Plugin | >= 2.x | EVET |
| NVIDIA Container Toolkit | latest | EVET |
| Python 3.12 | event-api, violation-service için | EVET |
| Python 3.10 | plate-service (Nomeroff uyumluluğu) | EVET |
| Node.js | 20 LTS | EVET (frontend build) |
| Git | >= 2.40 | EVET |

---

## Структура проекта / Proje Yapısı

```
ParkGuard-KZ-V2-Linux/
|-- cpp-deepstream/                  # C++ DeepStream 9.0 pipeline
|   |-- src/
|   |   |-- main.cpp                 # GStreamer pipeline, multi-camera
|   |   |-- kafka_sink.cpp           # Kafka output (detections topic)
|   |   `-- snapshot_tap.cpp         # 30-frame ring buffer + JPEG
|   |-- config/
|   |   |-- deepstream_app.txt       # DeepStream app config
|   |   |-- yolo11s.txt              # YOLO11s detector config
|   |   |-- nvdcf_tracker.yml        # NvDCF tracker parameters
|   |   `-- sgie_osnet.txt           # OSNet ReID embedding config
|   `-- CMakeLists.txt
|
|-- python-services/
|   |-- shared/                      # Ortak kutuphane
|   |   |-- config.py                # Pydantic Settings (env vars)
|   |   |-- db.py                    # SQLAlchemy async engine
|   |   |-- schemas.py               # Shared Pydantic models
|   |   |-- plate_normalize.py       # KZ normalize (Cyrillic->Latin, position fix)
|   |   `-- geometry.py              # BBox, IoU helpers
|   |
|   |-- event-api/                   # REST API + WebSocket
|   |   |-- src/
|   |   |   |-- main.py              # FastAPI app, lifespan, Prometheus
|   |   |   |-- routers/
|   |   |   |   |-- cameras.py       # CRUD + MJPEG proxy
|   |   |   |   |-- zones.py         # CRUD + polygon validation
|   |   |   |   `-- violations.py    # List/patch + PDF + WebSocket
|   |   |   |-- models/              # SQLAlchemy ORM models
|   |   |   `-- services/
|   |   |       |-- ws_manager.py    # WebSocket fanout manager
|   |   |       `-- audit.py         # Operator audit log
|   |   |-- migrations/              # Alembic migrations
|   |   |-- tests/                   # 29 pytest tests
|   |   `-- pyproject.toml
|   |
|   |-- violation-service/           # Core detection pipeline
|   |   |-- src/
|   |   |   |-- main.py              # Kafka consume loop, Runtime class
|   |   |   |-- cvi_manager.py       # 4-tier CVI identity matching
|   |   |   |-- state_machine.py     # OUTSIDE->INSIDE_ZONE->VIOLATED->COOLDOWN
|   |   |   |-- active_registry.py   # Redis active_violation guard
|   |   |   |-- violation_writer.py  # DB write + unique constraint
|   |   |   |-- zone_cache.py        # 30s TTL zone cache (Shapely)
|   |   |   `-- observation.py       # DetectionMessage -> Observation
|   |   |-- tests/                   # 53 pytest tests
|   |   `-- pyproject.toml
|   |
|   `-- plate-service/               # OCR service
|       |-- src/
|       |   |-- main.py              # Kafka snapshot consumer
|       |   |-- nomeroff_wrapper.py  # Nomeroff-net 4.0.1 adapter
|       |   |-- kz_postprocess.py    # KZ region filter, accepted regions
|       |   `-- processor.py         # Pipeline orchestrator
|       |-- tests/                   # 484 pytest tests
|       `-- pyproject.toml
|
|-- frontend/                        # React + TypeScript dashboard
|   |-- src/
|   |   |-- pages/
|   |   |   |-- CamerasPage.tsx      # MJPEG live grid (12 cameras)
|   |   |   |-- ZonesPage.tsx        # SVG polygon editor
|   |   |   `-- ViolationsPage.tsx   # Violation list + WebSocket
|   |   `-- api/client.ts            # API client + TypeScript types
|   |-- package.json
|   `-- vite.config.ts
|
|-- ops/
|   |-- prometheus/
|   |   |-- prometheus.yml           # Scrape config (4 jobs)
|   |   `-- rules/parkguard.yml      # 10 alert rules
|   |-- grafana/
|   |   |-- dashboards/parkguard.json
|   |   `-- provisioning/
|   |-- loki/loki.yml                # Loki 3.x, TSDB schema v13
|   `-- alertmanager/alertmanager.yml
|
|-- migrations/                      # Root Alembic (PostGIS extension)
|-- load-tests/locustfile.py         # Locust HTTP load test (50 users)
|-- docs/
|   |-- runbook.md                   # Incident response guide
|   |-- deployment.md                # Production checklist
|   |-- troubleshooting.md           # 12 failure scenarios
|   `-- kz_plate_formats.md          # KZ plate OCR reference
|-- scripts/
|   |-- seed_db.sh                   # Initial camera/zone seed
|   |-- build_tensorrt_engines.sh    # TRT engine build (run once!)
|   |-- download_nomeroff_models.sh  # Download OCR models
|   |-- smoke_test.sh                # System smoke test
|   |-- backup_db.sh                 # Database backup
|   `-- retention_cleanup.sh         # Delete old snapshots/logs
|-- docker-compose.yml               # Full development/staging stack
|-- docker-compose.prod.yml          # Production overrides
`-- .env.example                     # Environment variable template
```

---

## Полная установка / Tam Kurulum

> **ВАЖНО / ONEMLI:** Tum komutlar Ubuntu 24.04 LTS + NVIDIA GPU'lu makinede root veya sudo yetkisiyle calistirilir.
> Kurulum sirasiyla adim adim yapilmalidir. Hicbir adim atlanmamalidir.

---

### Adim 0 / Шаг 0 — Sistem Guncellemesi / Обновление системы

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y \
    build-essential cmake pkg-config git curl wget unzip \
    python3.12 python3.12-venv python3.12-dev \
    python3.10 python3.10-venv python3.10-dev \
    nodejs npm \
    libglib2.0-dev libgstreamer1.0-dev \
    libgstreamer-plugins-base1.0-dev \
    libssl-dev libffi-dev ca-certificates gnupg
```

---

### Adim 1 / Шаг 1 — NVIDIA Surucusu / NVIDIA Driver

```bash
# Blackwell (RTX 5070 Ti) icin 590+ gereklidir
# Для Blackwell (RTX 5070 Ti) требуется версия 590+

sudo ubuntu-drivers autoinstall
# VEYA manuel / ИЛИ вручную:
# sudo apt install -y nvidia-driver-590-open

sudo reboot

# Dogrula / Проверка (reboot sonrasi)
nvidia-smi
# Cikti ornegi / Пример вывода:
# Driver Version: 590.xx | CUDA Version: 12.8
```

---

### Adim 2 / Шаг 2 — CUDA 12.8 Toolkit

```bash
# CUDA keyring ekle / Добавить CUDA keyring
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt update
sudo apt install -y cuda-toolkit-12-8

# PATH ayarla / Настроить PATH
echo 'export PATH=/usr/local/cuda-12.8/bin:$PATH' >> ~/.bashrc
echo 'export LD_LIBRARY_PATH=/usr/local/cuda-12.8/lib64:$LD_LIBRARY_PATH' >> ~/.bashrc
source ~/.bashrc

# Dogrula / Проверка
nvcc --version
# nvcc: NVIDIA (R) Cuda compiler driver ... release 12.8
```

---

### Adim 3 / Шаг 3 — TensorRT 10.14

```bash
# TensorRT'yi NVIDIA Developer Portal'dan indir:
# https://developer.nvidia.com/tensorrt-download
# -> TensorRT 10.14.1.48 for Linux x86_64 CUDA 12.8
# -> TensorRT-10.14.1.48.Linux.x86_64-gnu.cuda-12.8.tar.gz dosyasini indir

tar xzf TensorRT-10.14.1.48.Linux.x86_64-gnu.cuda-12.8.tar.gz
cd TensorRT-10.14.1.48

# Kutuphaneleri kopyala / Копировать библиотеки
sudo cp lib/*.so* /usr/local/lib/
sudo cp include/*.h /usr/local/include/
sudo ldconfig
cd ..

# Python TensorRT paketini kur / Установить Python-пакет TensorRT
pip3 install TensorRT-10.14.1.48/python/tensorrt-10.14.1.48-cp312-none-linux_x86_64.whl

# Dogrula / Проверка
python3.12 -c "import tensorrt; print(tensorrt.__version__)"
# 10.14.1.48
```

---

### Adim 4 / Шаг 4 — Docker Engine

```bash
# Docker kur / Установить Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker

# Docker Compose plugin
sudo apt install -y docker-compose-plugin

# Dogrula / Проверка
docker --version        # Docker version 27.x.x
docker compose version  # Docker Compose version v2.x.x
```

---

### Adim 5 / Шаг 5 — NVIDIA Container Toolkit

```bash
# Paket deposunu ekle / Добавить репозиторий пакетов
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
    sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt update && sudo apt install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# Dogrula / Проверка — GPU'nun Docker icinde gorundugunden emin ol
docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi
```

---

### Adim 6 / Шаг 6 — DeepStream SDK 9.0

```bash
# ONEMLI: DeepStream 9.0 zorunludur — 7.x/8.0 Blackwell ile calismaz!
# ВАЖНО: Требуется DeepStream 9.0 — 7.x/8.0 не работает с Blackwell!

# NVIDIA Developer Portal'dan indir:
# https://developer.nvidia.com/deepstream-sdk-download
# -> DeepStream 9.0 for Linux -> deepstream_9.0.0-1_amd64.deb

# Gerekli kutuphaneleri kur / Установить зависимости
sudo apt install -y \
    libssl3 libgstreamer1.0-0 gstreamer1.0-tools \
    gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly gstreamer1.0-libav \
    libgstreamer-plugins-base1.0-0 \
    libgstrtspserver-1.0-0 libjansson4 libyaml-cpp-dev

sudo dpkg -i deepstream_9.0.0-1_amd64.deb
sudo ldconfig

# Dogrula / Проверка
deepstream-app --version
# deepstream-app version 9.0.0
```

---

### Adim 7 / Шаг 7 — Repo'yu Klonla / Клонировать репозиторий

```bash
git clone https://github.com/RuslanHinizov/ParkGuard-KZ-V2-Linux.git
cd ParkGuard-KZ-V2-Linux
```

---

### Adim 8 / Шаг 8 — .env Yapilandirmasi / Настройка .env

```bash
# Sablon dosyasindan olustur / Создать из шаблона
cp .env.example .env

# ZORUNLU: Tum sifreleri degistir!
# ОБЯЗАТЕЛЬНО: Измените все пароли!
nano .env
```

Asagidaki degerleri .env dosyasina yaz (sifreleri kendi degerlerinizle degistirin):

```env
# === DATABASE ===
POSTGRES_USER=parkguard
POSTGRES_PASSWORD=BURAYA_GUCLU_SIFRE_YAZ
POSTGRES_DB=parkguard
DATABASE_URL=postgresql+asyncpg://parkguard:BURAYA_GUCLU_SIFRE_YAZ@postgres:5432/parkguard
DATABASE_URL_SYNC=postgresql+psycopg2://parkguard:BURAYA_GUCLU_SIFRE_YAZ@postgres:5432/parkguard

# === REDIS ===
REDIS_PASSWORD=REDIS_SIFRE_YAZ
REDIS_URL=redis://:REDIS_SIFRE_YAZ@redis:6379/0

# === KAFKA ===
KAFKA_BOOTSTRAP_SERVERS=kafka:9092

# === MINIO ===
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=MINIO_SIFRE_YAZ
MINIO_ENDPOINT=minio:9000

# === APP ===
ENV=prod
CORS_ORIGINS=http://localhost,http://127.0.0.1
DATABASE_POOL_SIZE=10

# === KAMERALAR / KAMERY (RTSP URL'leri) ===
# Kendi kamera IP ve sifrenizi yazin / Введите IP и пароль ваших камер
CAMERA_01_RTSP=rtsp://admin:SIFRE@192.168.1.101:554/stream1
CAMERA_02_RTSP=rtsp://admin:SIFRE@192.168.1.102:554/stream1
CAMERA_03_RTSP=rtsp://admin:SIFRE@192.168.1.103:554/stream1
# ... diger kameralar / остальные камеры ...
```

---

### Adim 9 / Шаг 9 — TensorRT Engine Build (GPU'da bir kez / Один раз на GPU)

```bash
# YOLO11s ve OSNet modellerini indir ve TensorRT motoruna donustur
# Скачать модели YOLO11s и OSNet и конвертировать в TRT engine
# Bu islem GPU modeline gore 10-20 dakika surer
# Этот процесс занимает 10-20 минут

bash scripts/build_tensorrt_engines.sh

# Basarili ise su dosyalar olusur / При успехе создаются файлы:
# cpp-deepstream/models/yolo11s_fp16.engine
# cpp-deepstream/models/osnet_x0_25.engine
ls cpp-deepstream/models/*.engine
```

---

### Adim 10 / Шаг 10 — Nomeroff-net Modellerini Indir / Скачать модели Nomeroff-net

```bash
# OCR modellerini indir (~2-3 GB, internet gerektirir)
# Скачать OCR-модели (~2-3 ГБ, требуется интернет)
bash scripts/download_nomeroff_models.sh

# Modeller buraya indirilir / Модели загружаются сюда:
# ~/.cache/nomeroff/
ls ~/.cache/nomeroff/
```

---

### Adim 11 / Шаг 11 — C++ Pipeline'i Derle / Компиляция C++ пайплайна

```bash
cd cpp-deepstream
mkdir -p build && cd build

cmake .. \
    -DCMAKE_BUILD_TYPE=Release \
    -DDEEPSTREAM_ROOT=/opt/nvidia/deepstream/deepstream \
    -DTENSORRT_ROOT=/usr/local \
    -DCUDA_TOOLKIT_ROOT_DIR=/usr/local/cuda-12.8

make -j$(nproc)
# Basarili ise: [100%] Built target parkguard_deepstream

cd ../..
```

---

### Adim 12 / Шаг 12 — Altyapiyi Baslat / Запустить инфраструктуру

```bash
# Sadece veri tabani ve mesajlasma katmanini baslat
# Запустить только слой данных и обмена сообщениями
docker compose up -d postgres redis kafka zookeeper minio

# Hazir olmasini bekle / Ожидать готовности
sleep 20

# PostgreSQL hazir mi? / PostgreSQL готов?
docker compose exec postgres pg_isready -U parkguard
# /var/run/postgresql:5432 - accepting connections

# Kafka hazir mi? / Kafka готов?
docker compose exec kafka kafka-topics.sh \
    --bootstrap-server localhost:9092 --list
```

---

### Adim 13 / Шаг 13 — Veritabani Migrasyonu / Миграция базы данных

```bash
# Alembic migrasyonlarini calistir / Запустить миграции Alembic
docker compose run --rm event-api alembic upgrade head

# Baslangic kamera verilerini ekle (once scripts/seed_db.sh icine kamera bilgilerini yaz)
# Добавить начальные данные камер
bash scripts/seed_db.sh
```

---

### Adim 14 / Шаг 14 — Tum Servisleri Baslat / Запустить все сервисы

```bash
# Tum servisleri baslat / Запустить все сервисы
docker compose up -d

# Durumu kontrol et / Проверить статус
docker compose ps
# Tum satirlar "healthy" veya "Up" olmali
# Все строки должны быть "healthy" или "Up"

# Loglar / Логи
docker compose logs -f event-api
docker compose logs -f violation-service
docker compose logs -f deepstream
```

---

### Adim 15 / Шаг 15 — Frontend Derleme / Сборка фронтенда

```bash
cd frontend

# Node.js bagimliliklar / Установить зависимости
npm ci

# Production build
npm run build
# dist/ klasoru olusur / Создаётся директория dist/

cd ..
```

---

### Adim 16 / Шаг 16 — Nginx (Onerilendir / Рекомендуется)

```bash
sudo apt install -y nginx

# Nginx yapilandirmasi olustur / Создать конфигурацию nginx
sudo tee /etc/nginx/sites-available/parkguard > /dev/null << 'NGINX_CONF'
server {
    listen 80;
    server_name _;

    # Frontend (React build)
    root /home/USER/ParkGuard-KZ-V2-Linux/frontend/dist;
    index index.html;
    location / {
        try_files $uri /index.html;
    }

    # API proxy
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # WebSocket proxy
    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
    }

    # MJPEG stream - buffering kapatilmali / отключить буферизацию
    location ~ ^/api/v1/cameras/[^/]+/stream {
        proxy_pass http://127.0.0.1:8000;
        proxy_buffering off;
        proxy_set_header X-Accel-Buffering no;
    }
}
NGINX_CONF

# DIKKAT: 'USER' kelimesini kendi kullanici adinizla degistirin
# ВНИМАНИЕ: Замените 'USER' на ваше имя пользователя
sudo sed -i "s|/home/USER|/home/$USER|g" /etc/nginx/sites-available/parkguard

sudo ln -sf /etc/nginx/sites-available/parkguard /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx
```

---

## Запуск системы / Sistemi Başlatma

### Tam yeniden baslama / Полный перезапуск

```bash
cd ParkGuard-KZ-V2-Linux

# Her seyi durdur / Остановить всё
docker compose down

# Dogru sirayla yeniden baslat / Перезапустить в правильном порядке
docker compose up -d postgres redis kafka zookeeper minio
sleep 15
docker compose up -d event-api violation-service plate-service
sleep 5
docker compose up -d deepstream
docker compose up -d prometheus grafana loki alertmanager node-exporter cadvisor

# Durum / Статус
docker compose ps
```

### Sadece uygulama servislerini yeniden baslat / Перезапустить только сервисы приложения

```bash
docker compose restart event-api violation-service plate-service
```

---

## Первоначальная настройка / İlk Yapılandırma

Sistem basladiktan sonra tarayicida ac / После запуска открыть в браузере:

**http://SERVER_IP/** veya / или **http://localhost/**

### 1. Operator adi gir / Введите имя оператора
Ilk acilista isim modal penceresi cikar. Giris sayfasi yoktur.

### 2. Kamera ekle / Добавьте камеру (API veya UI)

```bash
curl -X POST http://localhost:8000/api/v1/cameras \
  -H "Content-Type: application/json" \
  -H "X-Operator-Name: admin" \
  -d '{
    "id": "cam_01",
    "name": "Parking A - Camera 1",
    "rtsp_url": "rtsp://admin:password@192.168.1.101:554/stream1",
    "enabled": true
  }'
```

### 3. Zone (yasak park bolgesi) olustur / Создайте зону

**UI yontemi:** Zones sayfasina git -> Kamera sec -> SVG uzerinde tikla-tikla (en az 3 nokta) -> Cift tikla ile kapat -> Kaydet

**API yontemi:**
```bash
curl -X POST http://localhost:8000/api/v1/zones \
  -H "Content-Type: application/json" \
  -H "X-Operator-Name: admin" \
  -d '{
    "camera_id": "cam_01",
    "name": "No Parking Zone 1",
    "zone_type": "no_parking",
    "polygon": [
      {"x": 100, "y": 200},
      {"x": 400, "y": 200},
      {"x": 400, "y": 500},
      {"x": 100, "y": 500}
    ],
    "threshold_seconds": 20,
    "exit_confirm_seconds": 10,
    "cooldown_seconds": 10,
    "color": "#FF0000",
    "enabled": true
  }'
```

### 4. Violations sayfasindan canli ihlalleri izle / Следите за нарушениями

Violations sayfasi: WebSocket ile canli guncelleme, 5 sn'de yeniden baglanma.

---

## Проверка работоспособности / Sistem Doğrulama

```bash
# Smoke test'i calistir / Запустить smoke test
bash scripts/smoke_test.sh

# Manuel kontroller / Ручные проверки:

# 1. event-api
curl http://localhost:8000/healthz
# {"status":"ok"}

# 2. violation-service
curl http://localhost:9200/healthz
# {"status":"ok"}

# 3. Prometheus metrikleri
curl http://localhost:8000/metrics | grep parkguard_event_api_up
# parkguard_event_api_up 1.0

# 4. Kafka topics
docker compose exec kafka kafka-topics.sh \
    --bootstrap-server localhost:9092 --list
# detections, snapshots, ocr_requests, ocr_results, penalty_cards

# 5. Veritabani tablolari / Таблицы базы данных
docker compose exec postgres psql -U parkguard -c "\dt"

# 6. Redis
docker compose exec redis redis-cli ping
# PONG

# 7. MinIO
curl http://localhost:9000/minio/health/live
# HTTP 200
```

---

## Форматы номерных знаков КЗ / KZ Plaka Formatları

| Тип / Tur | Format | Ornek / Пример |
|-----------|--------|---------------|
| Yeni standart / Новый | `dddLLLrr` | `123ABC02`, `999XYZ14` |
| Eski standart / Старый | `LdddLL[L]` | `A643BCG`, `K900EE` |
| Diplomatik / Дипломатический | `[DHFMTC]dddLL[L]` | `D123AB`, `HC987KL` |

**Bolge kodlari / Коды регионов:**

| Kod | Bolge |
|-----|-------|
| 01 | Akmola |
| 02 | Aktobe |
| 03 | Almaty Oblast |
| 04 | Atyrau |
| 05 | East Kazakhstan / Shyghys Qazaqstan |
| 06 | Zhambyl |
| 07 | West Kazakhstan / Batys Qazaqstan |
| 08 | Karaganda / Qaraghandy |
| 09 | Kostanay / Qostanay |
| 10 | Kyzylorda / Qyzylorda |
| 11 | Mangystau |
| 12 | Pavlodar |
| 13 | North Kazakhstan / Soltustik Qazaqstan |
| 14 | Turkestan |
| 16 | Astana (Nur-Sultan) |
| 17 | Almaty city |
| 18 | Shymkent |
| 19 | Abay |
| 20 | Zhetisu |

**OCR normalize kurallari / Правила нормализации OCR:**
- Kiril harfleri Latin'e cevrilir: А→A, В→B, Е→E, К→K, М→M, О→O, Р→P, С→C, Т→T, У→Y, Х→X
- Rakam slotlarinda harf hatasi duzeltilir: O→0, I→1, S→5, B→8, G→6
- Harf slotlarinda rakam hatasi duzeltilir: 0→O, 1→I, 8→B
- Bosluk ve tireler cikarilir: `123 ABC 02` → `123ABC02`
- Kucuk harfler buyutulur: `123abc02` → `123ABC02`

---

## Мониторинг / İzleme

| Arac / Инструмент | URL | Amac / Назначение |
|-------------------|-----|-------------------|
| Grafana Dashboard | http://localhost:3000 | Canli metrikler / Метрики в реальном времени |
| Prometheus | http://localhost:9090 | Metrik toplama / Сбор метрик |
| Alertmanager | http://localhost:9093 | Alert yonetimi / Управление алертами |
| MinIO Console | http://localhost:9001 | Snapshots ve PDF / Снимки и PDF |

**Grafana giris / Вход в Grafana:** kullanici/username: `admin` sifre/пароль: `admin`  
(Ilk girisde degistir / Сменить при первом входе)

**Onemli metrikler / Важные метрики:**
- `parkguard_event_api_up` — event-api canli / жив
- `parkguard_violation_service_up` — violation-service canli / жив
- `parkguard_detections_consumed_total` — toplam gozlem / всего наблюдений
- `parkguard_violations_written_total` — toplam ihlal / всего нарушений
- `parkguard_cvi_active` — aktif arac takibi / активных треков
- `parkguard_duplicates_prevented_total` — engellenen kopya / заблокировано дублей

---

## Тестирование / Test

```bash
# Violation-service (53 test)
cd python-services/violation-service
python3.12 -m pytest tests/ -v --tb=short

# Plate-service (484 test - Nomeroff olmadan stub ile)
cd ../plate-service
python3.10 -m pytest tests/ -v --tb=short

# Event-api (29 test)
cd ../event-api
python3.12 -m pytest tests/ -v --tb=short

# Tum testler toplam: 566 test, ~10 saniye
# Всего тестов: 566, ~10 секунд

# Yuk testi / Нагрузочный тест (canli event-api gerektirir / требует живой event-api)
cd ../../load-tests
pip install locust websocket-client
locust -f locustfile.py --host http://localhost:8000 \
    --users 50 --spawn-rate 5 --run-time 600s --headless
```

---

## Устранение неполадок / Sorun Giderme

### Ihlal olusmuyor / Нарушения не создаются

```bash
# 1. Kafka'da detection var mi?
docker compose exec kafka kafka-console-consumer.sh \
    --bootstrap-server localhost:9092 --topic detections --max-messages 5

# 2. Zone tanimli mi?
curl http://localhost:8000/api/v1/zones?camera_id=cam_01

# 3. violation-service metrikleri
curl http://localhost:9200/metrics | grep detections_consumed_total
```

### DeepStream baslamiyor / DeepStream не запускается

```bash
docker compose logs deepstream | tail -30

# "no kernel for sm_120" -> PyTorch CUDA 12.8 sürümü gerekli
# "TensorRT engine not found" -> scripts/build_tensorrt_engines.sh calistir
# "RTSP connection failed" -> kamera IP/sifre kontrol et
```

### "TensorRT engine not found" hatasi

```bash
bash scripts/build_tensorrt_engines.sh
# engine dosyalarinin varligi kontrol et:
ls cpp-deepstream/models/*.engine
```

### Port catismasi / Конфликт портов

```bash
sudo ss -tlnp | grep -E "8000|9200|9300|5432|6379|9092|9000"
```

### Detayli belgeler / Подробная документация

| Dosya / Файл | Icerik / Содержание |
|-------------|---------------------|
| `docs/runbook.md` | Incident response, alert aciklamalari |
| `docs/deployment.md` | Production checklist, env vars referansi |
| `docs/troubleshooting.md` | 12 hata senaryosu ve cozumleri |
| `docs/kz_plate_formats.md` | KZ plaka format ve OCR referansi |

---

## Тестовые результаты / Test Sonuclari

| Tur / Тип | Sonuc / Результат |
|-----------|-------------------|
| Violation-service unit testleri | 53/53 gecti |
| Plate-service accuracy testleri | 484/484 gecti |
| Event-api integration testleri | 29/29 gecti |
| KZ plaka accuracy (100 ground truth) | format >=98%, canonical >=97%, bolge >=99%, diplomatik 100% |
| Chaos testleri (10 senaryo) | 10/10 gecti |
| Throughput (12 kamera x 30fps x 60sn) | 4700+ gozlem/saniye |
| Duplicate violation rate | %0 (DB unique constraint garantisi) |

---

## Lisans / Лицензия

Proprietary — ParkGuard KZ, 2026. Tum haklari saklidir / Все права защищены.

---

*ParkGuard KZ V2 — DeepStream 9.0, YOLO11s, NvDCF, OSNet_x0_25, Nomeroff-net 4.0.1,*  
*FastAPI, React+TypeScript, PostgreSQL/PostGIS, Redis, Apache Kafka, MinIO,*  
*Prometheus, Grafana, Loki, Alertmanager ile insa edilmistir.*
