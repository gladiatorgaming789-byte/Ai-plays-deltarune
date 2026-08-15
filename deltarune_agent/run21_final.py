from __future__ import annotations

from pathlib import Path

from .entity_detection_v2 import single_side_entity_candidate
from .policy import BACKTRACK_WARP_RADIUS, DIRECTION_VECTORS
from .run21_link_cooldowns import Run21CooldownExplorer
from .warp_classification_v2 import _room_completion_pressure


_ROUTE_ONLY_FAILURE_MARKERS = (
    "route",
    "no safe learned approach",
    "loop",
    "made no progress",
)


class Run21Explorer(Run21CooldownExplorer):
    """Final Run21 migration policy after the eight-run calibration."""

    def __init__(self, seed: int = 0, memory_path: Path | None = None):
        super().__init__(seed, memory_path)
        self.reopened_legacy_route_only_entity_candidates = 0
        self.recovery_suppressed_link_approach_overrides = 0
        self._recovery_suppressed_link_override_ticks: set[
            tuple[str, int, int, str]
        ] = set()
        self._reopen_legacy_route_only_entity_candidates()

    def _reopen_legacy_route_only_entity_candidates(self) -> None:
        """Do not confuse an old routing failure with a failed interaction test."""
        for key, record in self.screen_regions.items():
            if not single_side_entity_candidate(record):
                continue
            if int(record.get("completed_tests", record.get("inspections", 0)) or 0) > 0:
                continue
            if int(record.get("failed_approaches", 0) or 0) <= 0:
                continue
            reason = str(record.get("last_failure_reason") or "").casefold()
            if not any(marker in reason for marker in _ROUTE_ONLY_FAILURE_MARKERS):
                continue

            record["failed_approaches"] = 0
            if str(record.get("guess_state") or "") in {"rejected", "retired"}:
                record["guess_state"] = "cooldown"
            record["entity_route_retry_migrated"] = True
            record["entity_route_retry_reason"] = (
                "legacy route-only failure reopened for one bounded concrete test"
            )
            self._refresh_visual_guess_metadata((key[1], key[2]), record)
            self.map_updates.append(self._screen_region_map_update(key, record))
            self.reopened_legacy_route_only_entity_candidates += 1

    def _weak_entity_probe_count(self, room: str) -> int:
        # Per-candidate lifecycle rules already bound cost: one-sided candidates
        # are nearby-only, approach-bounded, and rejected by a concrete no-response
        # test. A lifetime room cap could permanently skip a valid candidate just
        # because several scenery candidates were encountered first.
        return 0

    def _entry_direction_guard_active(
        self,
        room: str,
        cell: tuple[int, int],
        direction: str,
    ) -> bool:
        guard = getattr(self, "entry_direction_guards", {}).get(room)
        if guard is None:
            return False
        guarded_direction, arrival, expires_at = guard
        if self.navigation_tick >= expires_at:
            return False
        return (
            direction == guarded_direction
            and max(abs(cell[0] - arrival[0]), abs(cell[1] - arrival[1])) <= 2
        )

    def _pressure_recovery_warp_approach(
        self,
        room: str,
        cell: tuple[int, int],
        direction: str,
    ) -> bool:
        if direction not in DIRECTION_VECTORS:
            return False
        try:
            if not _room_completion_pressure(self, room):
                return False
        except AttributeError:
            return False

        dx, dy = DIRECTION_VECTORS[direction]
        next_cell = (cell[0] + dx, cell[1] + dy)
        entry_room = self.room_entry_from.get(room)
        for (
            source_room,
            source_x,
            source_y,
            action,
            target_room,
            _target_x,
            _target_y,
        ) in self.warps:
            if source_room != room or action not in DIRECTION_VECTORS:
                continue
            link = frozenset((room, target_room))
            if not (
                target_room == entry_room
                or link in self.suppressed_room_links
            ):
                continue
            # Run21's new short repeated-link hold remains authoritative even
            # when older blanket/suppression rules are relaxed for recovery.
            if self._run21_link_hold_active(room, target_room):
                continue
            source = (source_x, source_y)
            if source == cell and action == direction:
                return True
            current_distance = max(
                abs(cell[0] - source_x),
                abs(cell[1] - source_y),
            )
            next_distance = max(
                abs(next_cell[0] - source_x),
                abs(next_cell[1] - source_y),
            )
            if (
                next_distance < current_distance
                and next_distance <= BACKTRACK_WARP_RADIUS
            ):
                return True
        return False

    def _is_entry_warp_direction(
        self,
        room: str,
        cell: tuple[int, int],
        direction: str,
    ) -> bool:
        guarded = super()._is_entry_warp_direction(room, cell, direction)
        if not guarded:
            return False

        # The very short same-direction post-entry guard prevents held input
        # from carrying straight back through a doorway. Never override it.
        if self._entry_direction_guard_active(room, cell, direction):
            return True

        if not self._pressure_recovery_warp_approach(room, cell, direction):
            return True

        key = (room, cell[0], cell[1], direction)
        if key not in self._recovery_suppressed_link_override_ticks:
            self._recovery_suppressed_link_override_ticks.add(key)
            self.recovery_suppressed_link_approach_overrides += 1
        return False

    def summary(self) -> dict:
        summary = super().summary()
        summary["reopened_legacy_route_only_entity_candidates"] = (
            self.reopened_legacy_route_only_entity_candidates
        )
        summary["recovery_suppressed_link_approach_overrides"] = (
            self.recovery_suppressed_link_approach_overrides
        )
        return summary


__all__ = ["Run21Explorer"]
