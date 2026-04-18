"""
python-services/event-api/src/main.py

FastAPI entrypoint. In Adım 1 (infra) the API exposes only:
  - GET /healthz    liveness (always 200 while process is up)
  - GET /readyz     readiness (checks DB/Redis/Kafka/MinIO)
  - GET /metrics    Prometheus (scaffolded; populated in step 20)

Adım 2 adds the Camera CRUD + X-Operator-Name middleware + router
wiring; all of that plugs into the app factory here without changing
the lifecycle.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from shared import db, kafka_client, minio_client, redis_client
from shared.config import settings
from shared.logging import configure_logging, get_logger
from shared.schemas import HealthStatus

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = get_logger(__name__)

API_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI) -> "AsyncIterator[None]":
    """Startup / shutdown hooks — init Kafka producer eagerly."""
    configure_logging(service_name="event-api")
    logger.info(
        "event_api_starting",
        version=API_VERSION,
        env=settings.ENV,
        cors=settings.cors_origins_list,
    )

    # Eagerly open the Kafka producer so readyz reflects true broker state.
    try:
        await kafka_client.get_producer()
    except Exception as exc:  # noqa: BLE001 — startup should not crash hard
        logger.warning("kafka_producer_startup_warn", error=str(exc))

    try:
        yield
    finally:
        logger.info("event_api_stopping")
        await kafka_client.close_producer()
        await redis_client.close_redis()
        await db.dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(
        title="ParkGuard KZ — Event API",
        description=(
            "Real-time parking violation event gateway. "
            "LAN-only; no authentication — see spec §11 / §17."
        ),
        version=API_VERSION,
        lifespan=lifespan,
    )

    # CORS — LAN origins only. Tightening happens at nginx in prod.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*", "X-Operator-Name"],
        expose_headers=["X-Operator-Name"],
    )

    # ---- Ops routes (no router file — trivial) -----------------------------
    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        """Liveness: returns 200 as long as the process is up."""
        return {"status": "ok", "service": "event-api", "version": API_VERSION}

    @app.get("/readyz", response_model=HealthStatus, tags=["ops"])
    async def readyz() -> HealthStatus:
        """Readiness: checks every upstream dependency."""
        checks = {
            "postgres": await db.ping(),
            "redis": await redis_client.ping(),
            "kafka": await kafka_client.ping(),
            "minio": await minio_client.ping(),
        }
        healthy = all(checks.values())
        status = "ok" if healthy else ("degraded" if any(checks.values()) else "down")
        return HealthStatus(
            status=status,
            version=API_VERSION,
            service="event-api",
            checks=checks,
            timestamp=datetime.now(timezone.utc),
        )

    @app.get("/metrics", tags=["ops"])
    async def metrics() -> Response:
        """Prometheus scrape endpoint — populated in step 20."""
        body = (
            "# HELP parkguard_event_api_up 1 if the event-api is up.\n"
            "# TYPE parkguard_event_api_up gauge\n"
            "parkguard_event_api_up 1\n"
        )
        return Response(content=body, media_type="text/plain; version=0.0.4")

    return app


app = create_app()
