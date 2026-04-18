"""
python-services/shared/minio_client.py

Async MinIO / S3 wrapper used by snapshot-consumer, penalty-card-service,
and event-api (presigned URLs for the dashboard). Uses the official
``minio`` client behind ``asyncio.to_thread`` — the SDK is sync-only but
operations are fast and I/O-bound, so thread offload is sufficient for
our scale (8–12 cameras).

Bucket bootstrapping happens in the docker-compose `minio-init` one-shot.
"""

from __future__ import annotations

import asyncio
import io
from datetime import timedelta
from typing import BinaryIO

from minio import Minio
from minio.error import S3Error

from shared.config import settings
from shared.logging import get_logger

logger = get_logger(__name__)

_client: Minio | None = None


def get_client() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ROOT_USER,
            secret_key=settings.MINIO_ROOT_PASSWORD,
            secure=settings.MINIO_USE_SSL,
        )
        logger.info("minio_client_initialized", endpoint=settings.MINIO_ENDPOINT)
    return _client


async def put_object(
    bucket: str,
    key: str,
    data: bytes | BinaryIO,
    *,
    content_type: str = "application/octet-stream",
) -> str:
    """Upload bytes to MinIO. Returns the object key (not a URL)."""
    client = get_client()
    if isinstance(data, bytes):
        stream: BinaryIO = io.BytesIO(data)
        length = len(data)
    else:
        data.seek(0, io.SEEK_END)
        length = data.tell()
        data.seek(0)
        stream = data

    def _upload() -> None:
        client.put_object(
            bucket,
            key,
            stream,
            length=length,
            content_type=content_type,
        )

    await asyncio.to_thread(_upload)
    logger.debug("minio_put_object", bucket=bucket, key=key, bytes=length)
    return key


async def get_object(bucket: str, key: str) -> bytes:
    """Download object content as bytes."""
    client = get_client()

    def _download() -> bytes:
        response = client.get_object(bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    return await asyncio.to_thread(_download)


async def presigned_get_url(
    bucket: str,
    key: str,
    *,
    ttl_seconds: int | None = None,
) -> str:
    """Generate a short-lived GET URL (spec §17: 15 min default)."""
    client = get_client()
    ttl = ttl_seconds or settings.MINIO_PRESIGN_TTL_SECONDS

    def _sign() -> str:
        return client.presigned_get_object(
            bucket, key, expires=timedelta(seconds=ttl)
        )

    return await asyncio.to_thread(_sign)


async def ensure_bucket(bucket: str) -> None:
    """Idempotent — create bucket if missing (used in tests)."""
    client = get_client()

    def _ensure() -> None:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
            logger.info("minio_bucket_created", bucket=bucket)

    await asyncio.to_thread(_ensure)


async def ping() -> bool:
    """Healthcheck — list buckets to confirm credentials + connectivity."""
    try:
        client = get_client()
        await asyncio.to_thread(client.list_buckets)
        return True
    except S3Error as exc:
        logger.warning("minio_ping_failed", error=str(exc))
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("minio_ping_failed_unknown", error=str(exc))
        return False


__all__ = [
    "ensure_bucket",
    "get_client",
    "get_object",
    "ping",
    "presigned_get_url",
    "put_object",
]
