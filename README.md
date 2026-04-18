# ParkGuard KZ

**TR**: Kazakistan plakalarına optimize edilmiş, 8–12 kameralı gerçek zamanlı yasak park ihlali tespit ve otomatik ceza protokolü üretim sistemi.
**RU**: Система реального времени для выявления парковочных нарушений и автоматической генерации протоколов на 8–12 камер с распознаванием казахстанских номерных знаков.

---

## İçindekiler / Содержание

- [Türkçe](#türkçe)
- [Русский](#русский)
- [Architecture (EN summary)](#architecture)

---

## Türkçe

### Özet

ParkGuard KZ, RTSP kameralardan gelen canlı video akışında operatörün çizdiği yasak park bölgelerine **20 saniyeden fazla** park eden araçları tespit eder, Kazakistan plakalarını (yeni `123ABC02`, eski `A643KCG`, diplomatik `D123AB` formatları dahil) tanır ve her ihlal için PDF ceza protokolü üretir. Dashboard **yerel ağda** çalışır, **login yoktur** — güvenlik ağ seviyesinde sağlanır.

### Donanım ve yazılım

- **GPU**: NVIDIA RTX 5070 Ti (Blackwell, sm_120, 16 GB VRAM)
- **CPU**: Intel i7 14. nesil (hybrid P+E)
- **RAM**: 32 GB DDR5
- **OS**: Ubuntu 24.04 LTS
- **Driver**: ≥ 590.48 (open kernel modules)
- **CUDA**: 12.8+, **TensorRT**: 10.14.1.48+
- **DeepStream SDK**: 9.0 (eski sürümler Blackwell'de çalışmaz)
- **Python**: 3.10 (plate-service), 3.12 (diğer servisler)
- **Node**: 20+ (frontend)

### Hızlı başlangıç (dev)

```bash
# 1. Repo'yu klonla
git clone <repo-url> parkguard-kz
cd parkguard-kz

# 2. Ortam değişkenleri
cp .env.example .env
# .env dosyasını düzenle — CHANGE_ME_* değerlerini gerçek değerlerle değiştir

# 3. TensorRT engine'lerini HOST'TA build et (Blackwell için gerekli)
./scripts/build_tensorrt_engines.sh

# 4. Nomeroff-net modellerini indir
./scripts/download_nomeroff_models.sh

# 5. Infrastructure'ı ayağa kaldır
docker compose up -d postgres redis kafka minio

# 6. DB migration
docker compose run --rm event-api alembic upgrade head

# 7. Tüm servisleri başlat
docker compose up -d

# 8. Smoke test
./scripts/smoke_test.sh

# 9. Dashboard
# http://localhost:5173
```

### Temel iş akışı

1. **Zone çiz**: Dashboard → "Zone Editor" — kamera snapshot'ı üzerinde polygon çiz
2. **İhlal**: Araç polygon içinde 20sn+ kalırsa kırmızı bbox + countdown ekrana düşer
3. **Ceza**: 20 sn'de DB'ye violation kaydı + Kafka üzerinden PDF üretim tetiklenir
4. **Protokol**: Dashboard → "Violation Detail" — Türkçe/Rusça/İngilizce 3 dilli PDF indir

### Kritik tasarım kuralları

| Kural | Açıklama |
|-------|----------|
| **Duplicate ceza = 0** | 4 katmanlı savunma: CVI identity + Redis active registry + exit confirmation + DB unique constraint |
| **Raw frame Python'a asla gitmez** | C++ DeepStream hot path — sadece event-driven snapshot tap |
| **Login YOK** | LAN-only, güvenlik ağ seviyesinde (UFW + IP whitelist + VPN) |
| **Config-driven** | Tüm eşikler `.env` veya DB'de — kodda magic number yasak |
| **State Redis + AOF** | Service restart'ta CVI ve aktif ihlaller geri yüklenir |

### Dizin yapısı

```
parkguard-kz/
├── cpp-deepstream/        # C++ DeepStream app (decode + detection + tracking)
├── python-services/       # violation, plate, event-api, snapshot, penalty-card, worker
├── frontend/              # React + TypeScript + Vite + TailwindCSS
├── migrations/            # Alembic DB migrations
├── ops/                   # Prometheus, Grafana, Loki, Alertmanager
├── scripts/               # Build, seed, backup, smoke test
└── docs/                  # Architecture, runbook, troubleshooting, API
```

### Geliştirici komutları

```bash
# Lint & type check (Python)
make lint

# Testler (pytest + testcontainers)
make test

# C++ build (Ubuntu host, CUDA 12.8+, TensorRT 10.14+)
make cpp-build

# Frontend dev server
make frontend-dev

# Tüm servisleri restart
docker compose restart
```

### Yasal notlar

- Kazakistan plaka formatları: yeni (2012+) `DDDLLLDD`, eski A-tipi, diplomatik (D/T/HC/M/H/F)
- Bölge kodları: 01 Astana – 20 Ulytau (spec §9.3)
- Timezone: `Asia/Almaty` (UTC+5, yaz saati yok)
- Retention: snapshot 90 gün, violation 2 yıl, penalty card 5 yıl, audit log 5 yıl
- Para/ceza tutarı **yazılmaz** — bu sadece ihlal protokolüdür; ücret yetkili kurum belirler

### Dokümantasyon

- [docs/architecture.md](docs/architecture.md) — mimari detay
- [docs/runbook.md](docs/runbook.md) — on-call rehberi
- [docs/troubleshooting.md](docs/troubleshooting.md) — sık hatalar
- [docs/api.md](docs/api.md) — REST & WebSocket API
- `parking_violation_system_prompt.md` — tam spec (1671 satır, repo kökü)

---

## Русский

### Обзор

ParkGuard KZ — система реального времени, которая на видеопотоках RTSP с 8–12 камер выявляет автомобили, припаркованные **более 20 секунд** в запрещённых зонах, нарисованных оператором, распознаёт казахстанские номерные знаки (новый формат `123ABC02`, старый `A643KCG`, дипломатические `D123AB`) и генерирует PDF-протокол для каждого нарушения. Панель работает **в локальной сети без авторизации** — безопасность обеспечивается на сетевом уровне.

### Оборудование и ПО

- **GPU**: NVIDIA RTX 5070 Ti (Blackwell, sm_120, 16 ГБ VRAM)
- **CPU**: Intel i7 14-го поколения (hybrid P+E)
- **ОЗУ**: 32 ГБ DDR5
- **ОС**: Ubuntu 24.04 LTS
- **Драйвер**: ≥ 590.48 (open kernel modules)
- **CUDA**: 12.8+, **TensorRT**: 10.14.1.48+
- **DeepStream SDK**: 9.0 (старые версии не работают на Blackwell)
- **Python**: 3.10 (plate-service), 3.12 (остальные сервисы)
- **Node**: 20+ (frontend)

### Быстрый старт (dev)

```bash
# 1. Клонировать репозиторий
git clone <repo-url> parkguard-kz
cd parkguard-kz

# 2. Переменные окружения
cp .env.example .env
# Отредактируй .env — замени все CHANGE_ME_* на реальные значения

# 3. Собери TensorRT engine'ы НА ХОСТЕ (обязательно для Blackwell)
./scripts/build_tensorrt_engines.sh

# 4. Скачай модели Nomeroff-net
./scripts/download_nomeroff_models.sh

# 5. Подними инфраструктуру
docker compose up -d postgres redis kafka minio

# 6. Миграция БД
docker compose run --rm event-api alembic upgrade head

# 7. Запусти все сервисы
docker compose up -d

# 8. Smoke-тест
./scripts/smoke_test.sh

# 9. Панель управления
# http://localhost:5173
```

### Основной рабочий процесс

1. **Нарисовать зону**: панель → «Zone Editor» — нарисовать полигон на snapshot'е камеры
2. **Нарушение**: если автомобиль в полигоне более 20 сек — красный bbox + обратный отсчёт
3. **Протокол**: через 20 сек — запись в БД + триггер генерации PDF через Kafka
4. **Документ**: панель → «Violation Detail» — скачать трёхъязычный PDF (KZ / RU / EN)

### Ключевые принципы проектирования

| Правило | Описание |
|---------|----------|
| **0 дубликатов штрафов** | 4-уровневая защита: CVI identity + Redis registry + exit confirmation + DB unique constraint |
| **Raw-кадры никогда не идут в Python** | Hot path на C++ DeepStream — только event-driven snapshot |
| **Без логина** | LAN-only, безопасность на сетевом уровне (UFW + IP whitelist + VPN) |
| **Config-driven** | Все пороги в `.env` или БД — «магические числа» в коде запрещены |
| **Состояние в Redis + AOF** | После перезапуска CVI и активные нарушения восстанавливаются |

### Структура каталогов

```
parkguard-kz/
├── cpp-deepstream/        # C++ DeepStream (декодирование + детекция + трекинг)
├── python-services/       # violation, plate, event-api, snapshot, penalty-card, worker
├── frontend/              # React + TypeScript + Vite + TailwindCSS
├── migrations/            # Миграции Alembic
├── ops/                   # Prometheus, Grafana, Loki, Alertmanager
├── scripts/               # Сборка, seed, backup, smoke-тест
└── docs/                  # Архитектура, runbook, troubleshooting, API
```

### Команды разработчика

```bash
# Линт и проверка типов (Python)
make lint

# Тесты (pytest + testcontainers)
make test

# C++ сборка (Ubuntu host, CUDA 12.8+, TensorRT 10.14+)
make cpp-build

# Frontend dev-сервер
make frontend-dev

# Перезапустить все сервисы
docker compose restart
```

### Юридические замечания

- Форматы казахстанских номеров: новый (с 2012) `DDDLLLDD`, старый «A-тип», дипломатические (D/T/HC/M/H/F)
- Коды регионов: 01 Астана – 20 Улытау (spec §9.3)
- Часовой пояс: `Asia/Almaty` (UTC+5, без перехода на летнее время)
- Хранение: snapshot 90 дней, violation 2 года, penalty-card 5 лет, audit-log 5 лет
- Сумма штрафа **не указывается** — это только протокол нарушения; размер штрафа определяет уполномоченный орган

### Документация

- [docs/architecture.md](docs/architecture.md) — архитектура
- [docs/runbook.md](docs/runbook.md) — руководство on-call
- [docs/troubleshooting.md](docs/troubleshooting.md) — частые ошибки
- [docs/api.md](docs/api.md) — REST и WebSocket API
- `parking_violation_system_prompt.md` — полная спецификация (1671 строка, корень репо)

---

## Architecture

High-level flow (see [docs/architecture.md](docs/architecture.md) for details):

```
  RTSP cameras (8-12)
         │
         ▼
  ┌──────────────────────────┐
  │  C++ DeepStream app      │    YOLO11s FP16 → OSNet ReID → NvDCF tracker
  │  (hot path, GPU)         │    → Kafka (detections) + snapshot tap
  └─────────┬────────────────┘
            │
     Kafka / Redpanda
            │
   ┌────────┼────────────────────────────┐
   ▼        ▼        ▼          ▼         ▼
violation  plate  snapshot   penalty    event-api
service   service consumer   card svc   (REST+WS)
   │        │        │          │         │
   └────────┴────────┴──────────┴─────────┘
                     │
        PostgreSQL + PostGIS + Redis (AOF) + MinIO
                     │
                     ▼
         React + TypeScript + Vite (LAN-only)
```

**Principles**: no-login LAN dashboard, composite vehicle identity (4-tier fallback), state machine per (CVI × zone), idempotent violation write (Redis registry + DB unique constraint), Kafka-decoupled services, GPU-only hot path in C++.

---

## Lisans / Лицензия

Proprietary — ParkGuard KZ project (internal). See `LICENSE` (to be added).

## Sorumluluk reddi / Отказ от ответственности

**TR**: Bu sistem gerçek trafik cezası üretir. Deploy etmeden önce [docs/runbook.md](docs/runbook.md) ve spec §17 (Security Checklist) maddelerini mutlaka uygula.
**RU**: Система генерирует реальные штрафы ГАИ. Перед развёртыванием обязательно выполни чек-лист безопасности из [docs/runbook.md](docs/runbook.md) и раздела §17 спецификации.
