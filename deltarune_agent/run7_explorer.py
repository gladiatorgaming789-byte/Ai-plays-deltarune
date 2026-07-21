from __future__ import annotations

from pathlib import Path

from .aligned_room_view import AlignedRoomViewMemory
from .policy import DIRECTION_VECTORS
from .run4_explorer import Run4Explorer
from .run6_explorer import (
    ENTRY_ESCAPE_RADIUS_CELLS,
    Run6Explorer,
)
from .telemetry import TelemetrySample
from .world_model import Warp


ENTRY_ESCAPE_MAX_ATTEMPTS = 4


class Run7Explorer(Run6Explorer):
    """Fixes learned from the first two run-six implementation trials.

    The arrival escape action previously bypassed the normal exploration loop.
    Navigation time therefore never advanced and collision learning never got a
    chance to reject a bad escape direction. Saved ``room_entry_from`` data also
    acted like current-session context and penalized the bedroom exit at the
    beginning of the next process. This layer keeps both safeguards bounded and
    lets the ordinary movement learner observe every attempted step.
    """

    def __init__(self, seed: int = 0, memory_path: Path | None = None):
        super().__init__(seed, memory_path)

        # Which room was entered from is useful only within the current process.
        # Persisted portals remain authoritative map knowledge, but a new run may
        # start in any room and must not inherit an imaginary recent entrance.
        self.cleared_persistent_room_entries = len(self.room_entry_from)
        self.room_entry_from.clear()
        self.world.room_entry_from.clear()

        self.entry_escape_attempts: dict[str, int] = {}
        self.entry_escape_abandons = 0
        self.only_exit_return_warps_used = 0

        if memory_path is not None:
            self.room_view = AlignedRoomViewMemory(
                memory_path.parent / "room_views"
            )
            if self.room_view.load_warning:
                self.memory_warning = " ".join(
                    warning
                    for warning in (
                        self.memory_warning,
                        self.room_view.load_warning,
                    )
                    if warning
                )

    def _observe_room(self, telemetry: TelemetrySample) -> None:
        previous_room = self.observed_room
        super()._observe_room(telemetry)
        room = self._room_key(telemetry)
        if previous_room is not None and room != previous_room:
            self.entry_escape_attempts[room] = 0

    def _entry_escape_plan(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> tuple[str, int, str] | None:
        guard = self.entry_escape.get(room)
        if guard is None:
            return None
        direction, arrival, expires_at = guard
        distance = max(
            abs(cell[0] - arrival[0]),
            abs(cell[1] - arrival[1]),
        )
        attempts = self.entry_escape_attempts.get(room, 0)
        if (
            self.navigation_tick >= expires_at
            or distance > ENTRY_ESCAPE_RADIUS_CELLS
            or attempts >= ENTRY_ESCAPE_MAX_ATTEMPTS
            or direction not in DIRECTION_VECTORS
            or self._blocked_near(room, cell, direction)
        ):
            self.entry_escape.pop(room, None)
            self.entry_escape_attempts.pop(room, None)
            if attempts >= ENTRY_ESCAPE_MAX_ATTEMPTS:
                self.entry_escape_abandons += 1
            return None

        self.entry_escape_attempts[room] = attempts + 1
        self.entry_escape_moves += 1
        return (
            direction,
            1,
            "clear arrival portal before replanning; bounded move "
            f"{direction} into {room} ({attempts + 1}/"
            f"{ENTRY_ESCAPE_MAX_ATTEMPTS})",
        )

    def _plan_exploration(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> tuple[str, int, str]:
        escape = self._entry_escape_plan(room, cell)
        if escape is not None:
            return escape
        return super()._plan_exploration(room, cell)

    def choose(self, observation, perception, telemetry=None):
        # Skip Run6Explorer.choose's pre-planner early return. Run4's normal
        # choose path still dispatches to every Run6/Run7 override dynamically,
        # including transition correction and story/choice semantics.
        return Run4Explorer.choose(self, observation, perception, telemetry)

    def _warp_is_priority_candidate(self, warp: Warp) -> bool:
        if super()._warp_is_priority_candidate(warp):
            return True

        source_room, _x, _y, _action, target_room, _tx, _ty = warp
        role = self._portal_role(warp)
        if role not in {"return/backtrack", "likely_optional"}:
            return False
        if self.room_entry_from.get(source_room) == target_room:
            return False
        if self._link_is_cooling_down(source_room, target_room):
            return False

        alternatives = {
            candidate[4]
            for candidate, _crossings in self._reliable_warps()
            if candidate[0] == source_room
            and candidate[4] != target_room
            and self._portal_role(candidate)
            not in {"return/backtrack", "loop_suppressed"}
        }
        allowed = not alternatives
        if allowed:
            self.only_exit_return_warps_used += 1
        return allowed

    def summary(self) -> dict:
        summary = super().summary()
        summary["cleared_persistent_room_entries"] = (
            self.cleared_persistent_room_entries
        )
        summary["entry_escape_abandons"] = self.entry_escape_abandons
        summary["active_entry_escapes"] = len(self.entry_escape)
        summary["only_exit_return_warps_used"] = (
            self.only_exit_return_warps_used
        )
        return summary
