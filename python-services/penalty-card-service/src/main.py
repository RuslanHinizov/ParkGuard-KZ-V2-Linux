"""
python-services/penalty-card-service/src/main.py

Adım 9 (spec §11). Consumes ``penalty_card_requests``, produces a
bilingual (KZ/RU/EN) A4 PDF with an embedded QR code via WeasyPrint,
uploads the PDF to MinIO, updates ``violations.penalty_card_url`` in
Postgres, and publishes a ``penalty_card_issued`` follow-up event.

Process graph::

    penalty_card_requests (Kafka)
        → PenaltyCardRequest { violation_id }
            → fetch_violation (Postgres)
                → optional: fetch snapshot JPEG from MinIO
                    → PenaltyCardRenderer.render_pdf_async
                        → MinIO upload (penalty-cards/<cam>/<date>/<id>.pdf)
                            → write_card_url (Postgres)
                                → send_json("penalty_card_issued", …)
                                    → commit Kafka offset

One Kafka partition per host is expected (spec scale note §14). The
renderer is instantiated once at startup because Jinja template loading
and font discovery are not free.
"""

from __future__ import annotations

import asyncio
import base64
import signal
import time
from contextlib import suppress
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from aiokafka.errors import KafkaError
from pydantic import ValidationError

from shared import db, kafka_client, minio_client
from shared.config import settings
from shared.logging import bind_context, configure_logging, get_logger, new_trace_id
from shared.schemas import PenaltyCardRequest

from src.card_utils import pdf_key as _pdf_key, url_to_minio_key as _url_to_minio_key
from src.db_queries import fetch_violation, write_card_url
from src.health import HealthServer
from src.metrics import CARD_FAILURES, CARDS_GENERATED, PDF_BYTES, RENDER_LATENCY, UP
from src.renderer import CardData, PenaltyCardRenderer

if TYPE_CHECKING:
    from aiokafka import AIOKafkaConsumer

logger = get_logger(__name__)

SERVICE_NAME  = "penalty-card-service"
HEALTH_PORT   = 9500
TOPIC_ISSUED  = "penalty_card_issued"


