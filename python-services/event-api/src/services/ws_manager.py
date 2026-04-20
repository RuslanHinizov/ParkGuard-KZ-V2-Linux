"""
python-services/event-api/src/services/ws_manager.py

Thread-safe WebSocket fanout manager. Holds an in-process set of active
connections; the violation stream router and the Kafka fanout task both
talk to this singleton.

Design constraints (spec §11):
  * Fanout is in-process — no Redis pub/sub — because we run one uvicorn
    worker per host (no multiprocessing). Multiple hosts → operators
    connect to their local host; the dashboard aggregates via the React
    multi-camera grid.
  * Disconnect errors are silently swallowed; stale connections are
    removed on the next broadcast.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import WebSocket

from shared.logging import get_logger

logger = get_logger(__name__)


class ConnectionManager:
    """Fan-out JSON messages to all connected WebSocket clients."""

    def __init__(self) -> None:
        self._lock: asyncio.Lock = asyncio.Lock()
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)
        logger.info("ws_client_connected", total=len(self._clients))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)
        logger.info("ws_client_disconnected", total=len(self._clients))

    async def broadcast(self, payload: dict[str, Any]) -> None:
        """Send payload to all connected clients; remove closed connections."""
        text = json.dumps(payload, default=str)
        dead: list[WebSocket] = []
        async with self._lock:
            targets = list(self._clients)
        for ws in targets:
            try:
                await ws.send_text(text)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)

    @property
    def connection_count(self) -> int:
        return len(self._clients)


# Module-level singleton shared by the router and the Kafka fanout task.
manager: ConnectionManager = ConnectionManager()

__all__ = ["ConnectionManager", "manager"]
