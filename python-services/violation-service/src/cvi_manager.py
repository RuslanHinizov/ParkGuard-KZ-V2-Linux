"""
python-services/violation-service/src/cvi_manager.py

CVI manager — spec §4.3 4-tier fallback chain.

Active CVIs are kept in a small per-process dictionary; Redis is used
for the cross-process plate→cvi index and as the restart-survivable
snapshot (spec §4.4). A future adım can swap the in-memory dict for
a Redis-only path, but the hot path has to stay allocation-free, so
the process-local cache stays.

The match priorities (top-down) are:

    1. PLATE MATCH       — obs.plate_conf ≥ 0.75, last_seen < 300 s
    2. SPATIAL+TEMPORAL  — same camera, last_seen < 3 s, IoU > 0.3
    3. VISUAL EMBEDDING  — same camera, last_seen < 30 s, cosine > 0.82
    4. DS_TRACK_ID       — same camera, last_seen < 2 s

The manager does not itself talk to the state machine — it only
resolves "which CVI does this observation belong to". The caller
passes the resulting CVI into `state_machine.evaluate(...)`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import UUID

import numpy as np

from shared.geometry import BBox, bbox_iou
from shared.logging import get_logger
from shared.plate_normalize import normalize_kz_plate
from src.cvi import CVI, PLATE_MIN_CONFIDENCE

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from src.observation import Observation

logger = get_logger(__name__)


# Thresholds — spec §4.3.
PLATE_LAST_SEEN_WINDOW = timedelta(seconds=300)
SPATIAL_LAST_SEEN_WINDOW = timedelta(seconds=3)
SPATIAL_IOU_THRESHOLD = 0.30
EMBEDDING_LAST_SEEN_WINDOW = timedelta(seconds=30)
EMBEDDING_COSINE_THRESHOLD = 0.82
TRACK_ID_LAST_SEEN_WINDOW = timedelta(seconds=2)

# Redis TTL for active CVI ownership (spec §4.4)
ACTIVE_CVI_TTL_SECONDS = 600


@dataclass(slots=True, frozen=True)
class MatchResult:
    cvi_id: UUID
    priority: int
    reason: str  # "plate" | "spatial" | "embedding" | "track" | "new"


class CVIManager:
    """
    Process-local CVI store with a 4-tier match algorithm.

    `redis` is accepted but optional so tests can exercise the match
    algorithm without a live Redis. When redis is None, cross-process
    plate-index lookups degrade to a no-op and identity is still
    guaranteed by the DB unique constraint downstream.
    """

    def __init__(self, redis: Redis | None = None) -> None:
        self._cvis: dict[UUID, CVI] = {}
        self._by_camera: dict[str, set[UUID]] = {}
        self._redis: Redis | None = redis

    # ------------------------------------------------------------------ store
    def get(self, cvi_id: UUID) -> CVI | None:
        return self._cvis.get(cvi_id)

    def all_for_camera(self, camera_id: str) -> list[CVI]:
        return [self._cvis[i] for i in self._by_camera.get(camera_id, ())]

    def size(self) -> int:
        return len(self._cvis)

    # ------------------------------------------------------------------ main
    async def resolve(self, obs: Observation) -> tuple[CVI, MatchResult]:
        """
        Return the CVI this observation belongs to, creating a new one
        if no priority 1-4 match succeeds.
        """
        match = self._match(obs)
        if match is not None:
            cvi = self._cvis[match.cvi_id]
            cvi.apply(obs)
            await self._index_plate(cvi)
            return cvi, match

        # No match → new CVI.
        cvi = CVI.from_observation(obs)
        self._cvis[cvi.cvi_id] = cvi
        self._by_camera.setdefault(cvi.camera_id, set()).add(cvi.cvi_id)
        await self._index_plate(cvi)
        logger.info(
            "cvi_created",
            cvi_id=str(cvi.cvi_id),
            camera=cvi.camera_id,
            plate=cvi.plate_text,
        )
        return cvi, MatchResult(cvi_id=cvi.cvi_id, priority=0, reason="new")

    async def apply_ocr_result(
        self,
        *,
        cvi_id: UUID,
        plate_text: str | None,
        plate_confidence: float,
    ) -> bool:
        """
        Fold an ``ocr_results`` message back into its CVI. Returns True
        if the vote stabilised a new plate (i.e. we reached the
        majority threshold on this tick), which callers can use to
        decide whether to re-key Redis indexes.

        A missing CVI is not an error — it simply means we evicted it
        before the OCR result came back. Common under heavy traffic.
        """
        cvi = self._cvis.get(cvi_id)
        if cvi is None:
            logger.info("ocr_result_cvi_missing", cvi_id=str(cvi_id))
            return False
        stabilised = cvi.record_plate_vote(plate_text, plate_confidence)
        if stabilised:
            await self._index_plate(cvi)
            logger.info(
                "cvi_plate_stabilised",
                cvi_id=str(cvi_id),
                plate=cvi.plate_text,
            )
        return stabilised

    def evict_stale(self, now: datetime, *, max_idle_seconds: int = 600) -> int:
        """Drop CVIs idle for > max_idle_seconds — matches spec §4.4."""
        cutoff = now - timedelta(seconds=max_idle_seconds)
        dead = [cid for cid, cvi in self._cvis.items() if cvi.last_seen < cutoff]
        for cid in dead:
            cvi = self._cvis.pop(cid)
            self._by_camera.get(cvi.camera_id, set()).discard(cid)
        if dead:
            logger.info("cvi_evicted", count=len(dead))
        return len(dead)

    # ------------------------------------------------------------------ match
    def _match(self, obs: Observation) -> MatchResult | None:
        candidates = [self._cvis[i] for i in self._by_camera.get(obs.camera_id, ())]
        if not candidates:
            return None
        now = obs.timestamp

        # -- Priority 1: PLATE -------------------------------------------------
        if obs.plate_text and obs.plate_conf and obs.plate_conf >= PLATE_MIN_CONFIDENCE:
            normalized = normalize_kz_plate(obs.plate_text).text or obs.plate_text
            for c in candidates:
                if c.plate_text and normalize_kz_plate(c.plate_text).text == normalized:
                    if _within(now, c.last_seen, PLATE_LAST_SEEN_WINDOW):
                        return MatchResult(c.cvi_id, 1, "plate")

        # -- Priority 2: SPATIAL+TEMPORAL -------------------------------------
        # We intentionally compare the new bbox against the CVI's *last*
        # bbox directly rather than motion-compensated — a velocity
        # estimated from the observation we're trying to match would
        # trivially predict itself and defeat the IoU gate. Over a 3-s
        # window at typical parking-camera frame rates, zero-velocity
        # prediction is already within the IoU tolerance.
        spatial: list[tuple[float, CVI]] = []
        for c in candidates:
            if c.last_bbox is None:
                continue
            if not _within(now, c.last_seen, SPATIAL_LAST_SEEN_WINDOW):
                continue
            iou = bbox_iou(c.last_bbox, obs.bbox)
            if iou > SPATIAL_IOU_THRESHOLD:
                spatial.append((iou, c))
        if len(spatial) == 1:
            return MatchResult(spatial[0][1].cvi_id, 2, "spatial")
        if len(spatial) > 1 and obs.embedding is not None:
            # Tiebreak by embedding — highest cosine wins.
            picked = _pick_by_embedding([c for _, c in spatial], obs.embedding)
            if picked is not None:
                return MatchResult(picked.cvi_id, 2, "spatial")

        # -- Priority 3: VISUAL EMBEDDING -------------------------------------
        if obs.embedding is not None:
            emb_pool = [
                c for c in candidates
                if c.embedding_centroid is not None
                and _within(now, c.last_seen, EMBEDDING_LAST_SEEN_WINDOW)
            ]
            if emb_pool:
                scored = [(_cosine(c.embedding_centroid, obs.embedding), c) for c in emb_pool]  # type: ignore[arg-type]
                scored.sort(key=lambda x: x[0], reverse=True)
                best_sim, best = scored[0]
                if best_sim >= EMBEDDING_COSINE_THRESHOLD:
                    return MatchResult(best.cvi_id, 3, "embedding")

        # -- Priority 4: DS_TRACK_ID fallback ---------------------------------
        for c in candidates:
            if c.last_ds_track_id == obs.ds_track_id and _within(
                now, c.last_seen, TRACK_ID_LAST_SEEN_WINDOW
            ):
                return MatchResult(c.cvi_id, 4, "track")

        return None

    # ------------------------------------------------------------------ redis
    async def _index_plate(self, cvi: CVI) -> None:
        """Maintain the plate → cvi_id Redis index (spec §4.4)."""
        if self._redis is None or not cvi.plate_text:
            return
        try:
            normalised = normalize_kz_plate(cvi.plate_text).text or cvi.plate_text
            key = f"plate:{normalised}"
            await self._redis.sadd(key, str(cvi.cvi_id))
            await self._redis.expire(key, ACTIVE_CVI_TTL_SECONDS)
        except Exception as exc:  # noqa: BLE001 — Redis indexing is best-effort
            logger.warning("cvi_plate_index_failed", error=str(exc))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _within(now: datetime, last: datetime, window: timedelta) -> bool:
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now - last <= window


def _pick_by_embedding(candidates: list[CVI], sample: np.ndarray) -> CVI | None:
    best_sim = -1.0
    best: CVI | None = None
    for c in candidates:
        if c.embedding_centroid is None:
            continue
        sim = _cosine(c.embedding_centroid, sample)
        if sim > best_sim:
            best_sim = sim
            best = c
    return best if best_sim >= EMBEDDING_COSINE_THRESHOLD else best


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


__all__ = [
    "ACTIVE_CVI_TTL_SECONDS",
    "CVIManager",
    "EMBEDDING_COSINE_THRESHOLD",
    "EMBEDDING_LAST_SEEN_WINDOW",
    "MatchResult",
    "PLATE_LAST_SEEN_WINDOW",
    "SPATIAL_IOU_THRESHOLD",
    "SPATIAL_LAST_SEEN_WINDOW",
    "TRACK_ID_LAST_SEEN_WINDOW",
]

# (BBox exported for dependents/readers that previously imported it from here.)
_ = BBox
