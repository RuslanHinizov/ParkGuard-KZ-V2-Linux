"""
python-services/snapshot-consumer/src/health.py

aiohttp health endpoints — same shape as plate-service / violation-service
so the Prometheus scrape config is uniform across the fleet (spec §12.4).
"""

from __future__ import annotations

import asyncio

from aiohttp import web
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from shared import kafka_client, minio_client
from shared.logging import get_logger

logger = get_logger(__name__)


class HealthServer:
    def __init__(
        self,
        host: str,
        port: int,
        shutdown_event: asyncio.Event,
        *,
        is_ready: "callable[[], bool] | None" = None,  # type: ignore[name-defined]
    ) -> None:
        self.host = host
        self.port = port
        self._shutdown = shutdown_event
        self._is_ready = is_ready
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

    async def _healthz(self, _req: web.Request) -> web.Response:
        return web.json_response(
            {
                "status":  "ok" if not self._shutdown.is_set() else "draining",
                "service": "snapshot-consumer",
            }
        )

    async def _readyz(self, _req: web.Request) -> web.Response:
        checks = {
            "kafka": await kafka_client.ping(),
            "minio": await minio_client.ping(),
            "ready": bool(self._is_ready() if self._is_ready else True),
        }
        healthy = all(checks.values())
        return web.json_response(
            {"status": "ok" if healthy else "degraded", "checks": checks},
            status=200 if healthy else 503,
        )

    async def _metrics(self, _req: web.Request) -> web.Response:
        return web.Response(body=generate_latest(), content_type=CONTENT_TYPE_LATEST)
