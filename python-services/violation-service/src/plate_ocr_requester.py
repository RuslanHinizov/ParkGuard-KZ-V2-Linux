"""
python-services/violation-service/src/plate_ocr_requester.py

Adım 8 (spec §7.3 → §8.2). Periodically asks the DeepStream host to cut a
bbox-tight vehicle crop for every in-zone CVI that does not yet have a
stabilised plate, then — once the snapshot-consumer has durably uploaded
the JPEG to MinIO — fetches it and publishes an ``OCRRequest`` to the
plate-service.

Two cooperating tasks:

    poll_loop            periodically walks CVIManager._cvis and issues
                         SnapshotRequests (rate-limited per CVI).
    stored_consumer_loop subscribes to snapshot_stored, resolves the
                         pending request, reads the JPEG from MinIO,
                         base64-encodes it, and emits OCRRequest.

The requester does *not* modify the CVI; the vote back into CVI plate
state happens in ``ocr_results_consumer`` after plate-service replies.

Why split request issuance and upload-awaited OCR? Because the JPEG is
written to the shared volume by the C++ consumer first and only *then*
uploaded to MinIO — if we fed plate-service before the upload, we'd be
racing a 50-200 ms window where the MinIO key might not yet exist, and
the snapshot-consumer is the only process with the object at that
point (spec §15.8 exactly-once chain).
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from aiokafka.errors import KafkaError

from shared import kafka_client, minio_client
from shared.config import settings
from shared.logging import bind_context, get_logger, new_trace_id
from shared.schemas import CVIState

if TYPE_CHECKING:
    from aiokafka import AIOKafkaConsumer

    from src.cvi_manager import CVIManager

logger = get_logger(__name__)

TOPIC_SNAPSHOT_STORED = "snapshot_stored"
POLL_INTERVAL_SECONDS = 1.0


@dataclass(slots=True)
class _Pending:
    cvi_id: UUID
    camera_id: str
    attempt_no: int
    requested_at: float  # monotonic


class PlateOCRRequester:
    """Coordinate snapshot→OCR for CVIs that still need a plate read."""

    def __init__(
        self,
        *,
        cvi_manager: "CVIManager",
        shutdown_event: asyncio.Event,
        interval_seconds: float = POLL_INTERVAL_SECONDS,
        attempt_gap_seconds: int | None = None,
        max_attempts_per_cvi: int = 10,
    ) -> None:
        self._cvi = cvi_manager
        self._shutdown = shutdown_event
        self._interval = interval_seconds
        self._attempt_gap = timedelta(
            seconds=attempt_gap_seconds
            if attempt_gap_seconds is not None
            else settings.VIOLATION_OCR_ATTEMPT_INTERVAL_SECONDS
        )
        self._max_attempts = max_attempts_per_cvi

        self._last_attempt_at: dict[UUID, datetime] = {}
        self._attempts_count: dict[UUID, int] = {}
        self._pending: dict[str, _Pending] = {}
        self._pending_lock = asyncio.Lock()

        self._consumer: "AIOKafkaConsumer | None" = None
        self._poll_task: asyncio.Task[None] | None = None
        self._stored_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------ public
    async def start(self) -> None:
        self._consumer = kafka_client.make_consumer(
            [TOPIC_SNAPSHOT_STORED],
            group_id=f"{settings.KAFKA_CONSUMER_GROUP_VIOLATION}-snapshot",
            auto_offset_reset="latest",
        )
        await self._consumer.start()
        self._poll_task   = asyncio.create_task(self._poll_loop(), name="plate-ocr-poll")
        self._stored_task = asyncio.create_task(self._stored_loop(), name="plate-ocr-stored")
        logger.info("plate_ocr_requester_started",
                    interval_s=self._interval,
                    attempt_gap_s=int(self._attempt_gap.total_seconds()))

    async def stop(self) -> None:
        for t in (self._poll_task, self._stored_task):
            if t is not None:
                t.cancel()
                with suppress(asyncio.CancelledError):
                    await t
        if self._consumer is not None:
            with suppress(Exception):
                await self._consumer.stop()
            self._consumer = None

    # ------------------------------------------------------------------ tasks
    async def _poll_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                await self._issue_pending_requests()
            except Exception as exc:  # noqa: BLE001
                logger.exception("plate_ocr_poll_failed", error=str(exc))
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

    async def _stored_loop(self) -> None:
        assert self._consumer is not None
        try:
            async for msg in self._consumer:
                if self._shutdown.is_set():
                    break
                trace_id = new_trace_id()
                bind_context(trace_id=trace_id, topic=msg.topic,
                             partition=msg.partition, offset=msg.offset)
                try:
                    raw = msg.value.decode() if isinstance(msg.value, (bytes, bytearray)) else msg.value
                    envelope = json.loads(raw)
                    await self._handle_snapshot_stored(envelope, trace_id=trace_id)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    logger.warning("snapshot_stored_bad_json", error=str(exc))
                except Exception as exc:  # noqa: BLE001
                    logger.exception("snapshot_stored_handler_failed", error=str(exc))
                finally:
                    with suppress(Exception):
                        await self._consumer.commit()
        except KafkaError as exc:
            logger.exception("plate_ocr_stored_kafka_error", error=str(exc))

    # -------------------------------------------------------------- internals
    async def _issue_pending_requests(self) -> None:
        now = datetime.now(tz=timezone.utc)
        candidates = self._select_candidates(now)
        for cvi in candidates:
            request_id = uuid4()
            bbox = cvi.last_bbox
            if bbox is None:
                continue
            attempt_no = self._attempts_count.get(cvi.cvi_id, 0) + 1

            payload = {
                "request_id": str(request_id),
                "camera_id":  cvi.camera_id,
                "frame_id":   0,          # latest available is fine for OCR
                "reason":     "ocr",
                "type":       "vehicle_crop",
                "bbox": {
                    "x": int(bbox.x),
                    "y": int(bbox.y),
                    "w": int(bbox.w),
                    "h": int(bbox.h),
                },
            }
            try:
                await kafka_client.send_json(
                    settings.KAFKA_TOPIC_SNAPSHOT_REQ,
                    payload,
                    key=str(cvi.cvi_id),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("snapshot_request_publish_failed",
                                cvi_id=str(cvi.cvi_id), error=str(exc))
                continue

            async with self._pending_lock:
                self._pending[str(request_id)] = _Pending(
                    cvi_id=cvi.cvi_id,
                    camera_id=cvi.camera_id,
                    attempt_no=attempt_no,
                    requested_at=time.monotonic(),
                )
            self._last_attempt_at[cvi.cvi_id] = now
            self._attempts_count[cvi.cvi_id] = attempt_no
            logger.debug("snapshot_request_issued",
                         request_id=str(request_id),
                         cvi_id=str(cvi.cvi_id),
                         attempt=attempt_no)

    def _select_candidates(self, now: datetime) -> list:
        out = []
        # Access manager internals — CVIManager doesn't expose iteration,
        # and we don't want to copy the whole dict every tick.
        cvis = getattr(self._cvi, "_cvis", {}).values()
        for cvi in cvis:
            if cvi.plate_text:
                continue  # plate already stabilised → no more OCR
            if cvi.last_bbox is None:
                continue
            if not any(
                e.state in (CVIState.INSIDE_ZONE, CVIState.VIOLATED)
                for e in cvi.state_per_zone.values()
            ):
                continue
            if self._attempts_count.get(cvi.cvi_id, 0) >= self._max_attempts:
                continue
            last = self._last_attempt_at.get(cvi.cvi_id)
            if last is not None and now - last < self._attempt_gap:
                continue
            out.append(cvi)
        return out

    async def _handle_snapshot_stored(
        self,
        envelope: dict,
        *,
        trace_id: str,
    ) -> None:
        request_id = str(envelope.get("request_id") or "")
        status = str(envelope.get("status") or "")
        minio_key = envelope.get("minio_key")

        async with self._pending_lock:
            pending = self._pending.pop(request_id, None)
        if pending is None:
            # Not ours — could be a request issued by another service.
            return

        if status != "ok" or not minio_key:
            logger.info("snapshot_stored_non_ok",
                        request_id=request_id, status=status)
            return

        bucket = envelope.get("bucket") or settings.MINIO_BUCKET_SNAPSHOTS
        try:
            data = await minio_client.get_object(bucket, minio_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("snapshot_fetch_failed",
                           key=minio_key, error=str(exc))
            return

        ocr_payload = {
            "request_id":       str(uuid4()),
            "cvi_id":           str(pending.cvi_id),
            "camera_id":        pending.camera_id,
            "attempt_no":       pending.attempt_no,
            "vehicle_crop_b64": base64.b64encode(data).decode("ascii"),
        }
        try:
            await kafka_client.send_json(
                settings.KAFKA_TOPIC_OCR_REQ,
                ocr_payload,
                key=str(pending.cvi_id),
                headers={"trace_id": trace_id},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("ocr_request_publish_failed",
                            cvi_id=str(pending.cvi_id), error=str(exc))
            return

        logger.info("ocr_request_emitted",
                    cvi_id=str(pending.cvi_id),
                    attempt=pending.attempt_no,
                    bytes=len(data))


__all__ = ["PlateOCRRequester"]
