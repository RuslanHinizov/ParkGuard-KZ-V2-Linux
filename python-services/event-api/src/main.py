"""
python-services/event-api/src/main.py

FastAPI entrypoint. From Adım 2 onwards:
  - GET  /healthz                    liveness
  - GET  /readyz                     readiness (DB/Redis/Kafka/MinIO)
  - GET  /metrics                    Prometheus scrape (populated step 20)
  - *    /api/v1/cameras             cameras CRUD + X-Operator-Name audit

Later adımlar plug their routers into this factory without changing the
lifespan or middleware wiring.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from shared import db, kafka_client, minio_client, redis_client
from shared.config import settings
from shared.logging import configure_logging, get_logger, new_trace_id
from shared.schemas import HealthStatus
from src.middleware.operator import OperatorContextMiddleware
from src.routers import cameras as cameras_router
from src.routers import violations as violations_router
from src.routers import zones as zones_router
from src.services.ws_manager import manager as ws_manager

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = get_logger(__name__)

API_VERSION = "0.4.0"


@asynccontextmanager
async def lifespan(app: FastAPI) -> "AsyncIterator[None]":
    """Startup / shutdown hooks — init Kafka producer + WS fanout task."""
    configure_logging(service_name="event-api")
    logger.info(
        "event_api_starting",
        version=API_VERSION,
        env=settings.ENV,
        cors=settings.cors_origins_list,
    )

    try:
        await kafka_client.get_producer()
    except Exception as exc:  # noqa: BLE001
        logger.warning("kafka_producer_startup_warn", error=str(exc))

    # Start Kafka→WebSocket fanout task for the violations stream.
    fanout_task = asyncio.create_task(_violations_fanout(), name="ws-fanout")

    try:
        yield
    finally:
        fanout_task.cancel()
        with suppress(asyncio.CancelledError):
            await fanout_task
        logger.info("event_api_stopping")
        await kafka_client.close_producer()
        await redis_client.close_redis()
        await db.dispose_engine()


async def _violations_fanout() -> None:
    """
    Background task: consume the `violations` Kafka topic and broadcast
    each ViolationEvent JSON to all connected WebSocket clients.

    Uses a dedicated consumer group so the event-api can replay violations
    independently of violation-service. `auto_offset_reset="latest"` keeps
    latency minimal; the dashboard only needs live events — historical data
    is served by the REST endpoint.
    """
    consumer = kafka_client.make_consumer(
        [settings.KAFKA_TOPIC_VIOLATIONS],
        group_id=f"{settings.KAFKA_CLIENT_ID}-ws-fanout",
        auto_offset_reset="latest",
    )
    try:
        await consumer.start()
        logger.info("violations_fanout_started")
        async for msg in consumer:
            if ws_manager.connection_count == 0:
                # No clients → skip decode work entirely.
                continue
            try:
                payload = json.loads(
                    msg.value.decode() if isinstance(msg.value, (bytes, bytearray)) else msg.value
                )
                await ws_manager.broadcast(payload)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ws_fanout_broadcast_failed", error=str(exc))
            with suppress(Exception):
                await consumer.commit()
    except asyncio.CancelledError:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.exception("violations_fanout_error", error=str(exc))
    finally:
        with suppress(Exception):
            await consumer.stop()


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

    # --- CORS (LAN-only; nginx tightens further in prod) -------------------
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*", "X-Operator-Name", "X-Trace-Id"],
        expose_headers=["X-Operator-Name", "X-Trace-Id"],
    )

    # --- X-Operator-Name + trace_id capture/enforcement --------------------
    app.add_middleware(OperatorContextMiddleware)

    # --- Ops routes (no router file — trivial) -----------------------------
    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "event-api", "version": API_VERSION}

    @app.get("/readyz", response_model=HealthStatus, tags=["ops"])
    async def readyz() -> HealthStatus:
        checks = {
            "postgres": await db.ping(),
            "redis": await redis_client.ping(),
            "kafka": await kafka_client.ping(),
            "minio": await minio_client.ping(),
        }
        healthy = all(checks.values())
        status_ = "ok" if healthy else ("degraded" if any(checks.values()) else "down")
        return HealthStatus(
            status=status_,
            version=API_VERSION,
            service="event-api",
            checks=checks,
            timestamp=datetime.now(timezone.utc),
        )

    @app.get("/metrics", tags=["ops"])
    async def metrics() -> Response:
        body = (
            "# HELP parkguard_event_api_up 1 if the event-api is up.\n"
            "# TYPE parkguard_event_api_up gauge\n"
            "parkguard_event_api_up 1\n"
        )
        return Response(content=body, media_type="text/plain; version=0.0.4")

    # --- Feature routers ---------------------------------------------------
    app.include_router(cameras_router.router)
    app.include_router(zones_router.camera_zones)
    app.include_router(zones_router.zones)
    app.include_router(violations_router.router)
    app.include_router(violations_router.ws_router)

    return app


app = create_app()
