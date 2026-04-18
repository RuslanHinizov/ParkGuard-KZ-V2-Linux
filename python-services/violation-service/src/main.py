"""
python-services/violation-service/src/main.py

Adım 4 — skeleton entry point. The full state machine (spec §8) lands in
Adım 5; this file is here now so:

  * the Kafka consumer lifecycle (connect, manual commit, graceful
    shutdown on SIGTERM) is already exercised end-to-end
  * the health/metrics HTTP server is in the compose graph
  * later adımlar can plug in without reshaping the process

What this skeleton does:
  1. Configure logging + connect to Postgres / Redis / Kafka / MinIO
  2. Consume from topic `detections` with manual commit (spec §15.1)
  3. For each message: parse into DetectionMessage, log a counter, and
     commit the offset. NO state machine, NO writes to `violations`.
  4. Expose :9200/healthz + :9200/metrics (Prometheus).

Adım 5 replaces step 3 with the CVI manager + state machine logic.
"""

from __future__ import annotations

import asyncio
import signal
from contextlib import suppress
from typing import TYPE_CHECKING

from aiokafka.errors import KafkaError
from pydantic import ValidationError

from shared import db, kafka_client, minio_client, redis_client
from shared.config import settings
from shared.logging import bind_context, configure_logging, get_logger, new_trace_id
from shared.schemas import DetectionMessage
from src.health import HealthServer
from src.metrics import DETECTIONS_CONSUMED, MESSAGES_FAILED, UP

if TYPE_CHECKING:
    from aiokafka import AIOKafkaConsumer

logger = get_logger(__name__)

SERVICE_NAME = "violation-service"


# --------------------------------------------------------------------------- #
# Runtime
# --------------------------------------------------------------------------- #
class Runtime:
    """Owns every long-lived resource so shutdown is one call."""

    def __init__(self) -> None:
        self.shutdown_event: asyncio.Event = asyncio.Event()
        self.health: HealthServer | None = None
        self.consumer: AIOKafkaConsumer | None = None

    async def startup(self) -> None:
        configure_logging(service_name=SERVICE_NAME)
        logger.info(
            "violation_service_starting",
            env=settings.ENV,
            brokers=settings.KAFKA_BOOTSTRAP_SERVERS,
        )
        # Warm connections so /readyz on the health server is honest.
        await db.ping()
        await redis_client.ping()
        await minio_client.ping()

        self.consumer = await kafka_client.make_consumer(
            topic="detections",
            group_id="violation-service",
        )
        await self.consumer.start()

        self.health = HealthServer(
            host="0.0.0.0",  # noqa: S104 — LAN-only per spec §11
            port=9200,
            shutdown_event=self.shutdown_event,
        )
        await self.health.start()
        UP.set(1)

    async def consume(self) -> None:
        assert self.consumer is not None
        try:
            async for msg in self.consumer:
                trace_id = _extract_trace(msg.headers) or new_trace_id()
                bind_context(
                    trace_id=trace_id,
                    topic=msg.topic,
                    partition=msg.partition,
                    offset=msg.offset,
                )
                try:
                    payload = DetectionMessage.model_validate_json(
                        msg.value.decode() if isinstance(msg.value, (bytes, bytearray)) else msg.value
                    )
                    DETECTIONS_CONSUMED.labels(camera=payload.sensor_id).inc(len(payload.objects))
                    # Adım 5 will invoke cvi_manager + state_machine here.
                    logger.debug(
                        "detection_received",
                        camera=payload.sensor_id,
                        frame=payload.frame_id,
                        object_count=len(payload.objects),
                    )
                except ValidationError as exc:
                    MESSAGES_FAILED.labels(reason="schema").inc()
                    logger.warning("detection_schema_invalid", error=str(exc))
                except Exception as exc:  # noqa: BLE001 — log + continue
                    MESSAGES_FAILED.labels(reason="unknown").inc()
                    logger.exception("detection_handler_failed", error=str(exc))

                # manual commit per spec §15.1
                await self.consumer.commit()

                if self.shutdown_event.is_set():
                    break
        except KafkaError as exc:  # noqa: BLE001
            logger.exception("kafka_consumer_error", error=str(exc))

    async def shutdown(self) -> None:
        logger.info("violation_service_stopping")
        UP.set(0)
        if self.consumer is not None:
            with suppress(Exception):
                await self.consumer.stop()
        if self.health is not None:
            await self.health.stop()
        await kafka_client.close_producer()
        await redis_client.close_redis()
        await db.dispose_engine()


def _extract_trace(headers: list[tuple[str, bytes]] | None) -> str | None:
    if not headers:
        return None
    for k, v in headers:
        if k.lower() == "trace_id":
            try:
                return v.decode() if isinstance(v, (bytes, bytearray)) else str(v)
            except Exception:  # noqa: BLE001
                return None
    return None


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
async def amain() -> None:
    rt = Runtime()

    def _sigterm(*_args: object) -> None:
        logger.info("signal_received_shutting_down")
        rt.shutdown_event.set()

    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, _sigterm)
        except NotImplementedError:  # Windows
            signal.signal(s, _sigterm)

    await rt.startup()
    try:
        consumer_task = asyncio.create_task(rt.consume())
        await rt.shutdown_event.wait()
        consumer_task.cancel()
        with suppress(asyncio.CancelledError):
            await consumer_task
    finally:
        await rt.shutdown()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
