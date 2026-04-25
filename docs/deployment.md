# ParkGuard KZ — Deployment Guide

## Prerequisites

| Requirement | Version |
|-------------|---------|
| Ubuntu Server | 22.04 LTS |
| Docker Engine | ≥ 27.x |
| Docker Compose | ≥ 2.x |
| NVIDIA driver | ≥ 535 (for DeepStream) |
| NVIDIA Container Toolkit | latest |
| Python | 3.12 (for local dev) |
| Node.js | 20 LTS (for frontend build) |

---

## 1. First-time deployment

### 1.1 Clone and configure

```bash
git clone <repo-url> parkguard-kz
cd parkguard-kz

cp .env.example .env
# Edit .env — set strong passwords for POSTGRES_PASSWORD, REDIS_PASSWORD, MINIO_ROOT_PASSWORD
```

### 1.2 Build TensorRT engines (GPU host only)

```bash
# Run once per GPU model — takes ~10 minutes
scripts/build_tensorrt_engines.sh
```

### 1.3 Download Nomeroff-net OCR models

```bash
scripts/download_nomeroff_models.sh
```

### 1.4 Start infrastructure

```bash
docker compose up -d postgres redis kafka minio
sleep 10  # wait for Postgres to become ready

# Run DB migrations
docker compose run --rm event-api alembic upgrade head

# Seed initial cameras (edit the script first)
scripts/seed_db.sh
```

### 1.5 Start all services

```bash
docker compose up -d
```

### 1.6 Build and serve the frontend

```bash
cd frontend
npm ci
npm run build
# Serve dist/ behind nginx or copy to event-api's static root
```

### 1.7 Verify

```bash
# All containers must be healthy
docker compose ps

# Healthchecks
curl localhost:8000/healthz   # event-api
curl localhost:9200/healthz   # violation-service
curl localhost:3000/api/health  # grafana
```

---

## 2. Environment variables reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | yes | — | `postgresql+asyncpg://user:pass@host:5432/parkguard` |
| `DATABASE_URL_SYNC` | yes | — | psycopg2 URL (for Alembic) |
| `POSTGRES_PASSWORD` | yes | — | Postgres root password |
| `REDIS_URL` | yes | — | `redis://:pass@host:6379/0` |
| `REDIS_PASSWORD` | yes | — | Redis password |
| `KAFKA_BOOTSTRAP_SERVERS` | yes | — | `broker:9092` |
| `MINIO_ENDPOINT` | yes | — | `host:9000` |
| `MINIO_ROOT_USER` | yes | `minioadmin` | MinIO access key |
| `MINIO_ROOT_PASSWORD` | yes | — | MinIO secret key |
| `ENV` | no | `dev` | `dev` / `staging` / `prod` |
| `CORS_ORIGINS` | no | `*` | Comma-separated allowed origins |
| `VIOLATION_THRESHOLD_SECONDS` | no | `20` | Global default threshold |
| `DATABASE_POOL_SIZE` | no | `10` | SQLAlchemy connection pool size |

---

## 3. Production checklist

- [ ] Strong passwords set in `.env`
- [ ] `ENV=prod` in `.env`
- [ ] `CORS_ORIGINS` set to specific frontend IP/domain
- [ ] TLS termination in front of event-api (nginx/Caddy)
- [ ] Prometheus data volume is on a separate disk
- [ ] Automated `backup_db.sh` cron job configured
- [ ] Alertmanager webhook/email configured
- [ ] Firewall: only port 80/443 exposed externally (all others LAN-only)
- [ ] NVIDIA driver and Container Toolkit installed
- [ ] DeepStream 9.0 container accessible

---

## 4. Upgrading

```bash
git pull origin main
docker compose pull
docker compose up -d

# Run any new migrations
docker compose run --rm event-api alembic upgrade head

# Rebuild frontend if changed
cd frontend && npm ci && npm run build
```

---

## 5. Rollback

```bash
# Roll back to a previous image tag
docker compose down
git checkout <previous-tag>
docker compose up -d

# Roll back DB migration
docker compose run --rm event-api alembic downgrade -1
```

---

## 6. Service architecture diagram

```
Internet / LAN
       │
   nginx (TLS)
       │
   event-api :8000
   ├── REST  /api/v1/...
   ├── WS    /ws/violations
   └── Metrics /metrics
       │
   PostgreSQL ←── violation-service ←── Kafka ←── DeepStream
   MinIO      ←── violation-service          ←── plate-service
   Redis      ←── violation-service
```
