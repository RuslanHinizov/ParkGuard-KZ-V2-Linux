"""
python-services/event-api/src/middleware/operator.py

Captures three pieces of context on every request and attaches them to
`request.state` for downstream handlers/audit:

    - trace_id       — UUID4, also echoed back in the `X-Trace-Id` response
                       header and bound to structlog for the request's
                       lifetime.
    - operator_name  — value of the `X-Operator-Name` header (spec §11).
                       Required for mutating verbs; GET/HEAD/OPTIONS
                       tolerate its absence.
    - client_ip      — best-effort remote address for the audit log's
                       `ip_address` column.

The middleware is deliberately permissive on read-only endpoints so the
dashboard can render even when the operator modal has not yet been
shown (spec §17.3 first-visit flow).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from shared.logging import bind_context, get_logger, new_trace_id, set_trace_id

if TYPE_CHECKING:
    from starlette.middleware.base import RequestResponseEndpoint

logger = get_logger(__name__)

# Paths that never require an operator name (ops + docs + static).
_EXEMPT_PATHS: frozenset[str] = frozenset({
    "/healthz",
    "/readyz",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
})

_READ_ONLY_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})


def _is_exempt(path: str) -> bool:
    if path in _EXEMPT_PATHS:
        return True
    # Allow docs sub-paths (e.g. /docs/oauth2-redirect) without auth header.
    return path.startswith(("/docs/", "/redoc/"))


def _extract_client_ip(request: Request) -> str | None:
    # nginx sits in front in prod; honor X-Forwarded-For first token.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip() or None
    return request.client.host if request.client else None


class OperatorContextMiddleware(BaseHTTPMiddleware):
    """Attach trace/operator/ip to request.state; enforce header on mutations."""

    async def dispatch(
        self,
        request: Request,
        call_next: "RequestResponseEndpoint",
    ) -> Response:
        # ---- trace id ----
        incoming_trace = request.headers.get("x-trace-id")
        trace_id = incoming_trace or new_trace_id()
        set_trace_id(trace_id)
        request.state.trace_id = trace_id

        # ---- operator + ip ----
        operator_name = request.headers.get("x-operator-name")
        if operator_name is not None:
            operator_name = operator_name.strip() or None
        request.state.operator_name = operator_name
        request.state.client_ip = _extract_client_ip(request)

        bind_context(
            trace_id=trace_id,
            method=request.method,
            path=request.url.path,
            operator=operator_name,
        )

        # ---- enforce on mutating verbs ----
        if (
            request.method not in _READ_ONLY_METHODS
            and not _is_exempt(request.url.path)
            and not operator_name
        ):
            logger.warning("operator_name_missing", path=request.url.path)
            return JSONResponse(
                status_code=400,
                content={
                    "detail": (
                        "X-Operator-Name header is required for state-changing "
                        "requests (see spec §11). This is not authentication; "
                        "the header is captured for the audit log only."
                    ),
                    "trace_id": trace_id,
                },
                headers={"X-Trace-Id": trace_id},
            )

        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        return response
