"""
python-services/violation-service/src/state_machine.py

Per-(CVI × zone) state machine — spec §5.

The transitions are driven entirely by `evaluate(cvi, obs)`: each
observation is matched against every live zone on the camera and the
resulting state is advanced. The machine is side-effect-aware:

  * VIOLATED  ⇒ writes the DB row (via ViolationWriter) and records the
    Redis active_violation entry (spec §8 layer 2).
  * COOLDOWN → OUTSIDE ⇒ bumps the cycle_id counter.

Every transition is emitted to the Prometheus counter
`parkguard_state_transitions_total` so Grafana can display per-zone
conversion funnels.

The machine is **pure with respect to time** — it takes `now=obs.timestamp`
rather than `datetime.utcnow()` so tests can drive the timeline with
explicit timestamps.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from shapely.geometry import Point

from shared.logging import get_logger
from shared.schemas import CVIState
from src.identity import get_violation_identity
from src.metrics import DUPLICATES_PREVENTED, STATE_TRANSITIONS
from src.violation_writer import ViolationInsert

if TYPE_CHECKING:
    from src.active_registry import ActiveViolationRegistry
    from src.cvi import CVI
    from src.observation import Observation
    from src.violation_writer import ViolationWriter
    from src.zone_cache import ZoneEntry

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class TransitionEvent:
    """Emitted by `evaluate` for observability / tests."""

    cvi_id: str
    zone_id: int
    from_state: CVIState
    to_state: CVIState
    reason: str
    violation_id: int | None = None
    cycle_id: int | None = None


class StateMachine:
    def __init__(
        self,
        *,
        writer: ViolationWriter,
        registry: ActiveViolationRegistry,
    ) -> None:
        self._writer = writer
        self._registry = registry

    async def evaluate(
        self,
        cvi: CVI,
        obs: Observation,
        zones: list[ZoneEntry],
    ) -> list[TransitionEvent]:
        """Advance every zone on this camera; return the transitions fired."""
        events: list[TransitionEvent] = []
        for zone in zones:
            ev = await self._evaluate_zone(cvi, obs, zone)
            if ev is not None:
                events.append(ev)
        return events

    # ----------------------------------------------------------- per-zone
    async def _evaluate_zone(
        self,
        cvi: CVI,
        obs: Observation,
        zone: ZoneEntry,
    ) -> TransitionEvent | None:
        inside = zone.polygon.contains(Point(obs.centroid[0], obs.centroid[1]))
        entry = cvi.zone_state(zone.id)
        now = _aware(obs.timestamp)
        prev = entry.state

        match prev:
            case CVIState.OUTSIDE:
                if inside:
                    entry.state = CVIState.INSIDE_ZONE
                    entry.entered_at = now
                    entry.exit_candidate_at = None
                    cvi.active_zones.add(zone.id)
                    return self._emit(cvi, zone, prev, entry.state, "enter")

            case CVIState.INSIDE_ZONE:
                if not inside:
                    entry.state = CVIState.EXIT_CANDIDATE
                    entry.exit_candidate_at = now
                    return self._emit(cvi, zone, prev, entry.state, "exit_candidate")
                # Still inside — has the threshold matured?
                if entry.entered_at is not None:
                    elapsed = (now - _aware(entry.entered_at)).total_seconds()
                    if elapsed >= zone.threshold_seconds:
                        return await self._fire_violation(cvi, obs, zone, entry, now)

            case CVIState.EXIT_CANDIDATE:
                if inside:
                    # Re-entry during grace: cancel timer, do NOT reset entered_at.
                    entry.state = CVIState.INSIDE_ZONE
                    entry.exit_candidate_at = None
                    return self._emit(cvi, zone, prev, entry.state, "re_entry")
                if entry.exit_candidate_at is not None:
                    elapsed = (now - _aware(entry.exit_candidate_at)).total_seconds()
                    if elapsed >= zone.exit_confirm_seconds:
                        # Confirmed exit. If we had written a violation this
                        # cycle, close it and enter cooldown.
                        if entry.violation_id is not None:
                            entry.state = CVIState.COOLDOWN
                            entry.cooldown_until = now + timedelta(
                                seconds=zone.cooldown_seconds
                            )
                            # Drop the active_violation registry row so a
                            # re-entry after cooldown starts fresh.
                            if entry.last_active_violation_key is not None:
                                await self._registry.clear(
                                    cvi.camera_id,
                                    zone.id,
                                    entry.last_active_violation_key,
                                )
                            return self._emit(cvi, zone, prev, entry.state, "confirmed_exit")
                        # No violation in this cycle — straight back to OUTSIDE.
                        entry.state = CVIState.OUTSIDE
                        entry.entered_at = None
                        entry.exit_candidate_at = None
                        cvi.active_zones.discard(zone.id)
                        return self._emit(cvi, zone, prev, entry.state, "exit_no_violation")

            case CVIState.VIOLATED:
                if not inside:
                    entry.state = CVIState.EXIT_CANDIDATE
                    entry.exit_candidate_at = now
                    return self._emit(cvi, zone, prev, entry.state, "violated_exit_start")
                # Self-loop: update duration — handled by caller; no state change.
                return None

            case CVIState.COOLDOWN:
                if entry.cooldown_until is not None and now >= _aware(entry.cooldown_until):
                    # Cooldown over.
                    entry.violation_id = None
                    entry.last_active_violation_key = None
                    entry.entered_at = None
                    entry.exit_candidate_at = None
                    cvi.active_zones.discard(zone.id)

                    # cycle_id gets incremented at this boundary (spec §5.3).
                    if entry.last_active_violation_key is None:
                        # We don't know which identity was active without it,
                        # so recompute from the current CVI identity.
                        identity = get_violation_identity(cvi)
                        new_cycle = await self._registry.bump_cycle(
                            cvi.camera_id, zone.id, identity
                        )
                        entry.cycle_id = new_cycle

                    if inside:
                        # Immediate new cycle.
                        entry.state = CVIState.INSIDE_ZONE
                        entry.entered_at = now
                        cvi.active_zones.add(zone.id)
                        return self._emit(cvi, zone, prev, entry.state, "new_cycle")
                    entry.state = CVIState.OUTSIDE
                    return self._emit(cvi, zone, prev, entry.state, "cooldown_over")

        return None

    # ----------------------------------------------------------- fire
    async def _fire_violation(
        self,
        cvi: CVI,
        obs: Observation,
        zone: ZoneEntry,
        entry,
        now: datetime,
    ) -> TransitionEvent:
        identity = get_violation_identity(cvi)
        cycle = await self._registry.current_cycle(cvi.camera_id, zone.id, identity)
        entry.cycle_id = cycle

        # Layer 2 — Redis active_violation guard (spec §1.2, §8).
        if await self._registry.is_active(cvi.camera_id, zone.id, identity):
            DUPLICATES_PREVENTED.labels(layer="redis").inc()
            # Promote to VIOLATED in-memory so self-loops behave, but skip DB write.
            entry.state = CVIState.VIOLATED
            entry.last_active_violation_key = identity
            logger.info(
                "violation_skipped_active_registry",
                camera=cvi.camera_id,
                zone=zone.id,
                identity=identity,
            )
            return TransitionEvent(
                cvi_id=str(cvi.cvi_id),
                zone_id=zone.id,
                from_state=CVIState.INSIDE_ZONE,
                to_state=CVIState.VIOLATED,
                reason="threshold_met_but_active",
                violation_id=None,
                cycle_id=cycle,
            )

        # Layer 4 — DB unique constraint (writer swallows IntegrityError).
        row = ViolationInsert(
            camera_id=cvi.camera_id,
            zone_id=zone.id,
            cvi_id=cvi.cvi_id,
            identity_key=identity,
            cycle_id=cycle,
            plate_text=cvi.plate_text,
            plate_confidence=cvi.plate_confidence,
            plate_format=obs.plate_format,
            plate_region_code=obs.plate_region_code,
            plate_valid_format=obs.plate_valid_format,
            is_diplomatic=obs.is_diplomatic,
            vehicle_class=cvi.dominant_class or obs.class_name,
            first_seen_in_zone=entry.entered_at or now,
            violation_time=now,
            bbox=obs.bbox.as_dict(),
        )
        result = await self._writer.insert(row)

        entry.state = CVIState.VIOLATED
        entry.violation_id = result.violation_id if result.created else entry.violation_id
        entry.last_active_violation_key = identity
        await self._registry.mark_active(
            cvi.camera_id, zone.id, identity, violation_id=result.violation_id
        )

        reason = "threshold_met" if result.created else "threshold_met_race_swallowed"
        return self._emit(
            cvi,
            zone,
            CVIState.INSIDE_ZONE,
            CVIState.VIOLATED,
            reason,
            violation_id=result.violation_id,
            cycle_id=cycle,
        )

    # ----------------------------------------------------------- emit
    def _emit(
        self,
        cvi: CVI,
        zone: ZoneEntry,
        from_state: CVIState,
        to_state: CVIState,
        reason: str,
        *,
        violation_id: int | None = None,
        cycle_id: int | None = None,
    ) -> TransitionEvent:
        STATE_TRANSITIONS.labels(
            from_state=from_state.value, to_state=to_state.value
        ).inc()
        logger.debug(
            "state_transition",
            cvi_id=str(cvi.cvi_id),
            camera=cvi.camera_id,
            zone=zone.id,
            from_state=from_state.value,
            to_state=to_state.value,
            reason=reason,
        )
        return TransitionEvent(
            cvi_id=str(cvi.cvi_id),
            zone_id=zone.id,
            from_state=from_state,
            to_state=to_state,
            reason=reason,
            violation_id=violation_id,
            cycle_id=cycle_id,
        )


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


__all__ = ["StateMachine", "TransitionEvent"]
