# ParkGuard KZ — Troubleshooting Guide

---

## T-01: event-api returns 500 on `/api/v1/violations`

**Symptoms:** HTTP 500, log shows `asyncpg.exceptions.TooManyConnectionsError`

**Cause:** Connection pool exhausted (high concurrent load or leaked sessions).

**Fix:**
```bash
# Increase pool size
echo "DATABASE_POOL_SIZE=20" >> .env
docker compose restart event-api

# Find leaked connections
docker compose exec postgres psql -U parkguard -c \
  "SELECT pid, state, query_start, query FROM pg_stat_activity WHERE datname='parkguard';"
# Kill idle ones: SELECT pg_terminate_backend(pid) WHERE state='idle';
```

---

## T-02: violation-service not writing violations (stuck in INSIDE_ZONE)

**Symptoms:** Vehicles in zone, no violations in DB after 60+ seconds.

**Diagnosis:**
```bash
# Check state machine metrics
curl localhost:9200/metrics | grep parkguard_state_transitions_total

# If transitions counter is not rising → observations not reaching state machine
# Check Kafka consumption:
curl localhost:9200/metrics | grep parkguard_detections_consumed_total
```

**Possible causes:**
1. **Zone disabled** — check `enabled` flag via `GET /api/v1/zones`.
2. **Centroid outside polygon** — camera mount shifted; redraw zone in web panel.
3. **Threshold not reached** — default 20 s; check `threshold_seconds` in zone settings.
4. **CVI identity mismatch** — tracker reset mid-observation; check CVI match metrics:
   `curl localhost:9200/metrics | grep parkguard_cvi`

---

## T-03: Redis connection refused

**Symptoms:** event-api or violation-service logs `redis.exceptions.ConnectionError`

**Fix:**
```bash
docker compose up -d redis
redis-cli -h localhost -p 6379 -a "$REDIS_PASSWORD" ping
# Expected: PONG

# If auth fails, verify REDIS_PASSWORD in .env matches redis.conf requirepass
```

---

## T-04: Kafka topic `detections` has no messages

**Symptoms:** `kafka-console-consumer` shows nothing; violation-service idle.

**Diagnosis:**
```bash
# List consumer groups
kafka-consumer-groups.sh --bootstrap-server localhost:9092 --list

# Describe lag
kafka-consumer-groups.sh --bootstrap-server localhost:9092 \
  --describe --group violation-service
```

**Possible causes:**
1. DeepStream pipeline not running → `docker compose logs deepstream`
2. DeepStream Kafka sink misconfigured → check `cpp-deepstream/config/` for broker address
3. Topic does not exist → `kafka-topics.sh --create --topic detections ...`

---

## T-05: Grafana shows no data / "No data" panels

**Symptoms:** All Grafana panels say "No data".

**Fix:**
```bash
# Check Prometheus scrape targets
curl localhost:9090/api/v1/targets | python -m json.tool | grep health

# If event-api target is "down": check network between prometheus and event-api containers
# Verify: prometheus.yml scrape job host matches docker compose service name

# Reload Prometheus config without restart
curl -X POST localhost:9090/-/reload
```

---

## T-06: Loki not receiving logs

**Symptoms:** Loki log panel empty; `docker compose logs loki` shows schema errors.

**Fix:**
```bash
# Loki 3.x requires schema v13 with TSDB backend
# Verify ops/loki/loki.yml contains:
#   schema_config.configs[].store: tsdb
#   schema_config.configs[].schema: v13

docker compose restart loki

# Check Loki is ready
curl localhost:3100/ready
# Expected: ready
```

---

## T-07: Frontend cannot connect to WebSocket (`ws://...`)

**Symptoms:** Browser console shows `WebSocket connection failed`; Live indicator shows "Offline".

**Fix:**
1. Verify nginx (if used) proxies WebSocket headers:
   ```nginx
   location /ws/ {
       proxy_pass http://event-api:8000;
       proxy_http_version 1.1;
       proxy_set_header Upgrade $http_upgrade;
       proxy_set_header Connection "upgrade";
   }
   ```
2. Check event-api is accepting WS connections:
   ```bash
   curl -i -N -H "Connection: Upgrade" -H "Upgrade: websocket" \
     -H "Sec-WebSocket-Key: test" -H "Sec-WebSocket-Version: 13" \
     http://localhost:8000/ws/violations
   ```

---

## T-08: PDF generation fails (violation detail panel)

**Symptoms:** "Generate PDF" button returns error; event-api logs show MinIO error.

**Diagnosis:**
```bash
docker compose logs event-api | grep "minio\|pdf\|S3"
curl localhost:9000/minio/health/live
```

**Fix:**
```bash
docker compose up -d minio

# Verify bucket exists
docker compose exec minio mc ls local/parkguard-snapshots
# If missing: docker compose exec minio mc mb local/parkguard-snapshots
```

---

## T-09: Alembic migration fails with "table already exists"

**Symptoms:** `alembic upgrade head` raises `DuplicateTable`.

**Fix:**
```bash
# Check current revision
docker compose run --rm event-api alembic current

# Stamp the DB to the current state (if table was created manually)
docker compose run --rm event-api alembic stamp head

# Then retry
docker compose run --rm event-api alembic upgrade head
```

---

## T-10: High memory usage in violation-service

**Symptoms:** Container OOM-killed; `docker stats` shows violation-service >2 GB RAM.

**Cause:** CVI eviction not running, or very high vehicle count per camera.

**Fix:**
```bash
# Check CVI count metric
curl localhost:9200/metrics | grep parkguard_cvi_active

# If CVI count > 5000 and eviction not clearing them:
# Reduce eviction window (default 600 s):
# Add to .env: CVI_MAX_IDLE_SECONDS=300
docker compose restart violation-service
```

---

## T-11: DeepStream RTSP stream drops / camera reconnect

**Symptoms:** DeepStream logs `RTSP source disconnected`; no Kafka messages for that camera.

**Fix:**
DeepStream NvUriSrcBin automatically reconnects (configured in `cpp-deepstream/config/`). Check:
```bash
docker compose logs deepstream | grep "reconnect\|RTSP"
```

If stuck: restart only the DeepStream container (violation-service retains in-memory state):
```bash
docker compose restart deepstream
```

---

## T-12: Plate OCR accuracy degraded

**Symptoms:** Violations with `plate_text=null` increasing; OCR confidence low.

**Diagnosis:**
```bash
# Check plate-service health
curl localhost:9300/healthz

# Check OCR confidence histogram in Grafana
# Panel: "Plate OCR Confidence Distribution"
```

**Common causes:**
1. Camera lens dirty / misaligned → clean or reposition
2. Poor lighting (night without IR) → enable IR illuminator
3. Vehicle plate damaged / non-standard → expected; `plate_text=null` still creates violation
4. Model loaded incorrectly → `docker compose restart plate-service`
