"""
python-services/violation-service/src/ocr_results_consumer.py

Background consumer that subscribes to ``ocr_results`` and folds each
:class:`OCRResult` back into the CVI's plate_votes counter. Runs on
the same event loop as the detections consumer but on its own Kafka
group so back-pressure on one stream doesn't stall the other.

Why a separate consumer (vs. inline in the detections loop)?

  * Detections are lossy — we always reprocess on rebalance. OCR
    results, on the other hand, *must* be idempotent on replay: the
    `record_plate_vote` path already handles that because CVI votes
    are Counter-based and a repeat of the same plate only inflates
    the count, not the `plate_text` resolution logic.
  * A plate-service stall (GPU OOM, model reload) must not block
    violation-service's ingest of detections. Separate loops give us
    independent Kafka offsets so either can recover on its own.

Spec alignment: §8 (violation-service responsibilities), §9.2 (OCR
topology), §15.1 (manual commit after handler success).
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

from aiokafka.errors import KafkaError
from pydantic import ValidationError

from shared import kafka_client
from shared.config import settings
from shared.logging import bind_context, get_logger, new_trace_id
from shared.schemas import OCRResult

from src.metrics import MESSAGES_FAILED, OCR_RESULTS_APPLIED

if TYPE_CHECKING:
    from aiokafka import AIOKafkaConsumer

    from src.cvi_manager import CVIManager

logger = get_logger(__name__)


class OCRResultsConsumer:
    """Consume `ocr_results`, feed each into CVIManager.apply_ocr_result."""

    def __init__(self, cvi_manager: CVIManager, shutdown_event: asyncio.Event) -> None:
        self._cvi = cvi_manager
        self._shutdown = shutdown_event
        self._consumer: AIOKafkaConsumer | None = None

    async def start(self) -> None:
        self._consumer = kafka_client.make_consumer(
            [settings.KAFKA_TOPIC_OCR_RES],
            group_id=f"{settings.KAFKA_CONSUMER_GROUP_VIOLATION}-ocr",
        )
        await self._consumer.start()
        logger.info("ocr_results_consumer_started",
                    topic=settings.KAFKA_TOPIC_OCR_RES)

    async def stop(self) -> None:
        if self._consumer is not None:
            with suppress(Exception):
                await self._consumer.stop()
            self._consumer = None

    async def run(self) -> None:
        assert self._consumer is not None

        try:
            async for msg in self._consumer:
                trace_id = _extract_trace(msg.headers) or new_trace_id()
                bind_context(
                    trace_id=trace_id,
                    topic=msg.topic,
                    partition=msg.partition,
                    offset=msg.offset,
                )
                try:
                    result = OCRResult.model_validate_json(
                        msg.value.decode()
                        if isinstance(msg.value, (bytes, bytearray))
                        else msg.value
                    )
                    stabilised = await self._cvi.apply_ocr_result(
                        cvi_id=result.cvi_id,
                        plate_text=result.plate_text,
                        plate_confidence=result.plate_confidence,
                    )
                    if result.plate_text is None:
                        OCR_RESULTS_APPLIED.labels(outcome="invalid").inc()
                    elif stabilised:
                        OCR_RESULTS_APPLIED.labels(outcome="stabilised").inc()
                    else:
                        OCR_RESULTS_APPLIED.labels(outcome="vote_only").inc()
                except ValidationError as exc:
                    MESSAGES_FAILED.labels(reason="ocr_schema").inc()
                    logger.warning("ocr_result_schema_invalid", error=str(exc))
                except Exception as exc:  # noqa: BLE001
                    MESSAGES_FAILED.labels(reason="ocr_unknown").inc()
                    logger.exception("ocr_result_handler_failed", error=str(exc))

                with suppress(Exception):
                    await self._consumer.commit()

                if self._shutdown.is_set():
                    break
        except KafkaError as exc:
            logger.exception("ocr_results_consumer_error", error=str(exc))


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


__all__ = ["OCRResultsConsumer"]
