"""
python-services/snapshot-consumer/src/main.py

Entry for the snapshot-consumer service. The C++ DeepStream host writes a
JPEG file per answered snapshot request into the shared volume, then sends
a message on ``snapshot_responses`` describing the file's path. This
service drains that topic, uploads the JPEG to MinIO, removes the local
file, and (optionally) re-emits a follow-up ``snapshot_stored`` envelope
so consumers waiting for durability — primarily the violation-service's
OCR requester — can proceed.

Process lifecycle:

    1. configure logging + start health server
    2. start Kafka consumer (manual commit, spec §15.1)
    3. for each message: decode, upload via :class:`SnapshotUploader`,
       produce follow-up, commit offset
    4. on SIGTERM: drain current poll, close Kafka, stop health server.
"""

from __future__ import annotations

import asyncio
import json
import signal
import time
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from aiokafka.errors import KafkaError

from shared import kafka_client
from shared.config import settings
from shared.logging import bind_context, configure_logging, get_logger, new_trace_id

from src.health import HealthServer
from src.metrics import UP
from src.uploader import SnapshotUploader, UploaderConfig

if TYPE_CHECKING:
    from aiokafka import AIOKafkaConsumer

logger = get_logger(__name__)

SERVICE_NAME = "snapshot-consumer"
HEALTH_PORT  = 9400

# Follow-up topic — consumers of "I uploaded the snapshot" events; mainly
# the violation-service plate-OCR requester (Adım 8) waits for this before
# asking plate-service to read the plate.
TOPIC_STORED = "snapshot_stored"


class Runtime:
    def __init__(self, uploader: SnapshotUploader | None = None) -> None:
        self.shutdown_event: asyncio.Event = asyncio.Event()
        self.health: HealthServer | None = None
        self.consumer: "AIOKafkaConsumer | None" = None
        self.uploader: SnapshotUploader | None = uploader
        self._ready = False

    def is_ready(self) -> bool:
        return self._ready

    async def startup(self) -> None:
        configure_logging(service_name=SERVICE_NAME)
        logger.info(
            "snapshot_consumer_starting",
            env=settings.ENV,
            brokers=settings.KAFKA_BOOTSTRAP_SERVERS,
            bucket=settings.MINIO_BUCKET_SNAPSHOTS,
        )

        if self.uploader is None:
            self.uploader = SnapshotUploader(
                UploaderConfig(bucket=settings.MINIO_BUCKET_SNAPSHOTS),
            )

        self.consumer = kafka_client.make_consumer(
            [settings.KAFKA_TOPIC_SNAPSHOT_RES],
            group_id=settings.KAFKA_CONSUMER_GROUP_SNAPSHOT,
        )
        await self.consumer.start()

        self.health = HealthServer(
            host="0.0.0.0",  # noqa: S104 — LAN-only per spec §11
            port=HEALTH_PORT,
            shutdown_event=self.shutdown_event,
            is_ready=self.is_ready,
        )
        await self.health.start()

        # Pre-warm shared Kafka producer so the first follow-up publish
        # doesn't eat a broker metadata fetch on the hot path.
        await kafka_client.get_producer()

        self._ready = True
        UP.set(1)

    async def consume(self) -> None:
        assert self.consumer is not None
        assert self.uploader is not None
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
                envelope: dict[str, Any] = {}
                try:
                    raw = msg.value.decode() if isinstance(msg.value, (bytes, bytearray)) else msg.value
                    envelope = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    logger.warning("snapshot_response_bad_json", error=str(exc))
                else:
                    try:
                        outcome = await self.uploader.handle(envelope)
                        await _publish_stored(outcome, bucket=settings.MINIO_BUCKET_SNAPSHOTS,
                                               trace_id=trace_id)
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("snapshot_handle_failed", error=str(exc))
                finally:
                    with suppress(Exception):
                        await self.consumer.commit()
                    logger.debug("snapshot_msg_handled",
                                 elapsed_ms=round((time.perf_counter() - started) * 1000, 2))

                if self.shutdown_event.is_set():
                    break
        except KafkaError as exc:
            logger.exception("kafka_consumer_error", error=str(exc))

    async def shutdown(self) -> None:
        logger.info("snapshot_consumer_stopping")
        UP.set(0)
        self._ready = False
        if self.consumer is not None:
            with suppress(Exception):
                await self.consumer.stop()
        if self.health is not None:
            await self.health.stop()
        await kafka_client.close_producer()


async def _publish_stored(
    outcome: Any,
    *,
    bucket: str,
    trace_id: str,
) -> None:
    """Emit a follow-up envelope regardless of upload success — downstream
    consumers key on ``status`` to decide what to do next."""
    try:
        envelope = outcome.to_envelope(bucket=bucket)
        key = outcome.request_id or None
        await kafka_client.send_json(
            TOPIC_STORED,
            envelope,
            key=key,
            headers={"trace_id": trace_id},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("snapshot_stored_publish_failed", error=str(exc))


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
