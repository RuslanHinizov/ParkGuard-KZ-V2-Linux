"""
python-services/snapshot-consumer/src/uploader.py

Takes a parsed snapshot_responses envelope, reads the local JPEG produced by
the C++ DeepStream host, uploads it to MinIO under a date-partitioned key,
optionally deletes the local file, and publishes an enriched envelope to the
``snapshot_stored`` follow-up topic (consumed by violation-service when an
OCR attempt was gated on the upload being durable).

The class is intentionally free of Kafka I/O — the main loop owns the
consumer/producer handles and only calls :meth:`handle`. This keeps the
uploader trivially unit-testable against a local ``tmp_path`` + mocked
MinIO client.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared import minio_client
from shared.logging import get_logger

from src.metrics import (
    MISSING_FILES_TOTAL,
    STATUS_FILTERED_TOTAL,
    UPLOAD_BYTES,
    UPLOAD_FAILURES_TOTAL,
    UPLOAD_LATENCY,
    UPLOADS_TOTAL,
)

logger = get_logger(__name__)


@dataclass(slots=True)
class UploadOutcome:
    """Result returned by :meth:`SnapshotUploader.handle`."""

    ok: bool
    reason: str                 # "ok" | "miss" | "bad_status:<…>" | "no_file" | "upload_error"
    request_id: str
    camera_id: str
    minio_key: str | None = None
    bytes_uploaded: int = 0

    def to_envelope(self, *, bucket: str) -> dict[str, Any]:
        """Produce the `snapshot_stored` follow-up message."""
        return {
            "request_id": self.request_id,
            "camera_id":  self.camera_id,
            "status":     "ok" if self.ok else self.reason,
            "bucket":     bucket,
            "minio_key":  self.minio_key,
            "bytes":      self.bytes_uploaded,
            "timestamp":  datetime.now(tz=timezone.utc).isoformat(),
        }


@dataclass(slots=True)
class UploaderConfig:
    bucket: str
    delete_local_after_upload: bool = True
    # The C++ consumer writes JPEGs into a shared volume; validate the input
    # path is inside this prefix to refuse e.g. ``/etc/passwd`` if a bad
    # actor ever reaches the topic (spec §15.7 defense-in-depth).
    allowed_root: str = "/shared/snapshots"


class SnapshotUploader:
    """Upload one snapshot_responses envelope to MinIO."""

    def __init__(
        self,
        cfg: UploaderConfig,
        *,
        put_object: Any = None,       # override for tests
    ) -> None:
        self._cfg = cfg
        self._put = put_object or minio_client.put_object

    async def handle(self, envelope: dict[str, Any]) -> UploadOutcome:
        started = time.perf_counter()
        request_id = str(envelope.get("request_id") or "").strip()
        camera_id  = str(envelope.get("camera_id")  or "").strip()
        status     = str(envelope.get("status")     or "ok")
        path_raw   = envelope.get("path")

        if not request_id or not camera_id:
            return UploadOutcome(False, "bad_envelope", request_id, camera_id)

        # The DeepStream side sends a terminal status for every request —
        # "miss" / "decode_error" / "write_error" carry no path. Surface them
        # back unchanged so violation-service knows the request is final.
        if status != "ok":
            STATUS_FILTERED_TOTAL.labels(status=status).inc()
            return UploadOutcome(False, f"bad_status:{status}", request_id, camera_id)

        path_str = str(path_raw or "").strip()
        if not path_str:
            return UploadOutcome(False, "no_file", request_id, camera_id)

        # Reject paths that escape the shared volume — directory traversal
        # here would upload arbitrary host files to MinIO.
        resolved = Path(path_str).resolve()
        allowed_root = Path(self._cfg.allowed_root).resolve()
        try:
            resolved.relative_to(allowed_root)
        except ValueError:
            logger.warning(
                "snapshot_path_outside_allowed_root",
                path=str(resolved),
                allowed_root=str(allowed_root),
            )
            UPLOAD_FAILURES_TOTAL.labels(reason="path_traversal").inc()
            return UploadOutcome(False, "path_traversal", request_id, camera_id)

        if not resolved.is_file():
            MISSING_FILES_TOTAL.inc()
            return UploadOutcome(False, "no_file", request_id, camera_id)

        try:
            data = await asyncio.to_thread(resolved.read_bytes)
        except OSError as exc:
            logger.warning("snapshot_read_failed", path=str(resolved), error=str(exc))
            UPLOAD_FAILURES_TOTAL.labels(reason="read_error").inc()
            return UploadOutcome(False, "read_error", request_id, camera_id)

        key = _build_key(camera_id, request_id, envelope.get("timestamp"))
        try:
            await self._put(
                self._cfg.bucket,
                key,
                data,
                content_type="image/jpeg",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("minio_upload_failed", key=key, error=str(exc))
            UPLOAD_FAILURES_TOTAL.labels(reason="minio").inc()
            return UploadOutcome(False, "upload_error", request_id, camera_id)

        if self._cfg.delete_local_after_upload:
            try:
                await asyncio.to_thread(os.unlink, resolved)
            except OSError as exc:
                # Non-fatal — cleanup is best-effort; a janitor cron can
                # sweep /shared/snapshots on the host.
                logger.debug("snapshot_unlink_failed", path=str(resolved), error=str(exc))

        elapsed = time.perf_counter() - started
        UPLOAD_LATENCY.observe(elapsed)
        UPLOAD_BYTES.observe(len(data))
        UPLOADS_TOTAL.labels(camera=camera_id).inc()
        logger.info(
            "snapshot_uploaded",
            request_id=request_id,
            camera_id=camera_id,
            minio_key=key,
            bytes=len(data),
            elapsed_ms=round(elapsed * 1000, 2),
        )
        return UploadOutcome(
            True, "ok", request_id, camera_id,
            minio_key=key, bytes_uploaded=len(data),
        )


def _build_key(camera_id: str, request_id: str, ts_raw: Any) -> str:
    """
    MinIO layout:  <camera_id>/<YYYY>/<MM>/<DD>/<request_id>.jpg

    The timestamp prefix is derived from the envelope's ``timestamp`` (the
    frame's wall-clock time, not now) so an operator can bisect by date
    without joining Postgres. Falls back to ``now()`` if unparseable.
    """
    ts = _parse_ts(ts_raw) or datetime.now(tz=timezone.utc)
    safe_cam = _sanitize(camera_id)
    return (
        f"{safe_cam}/"
        f"{ts.year:04d}/{ts.month:02d}/{ts.day:02d}/"
        f"{request_id}.jpg"
    )


def _parse_ts(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, str) and raw:
        s = raw.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            return None
    return None


def _sanitize(s: str) -> str:
    """Restrict key segment to safe chars; MinIO accepts more, but we care
    about predictability when operators grep logs."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s) or "unknown"


__all__ = ["SnapshotUploader", "UploaderConfig", "UploadOutcome"]
