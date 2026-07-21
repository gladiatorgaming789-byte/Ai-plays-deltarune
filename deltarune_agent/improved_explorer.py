from __future__ import annotations

from pathlib import Path

from .policy import (
    BACKTRACK_WARP_RADIUS,
    DIRECTION_VECTORS,
    StarterPolicy,
)
from .telemetry import TelemetrySample
from .world_model import Warp


ROOM_ENTRY_WARP_COOLDOWN_STEPS = 120


class ImprovedExplorer(StarterPolicy):
    """StarterPolicy with stricter warp reliability and backtrack control.

    The first recorded run exposed two failure modes in the original warp
    planner: it preferred low-count warp samples and it returned through the
    entry doorway while reachable local frontiers still existed. This subclass
    fixes those behaviors without rewriting the proven mapping code.
    """

    def __init__(
        self,
        seed: int = 0,
        memory_path: Path | None = None,
    ):
        super().__init__(seed, memory_path)
        self.room_entered_at: dict[str, int] = {}
        unreliable = [
            warp
            for warp, crossings in list(self.warps.items())
            if not self._warp_is_reliable(warp, crossings)
        ]
        for warp in unreliable:
            self.warps.pop(warp, None)
        self.pruned_unreliable_warps = len(unreliable)

    def _observe_room(self, telemetry: TelemetrySample) -> None:
        room = self._room_key(telemetry)
        previous_room = self.observed_room
        super()._observe_room(telemetry)
        if previous_room is not None and room != previous_room:
            self.room_entered_at[room] = self.navigation_tick
            self.steps_without_frontier = 0
            self.stalled_recovery_steps = 0
            self.exit_search_goal = None
            self.visual_goal = None

    def _nearby_warp_strength(
        self,
        warp: Warp,
    ) -> int:
        (
            source_room,
            source_x,
            source_y,
            _action,
            target_room,
            _target_x,
            _target_y,
        ) = warp
        return max(
            (
                crossings
                for candidate, crossings in self.warps.items()
                if candidate[0] == source_room
                and candidate[4] == target_room
                and max(
                    abs(candidate[1] - source_x),
                    abs(candidate[2] - source_y),
                )
                <= BACKTRACK_WARP_RADIUS
            ),
            default=0,
        )

    def _warp_is_reliable(
        self,
        warp: Warp,
        crossings: int,
    ) -> bool:
        strongest = self._nearby_warp_strength(warp)
        return not (
            strongest >= 3
            and crossings * 2 < strongest
        )

    def _reliable_warps(self):
        for warp, crossings in self.warps.items():
            if self._warp_is_reliable(warp, crossings):
                yield warp, crossings

    def _known_warp_direction(
        self,
        room: str,
        cell: tuple[int, int],
        direction: str,
    ) -> bool:
        return any(
            source_room == room
            and (source_x, source_y) == cell
            and action == direction
            for (
                source_room,
                source_x,
                source_y,
                action,
                _target_room,
                _target_x,
                _target_y,
            ), _crossings in self._reliable_warps()
        )

    def _known_warp_endpoint(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> bool:
        return any(
            source_room == room
            and (source_x, source_y) == cell
            for (
                source_room,
                source_x,
                source_y,
                _action,
                _target_room,
                _target_x,
                _target_y,
            ), _crossings in self._reliable_warps()
        )

    def _is_entry_warp_direction(
        self,
        room: str,
        cell: tuple[int, int],
        direction: str,
    ) -> bool:
        entry_room = self.room_entry_from.get(room)
        vector_x, vector_y = DIRECTION_VECTORS[direction]
        next_cell = (
            cell[0] + vector_x,
            cell[1] + vector_y,
        )
        for (
            source_room,
            source_x,
            source_y,
            action,
            target_room,
            _target_x,
            _target_y,
        ), _crossings in self._reliable_warps():
            if source_room != room or not (
                target_room == entry_room
                or frozenset((room, target_room))
                in self.suppressed_room_links
            ):
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

    def _route_to_learned_warp(
        self,
        room: str,
        start: tuple[int, int],
    ) -> tuple[str, Warp] | None:
        """Prefer reliable exits and never abandon reachable local frontiers."""
        current_frontier = any(
            self._direction_is_unexplored(
                room,
                start,
                direction,
            )
            for direction in DIRECTION_VECTORS
        )
        frontier_route = self._route_to_nearest_frontier(
            room,
            start,
        )
        room_age = (
            self.navigation_tick
            - self.room_entered_at.get(room, self.navigation_tick)
        )

        forward_adjacency = self._adjacency(room)
        all_adjacency = self._adjacency(
            room,
            avoid_backtrack=False,
        )
        candidates: list[
            tuple[
                tuple[int, int, int, int, int],
                str,
                Warp,
            ]
        ] = []

        for warp, crossings in self._reliable_warps():
            (
                source_room,
                source_x,
                source_y,
                action,
                target_room,
                _target_x,
                _target_y,
            ) = warp
            if (
                source_room != room
                or action not in DIRECTION_VECTORS
            ):
                continue

            link = frozenset((room, target_room))
            is_entry = self.room_entry_from.get(room) == target_room
            is_suppressed = link in self.suppressed_room_links
            is_backtrack = is_entry or is_suppressed

            if is_backtrack and (
                room_age < ROOM_ENTRY_WARP_COOLDOWN_STEPS
                or current_frontier
                or frontier_route is not None
            ):
                continue

            link_penalty = (
                2 if is_suppressed else int(is_entry)
            )
            adjacency = (
                all_adjacency
                if link_penalty
                else forward_adjacency
            )
            source = (source_x, source_y)
            if source == start:
                if self._blocked_near(room, start, action):
                    continue
                first_direction = action
                distance = 0
                route_quality = 0
            else:
                route = self._route_to_target(
                    adjacency,
                    start,
                    source,
                )
                if route is None:
                    route = self._route_to_region_target(
                        room,
                        start,
                        source,
                        allow_backtrack=bool(link_penalty),
                    )
                    if route is None:
                        continue
                    route_quality = 1
                else:
                    route_quality = 0
                first_direction, distance = route

            target_regions = len(
                {
                    (region_x, region_y)
                    for (
                        seen_room,
                        region_x,
                        region_y,
                    ) in self.seen_regions
                    if seen_room == target_room
                }
                | {
                    self._region((seen_x, seen_y))
                    for (
                        seen_room,
                        seen_x,
                        seen_y,
                    ) in self.seen_cells
                    if seen_room == target_room
                }
            )
            score = (
                link_penalty,
                target_regions,
                -crossings,
                route_quality,
                distance,
            )
            candidates.append(
                (score, first_direction, warp)
            )

        if not candidates:
            return None
        _score, direction, warp = min(
            candidates,
            key=lambda candidate: candidate[0],
        )
        return direction, warp

    def summary(self) -> dict:
        summary = super().summary()
        summary["pruned_unreliable_warps"] = (
            self.pruned_unreliable_warps
        )
        return summary
