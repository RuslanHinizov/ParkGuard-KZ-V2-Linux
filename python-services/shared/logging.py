"""
python-services/shared/logging.py

structlog-based structured JSON logging. Every log record carries a trace_id
(set via contextvar) plus service, camera_id, cvi_id, zone_id context when
available. FastAPI middleware (event-api/src/middleware.py) and Kafka consumer
loops bind these keys before processing; child loggers inherit the binding.

Usage:
    from shared.logging import get_logger, bind_context

    logger = get_logger(__name__)
    bind_context(camera_id="cam_01", trace_id="abc")
    logger.info("detection_received", objects=5)
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars, merge_contextvars
from structlog.types import EventDict, Processor

from shared.config import settings

# ContextVar-backed trace propagation (Kafka headers, FastAPI request scope).
_trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")


def _ensure_trace_id(_: Any, __: str, event_dict: EventDict) -> EventDict:
    """Inject trace_id from contextvar if caller didn't supply one."""
    if "trace_id" not in event_dict:
        tid = _trace_id_var.get()
        if tid:
            event_dict["trace_id"] = tid
    return event_dict


def _ensure_service(_: Any, __: str, event_dict: EventDict) -> EventDict:
    event_dict.setdefault("service", settings.PROJECT_NAME)
    event_dict.setdefault("env", settings.ENV)
    return event_dict


def configure_logging(service_name: str | None = None) -> None:
    """Configure structlog + stdlib logging. Call once at service startup."""
    level = getattr(logging, settings.LOG_LEVEL, logging.INFO)

    shared_processors: list[Processor] = [
        merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _ensure_trace_id,
        _ensure_service,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.JSONRenderer(sort_keys=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging (uvicorn, aiokafka, sqlalchemy) through structlog.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Quiet noisy libraries.
    for noisy in ("aiokafka", "kafka", "asyncio", "botocore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if service_name:
        bind_contextvars(service=service_name)


def new_trace_id() -> str:
    tid = uuid4().hex
    _trace_id_var.set(tid)
    return tid


def set_trace_id(trace_id: str) -> None:
    _trace_id_var.set(trace_id)


def get_trace_id() -> str:
    return _trace_id_var.get()


def bind_context(**kwargs: Any) -> None:
    """Bind per-operation context keys (camera_id, cvi_id, zone_id, ...)."""
    bind_contextvars(**kwargs)


def clear_context() -> None:
    """Clear per-operation context — call at end of request/message scope."""
    clear_contextvars()


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
