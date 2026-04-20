"""
python-services/snapshot-consumer/tests/test_uploader.py

Exercises the uploader in isolation: real filesystem (pytest tmp_path),
mocked MinIO put. Covers:

    - happy path (status ok → file uploaded, local deleted, outcome.ok)
    - miss status (no path → early return, no upload)
    - path traversal (outside allowed_root → refuse, no upload)
    - missing file (valid path, file absent → no_file outcome)
    - key layout (camera_id + YYYY/MM/DD + request_id.jpg)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.uploader import SnapshotUploader, UploaderConfig


class _PutRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        bucket: str,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> str:
        self.calls.append(
            {"bucket": bucket, "key": key, "bytes": len(data), "content_type": content_type}
        )
        return key


@pytest.fixture
def uploader_factory(tmp_path: Path):
    def _build(*, delete_local: bool = True) -> tuple[SnapshotUploader, _PutRecorder, Path]:
        allowed_root = tmp_path / "snapshots"
        allowed_root.mkdir(parents=True, exist_ok=True)
        put = _PutRecorder()
        u = SnapshotUploader(
            UploaderConfig(
                bucket="snapshots",
                delete_local_after_upload=delete_local,
                allowed_root=str(allowed_root),
            ),
            put_object=put,
        )
        return u, put, allowed_root

    return _build


# --------------------------------------------------------------------------- #
# happy path
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_happy_path_uploads_and_deletes(uploader_factory) -> None:
    u, put, root = uploader_factory(delete_local=True)
    jpeg_path = root / "abc123.jpg"
    jpeg_path.write_bytes(b"\xff\xd8\xff\xe0" + b"payload-bytes" * 100)

    envelope = {
        "request_id": "00000000-0000-0000-0000-000000000001",
        "camera_id":  "cam_01",
        "status":     "ok",
        "timestamp":  "2026-04-17T10:23:45.123Z",
        "path":       str(jpeg_path),
        "width":      1920,
        "height":     1080,
    }

    outcome = await u.handle(envelope)
    assert outcome.ok is True
    assert outcome.reason == "ok"
    assert outcome.bytes_uploaded > 0
    assert outcome.minio_key is not None
    assert outcome.minio_key.startswith("cam_01/2026/04/17/")
    assert outcome.minio_key.endswith(".jpg")

    assert len(put.calls) == 1
    assert put.calls[0]["bucket"]       == "snapshots"
    assert put.calls[0]["content_type"] == "image/jpeg"

    assert not jpeg_path.exists(), "local file should have been removed"


@pytest.mark.asyncio
async def test_retain_local_when_configured(uploader_factory) -> None:
    u, _put, root = uploader_factory(delete_local=False)
    jpeg_path = root / "keep.jpg"
    jpeg_path.write_bytes(b"\xff\xd8\xff\xe0payload")

    outcome = await u.handle({
        "request_id": "req-keep",
        "camera_id":  "cam_01",
        "status":     "ok",
        "path":       str(jpeg_path),
    })
    assert outcome.ok
    assert jpeg_path.exists(), "should preserve file when delete_local_after_upload=False"


# --------------------------------------------------------------------------- #
# non-ok statuses short-circuit
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["miss", "decode_error", "write_error"])
async def test_non_ok_status_does_not_upload(uploader_factory, status: str) -> None:
    u, put, _root = uploader_factory()
    outcome = await u.handle({
        "request_id": "req-miss",
        "camera_id":  "cam_01",
        "status":     status,
        "path":       None,
    })
    assert outcome.ok is False
    assert outcome.reason.startswith("bad_status:")
    assert status in outcome.reason
    assert put.calls == []


# --------------------------------------------------------------------------- #
# security: path traversal is rejected
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_path_traversal_is_rejected(uploader_factory, tmp_path: Path) -> None:
    u, put, _root = uploader_factory()

    # Write a file *outside* the allowed root.
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"SENSITIVE")

    outcome = await u.handle({
        "request_id": "req-bad",
        "camera_id":  "cam_01",
        "status":     "ok",
        "path":       str(outside),
    })
    assert outcome.ok is False
    assert outcome.reason == "path_traversal"
    assert put.calls == []
    assert outside.exists(), "file outside allowed root must not be touched"


# --------------------------------------------------------------------------- #
# missing file
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_missing_file_is_reported(uploader_factory) -> None:
    u, put, root = uploader_factory()
    outcome = await u.handle({
        "request_id": "req-gone",
        "camera_id":  "cam_01",
        "status":     "ok",
        "path":       str(root / "nope.jpg"),
    })
    assert outcome.ok is False
    assert outcome.reason == "no_file"
    assert put.calls == []


# --------------------------------------------------------------------------- #
# malformed envelopes
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_empty_envelope_is_rejected(uploader_factory) -> None:
    u, put, _root = uploader_factory()
    outcome = await u.handle({})
    assert outcome.ok is False
    assert outcome.reason == "bad_envelope"
    assert put.calls == []


@pytest.mark.asyncio
async def test_to_envelope_shape_ok(uploader_factory) -> None:
    u, _put, root = uploader_factory()
    jpeg = root / "shape.jpg"
    jpeg.write_bytes(b"\xff\xd8\xff\xe0\x00\x10data")
    outcome = await u.handle({
        "request_id": "req-shape",
        "camera_id":  "cam_99",
        "status":     "ok",
        "timestamp":  "2026-04-17T10:23:45.123Z",
        "path":       str(jpeg),
    })
    env = outcome.to_envelope(bucket="snapshots")
    assert env["request_id"] == "req-shape"
    assert env["camera_id"]  == "cam_99"
    assert env["status"]     == "ok"
    assert env["bucket"]     == "snapshots"
    assert env["minio_key"]  == outcome.minio_key
    assert env["bytes"]      == outcome.bytes_uploaded
    assert "timestamp" in env
