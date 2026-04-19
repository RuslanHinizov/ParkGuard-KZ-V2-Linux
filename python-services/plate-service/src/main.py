"""
python-services/plate-service/src/main.py

Plate-service entry. Consumes ``ocr_requests``, feeds each request
through :class:`PlateProcessor` (Nomeroff-net 4.0.1), and publishes
an ``OCRResult`` onto ``ocr_results``. Every resulting message
inherits the originating trace id via Kafka headers so a single
violation can be grep'd across violation-service, plate-service, and
penalty-card-service logs.

Process graph::

    ocr_requests (Kafka)
        → OCRRequest
            → PlateProcessor.handle  (Nomeroff + KZ post-process)
                → OCRResult
                    → send_json("ocr_results", …, key=cvi_id)

Shutdown is cooperative: SIGTERM / SIGINT set `shutdown_event`, the
consumer drains its current poll batch, then the health server and
Kafka producer are closed.
"""

from __future__ import annotations

import asyncio
import signal
import time
from contextlib import suppress
from typing import TYPE_CHECKING

from aiokafka.errors import KafkaError
from pydantic import ValidationError

from shared import kafka_client
from shared.config import settings
from shared.logging import bind_context, configure_logging, get_logger, new_trace_id
from shared.schemas import OCRRequest

from src.health import HealthServer
from src.metrics import OCR_LATENCY, OCR_REQUESTS, UP
from src.model_warmup import warmup
from src.nomeroff_wrapper import NomeroffAdapter, PlatePipeline
from src.processor import PlateProcessor, ProcessorConfig

if TYPE_CHECKING:
    from aiokafka import AIOKafkaConsumer

logger = get_logger(__name__)

SERVICE_NAME = "plate-service"


class Runtime:
    """Own every long-lived resource so shutdown is one call."""

    def __init__(self, pipeline_factory: "callable[[], PlatePipeline] | None" = None) -> None:  # type: ignore[name-defined]
        self.shutdown_event: asyncio.Event = asyncio.Event()
        self.health: HealthServer | None = None
        self.consumer: AIOKafkaConsumer | None = None
        self.processor: PlateProcessor | None = None
        self._pipeline: PlatePipeline | None = None
        self._pipeline_factory = pipeline_factory or (
            lambda: NomeroffAdapter(device=settings.PLATE_SERVICE_DEVICE)
        )
        self._ready = False

    def is_ready(self) -> bool:
        return self._ready

    async def startup(self) -> None:
        configure_logging(service_name=SERVICE_NAME)
        logger.info(
            "plate_service_starting",
            env=settings.ENV,
            brokers=settings.KAFKA_BOOTSTRAP_SERVERS,
            device=settings.PLATE_SERVICE_DEVICE,
        )

        # Build pipeline and warm it up *before* we accept traffic so a
        # cold-start request never sees the 3-6s first-forward stall.
        self._pipeline = self._pipeline_factory()
        if isinstance(self._pipeline, NomeroffAdapter):
            await self._pipeline.ensure_ready()
        try:
            await warmup(self._pipeline, frames=settings.PLATE_SERVICE_WARMUP_IMAGES)
        except Exception as exc:  # noqa: BLE001
            logger.warning("nomeroff_warmup_failed", error=str(exc))

        self.processor = PlateProcessor(
            pipeline=self._pipeline,
            config=ProcessorConfig(
                min_confidence=settings.PLATE_SERVICE_CONFIDENCE_THRESHOLD,
                accepted_regions=frozenset(settings.accepted_plate_regions),
            ),
        )

        self.consumer = kafka_client.make_consumer(
            [settings.KAFKA_TOPIC_OCR_REQ],
            group_id=settings.KAFKA_CONSUMER_GROUP_PLATE,
        )
        await self.consumer.start()

        self.health = HealthServer(
            host="0.0.0.0",  # noqa: S104 — LAN-only per spec §11
            port=9300,
            shutdown_event=self.shutdown_event,
            is_ready=self.is_ready,
        )
        await self.health.start()
        self._ready = True
        UP.set(1)

    async def consume(self) -> None:
        assert self.consumer is not None
        assert self.processor is not None

        try:
            async for msg in self.consumer:
                trace_id = _extract_trace(msg.headers) or new_trace_id()
                bind_context(
                    trace_id=trace_id,
                    topic=msg.topic,
                    partition=msg.partition,
                    offset=msg.offset,
                )
                started = time.perf_counter()
                try:
                    req = OCRRequest.model_validate_json(
                        msg.value.decode()
                        if isinstance(msg.value, (bytes, bytearray))
                        else msg.value
                    )
                    OCR_REQUESTS.labels(camera=req.camera_id).inc()
                    result = await self.processor.handle(req)

                    await kafka_client.send_json(
                        settings.KAFKA_TOPIC_OCR_RES,
                        result.model_dump(mode="json"),
                        key=str(req.cvi_id),
                        headers={"trace_id": trace_id},
                    )
                    logger.info(
                        "ocr_result_produced",
                        cvi_id=str(req.cvi_id),
                        plate=result.plate_text,
                        fmt=result.format_type,
                        conf=result.plate_confidence,
                        valid=result.valid_format,
                    )
                except ValidationError as exc:
                    logger.warning("ocr_request_schema_invalid", error=str(exc))
                except Exception as exc:  # noqa: BLE001
                    logger.exception("ocr_handler_failed", error=str(exc))
                finally:
                    OCR_LATENCY.observe(time.perf_counter() - started)
                    # Manual commit per spec §15.1 — only after we've
                    # either produced a result or logged the failure.
                    with suppress(Exception):
                        await self.consumer.commit()

                if self.shutdown_event.is_set():
                    break
        except KafkaError as exc:
            logger.exception("kafka_consumer_error", error=str(exc))

    async def shutdown(self) -> None:
        logger.info("plate_service_stopping")
        UP.set(0)
        self._ready = False
        if self.consumer is not None:
            with suppress(Exception):
                await self.consumer.stop()
        if self.health is not None:
            await self.health.stop()
        await kafka_client.close_producer()


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
