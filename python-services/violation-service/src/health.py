"""
python-services/violation-service/src/health.py

Tiny aiohttp server serving /healthz, /readyz, /metrics on a background
task of the same event loop as the Kafka consumer. We deliberately avoid
pulling in FastAPI here — this service has no REST surface area, and a
200-line aiohttp app is plenty.

The readiness endpoint pings every dependency (DB / Redis / Kafka /
MinIO) so Prometheus alerting can distinguish between "I'm alive but
Kafka is flapping" and "process is dead" without parsing logs.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aiohttp import web
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from shared import db, kafka_client, minio_client, redis_client
from shared.logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class HealthServer:
    def __init__(self, host: str, port: int, shutdown_event: asyncio.Event) -> None:
        self.host = host
        self.port = port
        self._shutdown = shutdown_event
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/healthz", self._healthz)
        app.router.add_get("/readyz", self._readyz)
        app.router.add_get("/metrics", self._metrics)

        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        logger.info("health_server_listening", host=self.host, port=self.port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    # ---- handlers -------------------------------------------------------- #
    async def _healthz(self, _req: web.Request) -> web.Response:
        return web.json_response(
            {"status": "ok" if not self._shutdown.is_set() else "draining",
             "service": "violation-service"}
        )

    async def _readyz(self, _req: web.Request) -> web.Response:
        checks = {
            "postgres": await db.ping(),
            "redis":    await redis_client.ping(),
            "kafka":    await kafka_client.ping(),
            "minio":    await minio_client.ping(),
        }
        healthy = all(checks.values())
        status = 200 if healthy else 503
        return web.json_response(
            {
                "status": "ok" if healthy else ("degraded" if any(checks.values()) else "down"),
                "checks": checks,
            },
            status=status,
        )

    async def _metrics(self, _req: web.Request) -> web.Response:
        return web.Response(body=generate_latest(), content_type=CONTENT_TYPE_LATEST)
