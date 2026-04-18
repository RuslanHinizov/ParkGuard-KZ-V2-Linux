"""
python-services/shared/kafka_client.py

aiokafka producer/consumer factories. Producer is a shared singleton per
process (aiokafka is async-safe for concurrent `send_and_wait`). Consumers
are per-group; callers construct them with the right topic set and commit
manually (spec §15.1: `enable_auto_commit=False`, manual commit after
processing — prevents duplicate ceza on rebalance).

Message envelope: JSON-encoded; `trace_id` and `sensor_id` propagated via
Kafka headers so structured logs keep context across service hops.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaConnectionError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from shared.config import settings
from shared.logging import get_logger

logger = get_logger(__name__)

_producer: AIOKafkaProducer | None = None


# --------------------------------------------------------------------------- #
# Producer
# --------------------------------------------------------------------------- #
async def get_producer() -> AIOKafkaProducer:
    """Lazy-init shared producer (LZ4 compression, idempotent)."""
    global _producer
    if _producer is None:
        _producer = AIOKafkaProducer(
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            client_id=f"{settings.KAFKA_CLIENT_ID}-prod",
            compression_type="lz4",
            enable_idempotence=True,
            acks="all",
            linger_ms=5,
            max_request_size=10 * 1024 * 1024,    # 10 MB (crops + embeddings)
        )
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(10),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=10),
            retry=retry_if_exception_type(KafkaConnectionError),
            reraise=True,
        ):
            with attempt:
                await _producer.start()
        logger.info("kafka_producer_started", brokers=settings.KAFKA_BOOTSTRAP_SERVERS)
    return _producer


async def close_producer() -> None:
    global _producer
    if _producer is not None:
        try:
            await _producer.stop()
            logger.info("kafka_producer_stopped")
        except Exception as exc:  # noqa: BLE001
            logger.warning("kafka_producer_stop_error", error=str(exc))
    _producer = None


async def send_json(
    topic: str,
    value: dict[str, Any],
    *,
    key: str | bytes | None = None,
    headers: dict[str, str] | None = None,
) -> None:
    """
    Encode `value` as JSON and publish to `topic`. Adds trace_id header
    automatically when a contextvar trace is active. Waits for broker ack.
    """
    from shared.logging import get_trace_id

    producer = await get_producer()

    hdr: list[tuple[str, bytes]] = []
    if headers:
        hdr.extend((k, v.encode("utf-8")) for k, v in headers.items())
    tid = get_trace_id()
    if tid and not any(h[0] == "trace_id" for h in hdr):
        hdr.append(("trace_id", tid.encode("utf-8")))

    payload = json.dumps(value, default=_json_default, separators=(",", ":")).encode("utf-8")
    key_bytes = key.encode("utf-8") if isinstance(key, str) else key

    await producer.send_and_wait(topic, payload, key=key_bytes, headers=hdr or None)


# --------------------------------------------------------------------------- #
# Consumer
# --------------------------------------------------------------------------- #
def make_consumer(
    topics: Iterable[str],
    *,
    group_id: str,
    auto_offset_reset: str = "earliest",
    max_poll_records: int = 200,
) -> AIOKafkaConsumer:
    """
    Build a per-group consumer. Caller is responsible for
    ``await consumer.start()`` and manual commit after processing.

    Manual commit is mandatory (spec §15.1, §15.8): we commit only after
    the handler has *successfully* persisted state, preventing duplicate
    violation processing on rebalance or crash.
    """
    return AIOKafkaConsumer(
        *topics,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        client_id=f"{settings.KAFKA_CLIENT_ID}-{group_id}",
        group_id=group_id,
        enable_auto_commit=False,
        auto_offset_reset=auto_offset_reset,
        max_poll_records=max_poll_records,
        session_timeout_ms=30000,
        heartbeat_interval_ms=10000,
        fetch_max_bytes=10 * 1024 * 1024,
        max_partition_fetch_bytes=10 * 1024 * 1024,
    )


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
async def ping() -> bool:
    """Healthcheck — True if broker responds to metadata request."""
    try:
        producer = await get_producer()
        # aiokafka uses `client` attribute for broker metadata fetching
        cluster = producer.client.cluster
        brokers = list(cluster.brokers())
        if brokers:
            return True
        await producer.client._wait_on_metadata(topic=None, timeout_ms=2000)
        return bool(cluster.brokers())
    except Exception as exc:  # noqa: BLE001
        logger.warning("kafka_ping_failed", error=str(exc))
        return False


def _json_default(obj: Any) -> Any:
    """Handle datetime, UUID, bytes, numpy arrays when serialising."""
    import base64
    from datetime import date, datetime
    from uuid import UUID

    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, bytes):
        return base64.b64encode(obj).decode("ascii")
    if hasattr(obj, "tolist"):  # numpy array
        return obj.tolist()
    if hasattr(obj, "__dataclass_fields__"):
        from dataclasses import asdict
        return asdict(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serialisable")


__all__ = [
    "close_producer",
    "get_producer",
    "make_consumer",
    "ping",
    "send_json",
]
