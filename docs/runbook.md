# ParkGuard KZ — Operations Runbook

> Target audience: on-call engineer who has never touched this service before.

---

## 1. Quick orientation

| Component | Port | Healthcheck |
|-----------|------|-------------|
| event-api (FastAPI) | 8000 | `GET /healthz` |
| violation-service (Python) | 9200 | `GET /healthz` |
| plate-service (Python) | 9300 | `GET /healthz` |
| PostgreSQL / PostGIS | 5432 | `pg_isready` |
| Redis | 6379 | `redis-cli ping` |
| Kafka | 9092 | `kafka-topics.sh --list` |
| MinIO | 9000 | `GET /minio/health/live` |
| Prometheus | 9090 | `GET /-/healthy` |
| Grafana | 3000 | `GET /api/health` |
| Loki | 3100 | `GET /ready` |
| Alertmanager | 9093 | `GET /-/healthy` |

---

## 2. Service startup order

```
PostgreSQL → Redis → Kafka → MinIO
    → event-api (runs migrations on boot)
    → violation-service
    → plate-service
    → cpp-deepstream (DeepStream pipeline)
```

### Docker Compose (development / staging)

```bash
# Full stack
docker compose up -d

# Check logs for a service
docker compose logs -f event-api

# Restart one service
docker compose restart violation-service
```

### Ubuntu production (systemd)

```bash
# Start all services
sudo systemctl start parkguard-event-api parkguard-violation-service parkguard-plate-service

# Check status
sudo systemctl status parkguard-event-api

# View logs
sudo journalctl -u parkguard-violation-service -f --since "1 hour ago"
```

---

## 3. Incident response flowchart

### 3.1 No violations appearing in dashboard

```
1. Is the DeepStream pipeline running?
   docker ps | grep deepstream  →  if missing, restart cpp container

2. Is Kafka receiving detections?
   kafka-console-consumer.sh --topic detections --max-messages 5
   →  if empty, DeepStream→Kafka bridge is broken (check KAFKA_BOOTSTRAP_SERVERS env)

3. Is violation-service consuming?
   curl localhost:9200/healthz  →  check "kafka_lag" in response
   Grafana: parkguard_detections_consumed_total  →  counter must be rising

4. Are zones configured?
   GET /api/v1/zones?camera_id=<cam>  →  must return ≥1 enabled zone
   If empty: log in to the web panel and draw a zone polygon

5. Is threshold met?
   Default threshold = 20 s. Vehicle must be inside zone for 20 s continuously.
```

### 3.2 Duplicate violations appearing

```
1. Check Redis active_violation keys:
   redis-cli keys "active_viol:*"  →  if empty after a violation, registry cleared too early

2. Check DB unique constraint:
   SELECT identity_key, cycle_id, COUNT(*) FROM violations
   GROUP BY identity_key, cycle_id HAVING COUNT(*) > 1;
   →  should return 0 rows

3. Check CVI cooldown:
   If two violations for same plate within <cooldown_seconds> (default 10 s),
   the cycle_id must differ.  If same cycle_id → state machine bug → escalate.
```

### 3.3 High error rate on event-api

```
1. Check Prometheus: rate(parkguard_event_api_http_requests_total{status=~"5.."}[5m])
2. Check Loki: {app="event-api"} |= "ERROR"
3. Common causes:
   a. PostgreSQL connection pool exhausted → increase DB_POOL_SIZE env
   b. Slow query → EXPLAIN ANALYZE on violations table (check indexes)
   c. MinIO unreachable (PDF generation failing) → check MinIO container
```

### 3.4 Kafka consumer lag growing

```
kafka-consumer-groups.sh --bootstrap-server localhost:9092 \
  --describe --group violation-service

# If LAG > 1000:
1. Check violation-service CPU: docker stats violation-service
2. Check DB write latency: Grafana → HTTP p95 panel
3. Scale violation-service replicas (if using Swarm/k8s)
4. Temporary mitigation: increase Kafka partition count for "detections" topic
```

---

## 4. Routine operations

### 4.1 Database backup

```bash
scripts/backup_db.sh
# Backup lands in: /var/backups/parkguard/postgres_YYYYMMDD.dump
```

### 4.2 Log retention cleanup

```bash
scripts/retention_cleanup.sh
# Deletes MinIO snapshots older than 90 days; Loki auto-expires at 30 days
```

### 4.3 Alembic migrations (production)

```bash
cd python-services/event-api
alembic upgrade head

# Rollback one step
alembic downgrade -1
```

### 4.4 Seed initial cameras and zones

```bash
scripts/seed_db.sh
# See scripts/seed_db.sh for customizable camera list
```

### 4.5 Force zone cache refresh (no restart needed)

```bash
# violation-service refreshes zones every 30 s automatically.
# To force immediate refresh, touch the zone in the web panel (Save).
# Or: redis-cli del zone_cache:*   (cache miss → immediate DB reload)
```

---

## 5. Performance tuning

| Parameter | Default | Where to change |
|-----------|---------|-----------------|
| CVI eviction window | 600 s | `CVIManager(max_idle_seconds=600)` |
| Zone cache TTL | 30 s | `ZoneCache(ttl_seconds=30)` |
| Kafka consumer fetch size | 1 MB | `KAFKA_FETCH_MAX_BYTES` env |
| DB connection pool | 10 | `DATABASE_POOL_SIZE` env |
| Violation threshold | 20 s | Per-zone setting in web panel |

---

## 6. Alert reference

| Alert | Severity | Action |
|-------|----------|--------|
| `ParkGuardEventApiDown` | critical | Restart event-api; check DB connection |
| `ParkGuardViolationServiceDown` | critical | Restart violation-service; check Kafka |
| `ParkGuardHighKafkaLag` | warning | Scale consumers; check DB latency |
| `ParkGuardHighErrorRate` | warning | Check logs; probable DB or MinIO issue |
| `ParkGuardHighCPU` | warning | Check DeepStream pipeline load |
| `ParkGuardDiskFull` | critical | Run retention_cleanup.sh; expand volume |