class Runtime:
    def __init__(self, renderer: PenaltyCardRenderer | None = None) -> None:
        self.shutdown_event: asyncio.Event = asyncio.Event()
        self.health: HealthServer | None = None
        self.consumer: "AIOKafkaConsumer | None" = None
        self._renderer = renderer
        self._session_factory = None
        self._ready = False

    def is_ready(self) -> bool:
        return self._ready

    async def startup(self) -> None:
        configure_logging(service_name=SERVICE_NAME)
        logger.info(
            "penalty_card_service_starting",
            env=settings.ENV,
            brokers=settings.KAFKA_BOOTSTRAP_SERVERS,
            bucket=settings.MINIO_BUCKET_CARDS,
        )

        await db.ping()
        await minio_client.ping()
        self._session_factory = db.get_session_factory()

        if self._renderer is None:
            self._renderer = PenaltyCardRenderer()

        self.consumer = kafka_client.make_consumer(
            [settings.KAFKA_TOPIC_PENALTY_REQ],
            group_id=settings.KAFKA_CONSUMER_GROUP_PENALTY,
        )
        await self.consumer.start()

        # Pre-create the MinIO bucket idempotently.
        await minio_client.ensure_bucket(settings.MINIO_BUCKET_CARDS)

        self.health = HealthServer(
            host="0.0.0.0",  # noqa: S104 — LAN-only per spec §11
            port=HEALTH_PORT,
            shutdown_event=self.shutdown_event,
            is_ready=self.is_ready,
        )
        await self.health.start()
        await kafka_client.get_producer()
        self._ready = True
        UP.set(1)

    async def consume(self) -> None:
        assert self.consumer is not None
        assert self._renderer is not None
        assert self._session_factory is not None

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
                    req = PenaltyCardRequest.model_validate_json(
                        msg.value.decode()
                        if isinstance(msg.value, (bytes, bytearray))
                        else msg.value
                    )
                    await self._handle(req, trace_id=trace_id)
                except ValidationError as exc:
                    CARD_FAILURES.labels(reason="schema").inc()
                    logger.warning("penalty_card_schema_invalid", error=str(exc))
                except Exception as exc:  # noqa: BLE001
                    CARD_FAILURES.labels(reason="handler").inc()
                    logger.exception("penalty_card_handler_failed", error=str(exc))
                finally:
                    RENDER_LATENCY.observe(time.perf_counter() - started)
                    with suppress(Exception):
                        await self.consumer.commit()
                if self.shutdown_event.is_set():
                    break
        except KafkaError as exc:
            logger.exception("kafka_consumer_error", error=str(exc))

    async def _handle(self, req: PenaltyCardRequest, *, trace_id: str) -> None:
        assert self._renderer is not None
        assert self._session_factory is not None

        row = await fetch_violation(self._session_factory, req.violation_id)
        if row is None:
            CARD_FAILURES.labels(reason="violation_missing").inc()
            logger.warning("penalty_card_violation_missing", violation_id=req.violation_id)
            return

        # Skip if a card was already issued (idempotency guard).
        if row.penalty_card_url:
            logger.info("penalty_card_already_issued",
                        violation_id=req.violation_id,
                        url=row.penalty_card_url)
            return

        # Optionally fetch vehicle snapshot from MinIO for embedding in card.
        snapshot_b64: str | None = None
        if row.snapshot_vehicle_url:
            try:
                key = _url_to_minio_key(row.snapshot_vehicle_url)
                data = await minio_client.get_object(settings.MINIO_BUCKET_SNAPSHOTS, key)
                snapshot_b64 = base64.b64encode(data).decode("ascii")
            except Exception as exc:  # noqa: BLE001
                logger.debug("snapshot_fetch_skipped", error=str(exc))

        card = CardData(
            violation_id=row.id,
            camera_id=row.camera_id,
            zone_id=row.zone_id,
            zone_name=row.zone_name,
            violation_time=row.violation_time,
            duration_seconds=row.duration_seconds,
            plate_text=row.plate_text,
            plate_format=row.plate_format,
            plate_region_code=row.plate_region_code,
            is_diplomatic=row.is_diplomatic,
            vehicle_class=row.vehicle_class,
            snapshot_b64=snapshot_b64,
        )

        pdf_bytes = await self._renderer.render_pdf_async(card)
        PDF_BYTES.observe(len(pdf_bytes))

        key = _pdf_key(row.camera_id, row.violation_time, req.violation_id)
        minio_key = await minio_client.put_object(
            settings.MINIO_BUCKET_CARDS,
            key,
            pdf_bytes,
            content_type="application/pdf",
        )

        # Build a stable LAN URL the frontend can serve to operators.
        card_url = f"http://{settings.MINIO_ENDPOINT}/{settings.MINIO_BUCKET_CARDS}/{minio_key}"

        await write_card_url(self._session_factory, req.violation_id, card_url)

        await kafka_client.send_json(
            TOPIC_ISSUED,
            {
                "violation_id":   req.violation_id,
                "camera_id":      row.camera_id,
                "penalty_card_url": card_url,
                "bytes":          len(pdf_bytes),
                "timestamp":      datetime.now(tz=timezone.utc).isoformat(),
            },
            key=str(req.violation_id),
            headers={"trace_id": trace_id},
        )

        CARDS_GENERATED.labels(camera=row.camera_id).inc()
        logger.info(
            "penalty_card_issued",
            violation_id=req.violation_id,
            camera=row.camera_id,
            minio_key=minio_key,
            pdf_bytes=len(pdf_bytes),
        )

    async def shutdown(self) -> None:
        logger.info("penalty_card_service_stopping")
        UP.set(0)
        self._ready = False
        if self.consumer is not None:
            with suppress(Exception):
                await self.consumer.stop()
        if self.health is not None:
            await self.health.stop()
        await kafka_client.close_producer()
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
        except NotImplementedError:
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
