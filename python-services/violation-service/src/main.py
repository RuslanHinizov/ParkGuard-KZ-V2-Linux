"""
python-services/violation-service/src/main.py

Adım 5 — CVI manager + state machine wired into the Kafka consume
loop. Each `DetectionMessage` is expanded into one Observation per
detected object; the CVI manager resolves identity; the state machine
advances every zone; violations land in Postgres through the unique-
key-protected writer.

Process graph:

    detections (Kafka)
        → DetectionMessage
            → Observation (× objects)
                → CVIManager.resolve
                    → StateMachine.evaluate per zone
                        → ViolationWriter.insert (on VIOLATED)
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
from src.active_registry import ActiveViolationRegistry
from src.cvi_manager import CVIManager
from src.health import HealthServer
from src.metrics import CVI_ACTIVE, CVI_EVICTIONS, DETECTIONS_CONSUMED, MESSAGES_FAILED, UP
from src.observation import Observation
from src.ocr_results_consumer import OCRResultsConsumer
from src.plate_ocr_requester import PlateOCRRequester
from src.state_machine import StateMachine
from src.violation_writer import ViolationWriter
from src.zone_cache import ZoneCache

if TYPE_CHECKING:
    from aiokafka import AIOKafkaConsumer

logger = get_logger(__name__)

SERVICE_NAME = "violation-service"


class Runtime:
    """Owns every long-lived resource so shutdown is one call."""

    def __init__(self) -> None:
        self.shutdown_event: asyncio.Event = asyncio.Event()
        self.health: HealthServer | None = None
        self.consumer: AIOKafkaConsumer | None = None
        self.cvi_manager: CVIManager | None = None
        self.state_machine: StateMachine | None = None
        self.zone_cache: ZoneCache | None = None
        self.ocr_consumer: OCRResultsConsumer | None = None
        self.ocr_requester: PlateOCRRequester | None = None
        self._evict_task: asyncio.Task | None = None

    async def startup(self) -> None:
        configure_logging(service_name=SERVICE_NAME)
        logger.info(
            "violation_service_starting",
            env=settings.ENV,
            brokers=settings.KAFKA_BOOTSTRAP_SERVERS,
        )
        await db.ping()
        await redis_client.ping()
        await minio_client.ping()

        redis = redis_client.get_redis()
        session_factory = db.get_session_factory()

        self.cvi_manager = CVIManager(redis=redis)
        self.zone_cache = ZoneCache(session_factory=session_factory)
        self.state_machine = StateMachine(
            writer=ViolationWriter(session_factory),
            registry=ActiveViolationRegistry(redis),
        )
        await self.zone_cache.refresh()

        self.consumer = kafka_client.make_consumer(
            ["detections"], group_id="violation-service"
        )
        await self.consumer.start()

        # Adım 7 — fold OCR results from plate-service back into CVIs.
        self.ocr_consumer = OCRResultsConsumer(
            cvi_manager=self.cvi_manager,
            shutdown_event=self.shutdown_event,
        )
        await self.ocr_consumer.start()

        # Adım 8 — drive snapshot→OCR for in-zone CVIs without a plate.
        self.ocr_requester = PlateOCRRequester(
            cvi_manager=self.cvi_manager,
            shutdown_event=self.shutdown_event,
        )
        await self.ocr_requester.start()

        self.health = HealthServer(
            host="0.0.0.0",  # noqa: S104 — LAN-only per spec §11
            port=9200,
            shutdown_event=self.shutdown_event,
        )
        await self.health.start()

        # Periodic CVI eviction (spec §4.4: drop idle CVIs every 60 s).
        self._evict_task = asyncio.create_task(
            self._evict_loop(), name="cvi-evict"
        )

        UP.set(1)

    async def _evict_loop(self, interval_s: int = 60) -> None:
        """
        Background task: evict idle CVIs every `interval_s` seconds.
        Spec §4.4 — CVIs not seen for >600 s are dropped from memory;
        their Redis keys expire independently via ACTIVE_CVI_TTL_SECONDS.
        """
        assert self.cvi_manager is not None
        from datetime import datetime, timezone  # noqa: PLC0415 — local import OK

        try:
            while not self.shutdown_event.is_set():
                await asyncio.sleep(interval_s)
                evicted = self.cvi_manager.evict_stale(datetime.now(tz=timezone.utc))
                if evicted:
                    CVI_EVICTIONS.inc(evicted)
                    logger.info("cvi_eviction_cycle", evicted=evicted)
                CVI_ACTIVE.set(self.cvi_manager.size())
        except asyncio.CancelledError:
            pass

    async def consume(self) -> None:
        assert self.consumer is not None
        assert self.cvi_manager is not None
        assert self.state_machine is not None
        assert self.zone_cache is not None

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
                        msg.value.decode()
                        if isinstance(msg.value, (bytes, bytearray))
                        else msg.value
                    )
                    DETECTIONS_CONSUMED.labels(camera=payload.sensor_id).inc(
                        len(payload.objects)
                    )
                    zones = await self.zone_cache.zones_for_camera(payload.sensor_id)
                    if not zones:
                        # No enabled zones on this camera → nothing to evaluate.
                        await self.consumer.commit()
                        continue
                    for obj in payload.objects:
                        obs = Observation.from_detection(payload, obj)
                        cvi, _match = await self.cvi_manager.resolve(obs)
                        await self.state_machine.evaluate(cvi, obs, zones)
                except ValidationError as exc:
                    MESSAGES_FAILED.labels(reason="schema").inc()
                    logger.warning("detection_schema_invalid", error=str(exc))
                except Exception as exc:  # noqa: BLE001
                    MESSAGES_FAILED.labels(reason="unknown").inc()
                    logger.exception("detection_handler_failed", error=str(exc))

                # Manual commit per spec §15.1 — only after handler returned.
                await self.consumer.commit()

                if self.shutdown_event.is_set():
                    break
        except KafkaError as exc:
            logger.exception("kafka_consumer_error", error=str(exc))

    async def shutdown(self) -> None:
        logger.info("violation_service_stopping")
        UP.set(0)
        if self._evict_task is not None:
            self._evict_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._evict_task
        if self.consumer is not None:
            with suppress(Exception):
                await self.consumer.stop()
        if self.ocr_requester is not None:
            await self.ocr_requester.stop()
        if self.ocr_consumer is not None:
            await self.ocr_consumer.stop()
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
        assert rt.ocr_consumer is not None
        consumer_task = asyncio.create_task(rt.consume())
        ocr_task = asyncio.create_task(rt.ocr_consumer.run())
        await rt.shutdown_event.wait()
        for t in (consumer_task, ocr_task):
            t.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.gather(consumer_task, ocr_task, return_exceptions=True)
    finally:
        await rt.shutdown()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
