"""
python-services/violation-service/src/cvi.py

In-memory Composite Vehicle Identity type. The dataclass mirrors the
field set described in spec §4.2, plus the per-zone state dict needed
by the state machine (`state_per_zone`).

This type is the *live* representation used while the vehicle is
actively tracked; a slim archival form ends up in `cvi_records` (see
`models.cvi.CVIRecord`). Mutations below (update/apply) are pure and
keep all the arithmetic localised so the CVI manager + state machine
can stay oblivious to EMA / plate-vote mechanics.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import numpy as np

from shared.geometry import BBox
from shared.schemas import CVIState

if TYPE_CHECKING:
    from src.observation import Observation

EMBEDDING_EMA_ALPHA = 0.1         # spec §4.4 — new = 0.9*old + 0.1*obs
PLATE_VOTE_MAJORITY = 3           # spec §5.1
PLATE_MIN_CONFIDENCE = 0.75       # spec §4.3 priority 1


@dataclass(slots=True)
class ZoneStateEntry:
    """Per-(cvi, zone) state snapshot — owned by CVI.state_per_zone."""

    state: CVIState = CVIState.OUTSIDE
    entered_at: datetime | None = None
    exit_candidate_at: datetime | None = None
    cooldown_until: datetime | None = None
    cycle_id: int = 0
    violation_id: int | None = None
    last_active_violation_key: str | None = None


@dataclass(slots=True)
class CVI:
    cvi_id: UUID
    camera_id: str
    first_seen: datetime
    last_seen: datetime

    plate_text: str | None = None
    plate_confidence: float | None = None
    plate_votes: Counter[str] = field(default_factory=Counter)

    embedding_centroid: np.ndarray | None = None
    embedding_samples: int = 0

    last_bbox: BBox | None = None
    last_centroid: tuple[int, int] = (0, 0)
    last_ds_track_id: int = -1

    active_zones: set[int] = field(default_factory=set)
    state_per_zone: dict[int, ZoneStateEntry] = field(default_factory=dict)

    dominant_class: str | None = None
    observations_count: int = 0

    # ------------------------------------------------------------------ API
    @classmethod
    def from_observation(cls, obs: Observation) -> CVI:
        c = cls(
            cvi_id=uuid4(),
            camera_id=obs.camera_id,
            first_seen=obs.timestamp,
            last_seen=obs.timestamp,
        )
        c.apply(obs)
        return c

    def apply(self, obs: Observation) -> None:
        """Fold a new Observation into this CVI's running identity state."""
        self.last_seen = obs.timestamp
        self.last_bbox = obs.bbox
        self.last_centroid = obs.centroid
        self.last_ds_track_id = obs.ds_track_id
        self.dominant_class = obs.class_name
        self.observations_count += 1

        if obs.embedding is not None:
            self._update_embedding(obs.embedding)

        if obs.plate_text and obs.plate_conf and obs.plate_conf >= PLATE_MIN_CONFIDENCE:
            self.plate_votes[obs.plate_text] += 1
            top, votes = self.plate_votes.most_common(1)[0]
            if votes >= PLATE_VOTE_MAJORITY:
                self.plate_text = top
                self.plate_confidence = obs.plate_conf

    # ------------------------------------------------------------------ util
    def _update_embedding(self, sample: np.ndarray) -> None:
        if self.embedding_centroid is None:
            self.embedding_centroid = sample.astype(np.float32, copy=True)
        else:
            self.embedding_centroid = (
                (1.0 - EMBEDDING_EMA_ALPHA) * self.embedding_centroid
                + EMBEDDING_EMA_ALPHA * sample
            ).astype(np.float32, copy=False)
        self.embedding_samples += 1

    def zone_state(self, zone_id: int) -> ZoneStateEntry:
        """Get-or-create the per-zone state entry."""
        entry = self.state_per_zone.get(zone_id)
        if entry is None:
            entry = ZoneStateEntry()
            self.state_per_zone[zone_id] = entry
        return entry


__all__ = [
    "CVI",
    "EMBEDDING_EMA_ALPHA",
    "PLATE_MIN_CONFIDENCE",
    "PLATE_VOTE_MAJORITY",
    "ZoneStateEntry",
]
