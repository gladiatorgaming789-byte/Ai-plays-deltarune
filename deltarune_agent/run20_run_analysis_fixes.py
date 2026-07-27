from __future__ import annotations

from pathlib import Path

from .policy import DIRECTION_VECTORS, MOVEMENT_COMMIT_STEPS, OPPOSITE
from .run16_warp_guard import Run16GuardedExplorer
from .run18_reinforcement_accounting import Run18ReinforcementExplorer
from .telemetry import TelemetrySample


POST_ENTRY_ESCAPE_COMMIT_STEPS = 3
POST_ENTRY_ESCAPE_RADIUS = 3
MAX_UNTESTED_DOORWAY_SELECTIONS = 6
MIN_ACTIONABLE_INTERACTABLE_SIDES = 2
PORTAL_PROGRESS_ATTRIBUTION_STEPS = 24
AUTOMATIC_PROGRESS_EVENTS = {
    "automatic scripted sequence",
    "automatic dialogue",
}


class Run20RunAnalysisExplorer(Run18ReinforcementExplorer):
    """Fix failures observed throughout the first cleaned-code run.

    The rules in this layer use only observed transitions, learned geometry,
    visual evidence, and outcomes. No room name, route, NPC, or story solution is
    encoded here.
    """

    def __init__(self, seed: int = 0, memory_path: Path | None = None):
        super().__init__(seed, memory_path)
        self._pending_entry_escape: tuple[str, str, tuple[int, int], str] | None = None
        self.post_entry_escape_plans = 0
        self.frontier_first_steps = 0
        self.unreachable_doorways_retired = 0
        self.single_side_interactable_routes_suppressed = 0
        self.automatic_reward_events_suppressed = 0
        self.direct_room_discovery_rewards = 0

    def _observe_room(self, telemetry: TelemetrySample) -> None:
        previous_room = self.observed_room
        transition_direction = (
            self.last_movement
            or self.last_overworld_movement
            or (
                telemetry.transition_from_facing
                if telemetry.transition_from_facing in DIRECTION_VECTORS
                else None
            )
        )
        super()._observe_room(telemetry)
        room = self._room_key(telemetry)
        if previous_room is None or previous_room == room:
            return

        arrival = self._cell(telemetry)
        escape = self._derive_post_entry_escape(
            room,
            previous_room,
            arrival,
            transition_direction,
        )
        if escape is not None:
            self._pending_entry_escape = (room, escape, arrival, previous_room)

    def _derive_post_entry_escape(
        self,
        room: str,
        previous_room: str,
        arrival: tuple[int, int],
        transition_direction: str | None,
    ) -> str | None:
        """Choose movement away from the observed return aperture.

        Once the reverse portal has been observed, its action points back through
        the doorway, so the safe escape is its opposite. On a first visit, the
        movement that caused the transition is the best observed continuation.
        """
        reverse_candidates = []
        for warp, crossings in self.warps.items():
            source_room, source_x, source_y, action, target_room, _tx, _ty = warp
            if (
                source_room != room
                or target_room != previous_room
                or action not in DIRECTION_VECTORS
            ):
                continue
            distance = max(abs(source_x - arrival[0]), abs(source_y - arrival[1]))
            reverse_candidates.append((distance, -int(crossings), action))

        preferred: list[str] = []
        if reverse_candidates:
            _distance, _crossings, return_action = min(reverse_candidates)
            preferred.append(OPPOSITE[return_action])
        if transition_direction in DIRECTION_VECTORS:
            preferred.append(str(transition_direction))
        preferred.extend(("down", "right", "left", "up"))

        seen: set[str] = set()
        candidates = []
        for direction in preferred:
            if direction in seen:
                continue
            seen.add(direction)
            if self._blocked_near(room, arrival, direction):
                continue
            if self._is_entry_warp_direction(room, arrival, direction):
                continue
            dx, dy = DIRECTION_VECTORS[direction]
            target = self._known_open_neighbor(room, arrival, direction) or (
                arrival[0] + dx,
                arrival[1] + dy,
            )
            candidates.append(
                (
                    0 if self._known_open_neighbor(room, arrival, direction) else 1,
                    self.visits[(room, *target)],
                    self._recent_cell_cost(room, target),
                    direction,
                )
            )
        return min(candidates)[-1] if candidates else None

    def _retire_run20_visual_leads(self, room: str) -> None:
        for key, record in list(self.screen_regions.items()):
            if key[0] != room:
                continue
            state = str(record.get("guess_state") or "proposed")
            if state in {"confirmed", "rejected", "retired"}:
                continue
            if not self._is_doorway_facade(record):
                continue
            if record.get("path_continuation"):
                continue
            if int(record.get("completed_tests", record.get("inspections", 0)) or 0):
                continue
            if int(record.get("approach_attempts", 0) or 0) < MAX_UNTESTED_DOORWAY_SELECTIONS:
                continue
            if int(record.get("doorway_story_retry_epoch", -1) or -1) == self.story_epoch:
                continue
            record["story_sensitive_doorway"] = True
            record["doorway_failed_story_epoch"] = self.story_epoch
            self._retire_visual_lead(
                key,
                record,
                "structured doorway was reselected repeatedly without a reachable concrete test",
            )
            self.unreachable_doorways_retired += 1

    def _plan_exploration(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> tuple[str, int, str]:
        self._retire_run20_visual_leads(room)

        pending = self._pending_entry_escape
        if pending is not None:
            pending_room, direction, arrival, previous_room = pending
            distance = max(abs(cell[0] - arrival[0]), abs(cell[1] - arrival[1]))
            self._pending_entry_escape = None
            if (
                room == pending_room
                and distance <= POST_ENTRY_ESCAPE_RADIUS
                and not self._blocked_near(room, cell, direction)
                and not self._is_entry_warp_direction(room, cell, direction)
            ):
                self.post_entry_escape_plans += 1
                return (
                    direction,
                    POST_ENTRY_ESCAPE_COMMIT_STEPS,
                    f"move {direction} away from observed return portal to {previous_room}",
                )

        # In a scrolling room, reachable movement evidence is stronger than a
        # speculative visual target. Finish those frontiers before selecting a
        # remembered doorway, seam, or object.
        if self._room_is_long_scrolling(room) and self._has_reachable_frontier(
            room,
            cell,
        ):
            current_frontier = any(
                self._direction_is_unexplored(room, cell, direction)
                for direction in DIRECTION_VECTORS
            )
            direction = (
                self._least_visited_direction(room, cell, self.direction)
                if current_frontier
                else self._route_to_nearest_frontier(room, cell)
            )
            if direction is not None:
                self.frontier_first_steps += 1
                return (
                    direction,
                    MOVEMENT_COMMIT_STEPS if current_frontier else 1,
                    "long room: exhaust reachable learned frontier before visual speculation",
                )

        return super()._plan_exploration(room, cell)

    def _direction_to_visual_hypothesis(
        self,
        room: str,
        cell: tuple[int, int],
        *,
        story_focus: bool = False,
        allowed_hypotheses: set[str] | None = None,
    ):
        self._retire_run20_visual_leads(room)
        suppressed: list[tuple[dict[str, object], object]] = []
        for key, record in self.screen_regions.items():
            if key[0] != room or record.get("hypothesis") != "possible_interactable":
                continue
            if record.get("choice_retry") or self._region_has_useful_interaction(
                room,
                key[1],
                key[2],
            ):
                continue
            if int(record.get("entity_approach_directions", 0) or 0) >= MIN_ACTIONABLE_INTERACTABLE_SIDES:
                continue
            suppressed.append((record, record.get("hypothesis")))
            record["hypothesis"] = None
            if self.visual_goal == key:
                self.visual_goal = None
                self.decision_visual_goal = None
            self.single_side_interactable_routes_suppressed += 1
        try:
            return super()._direction_to_visual_hypothesis(
                room,
                cell,
                story_focus=story_focus,
                allowed_hypotheses=allowed_hypotheses,
            )
        finally:
            for record, hypothesis in suppressed:
                record["hypothesis"] = hypothesis

    def _visual_hypothesis_priority(
        self,
        record: dict[str, object],
        key: tuple[str, int, int],
        current_region: tuple[int, int],
        story_focus: bool,
    ) -> tuple:
        base = super()._visual_hypothesis_priority(
            record,
            key,
            current_region,
            story_focus,
        )
        evidence_rank = (
            0
            if self._is_doorway_facade(record)
            else 1
            if record.get("path_continuation")
            else 2
        )
        return (
            base[0],
            evidence_rank,
            int(record.get("failed_approaches", 0) or 0),
            min(9, int(record.get("approach_attempts", 0) or 0)),
            *base[1:],
        )

    def _portal_is_recent_selected_action(
        self,
        portal_id: str | None,
        telemetry: TelemetrySample | None,
    ) -> bool:
        if portal_id is None:
            return False
        portal = self.world.portal_metadata(portal_id) or {}
        action = str(portal.get("action") or "")
        if action not in DIRECTION_VECTORS:
            return False
        if str(portal.get("role") or "") == "automatic_sequence":
            return False
        if self.navigation_tick - self.last_portal_crossing_tick > PORTAL_PROGRESS_ATTRIBUTION_STEPS:
            return False
        if telemetry is None:
            return True
        return str(portal.get("to_room") or "") == self._room_key(telemetry)

    def _record_story_progress(self, event: str, telemetry: TelemetrySample | None) -> None:
        portal_id = self.last_portal_id

        if event in AUTOMATIC_PROGRESS_EVENTS:
            # Automatic state changes are useful progress observations, but they
            # were not an action selected by the agent. Preserve progress memory
            # while bypassing reinforcement and portal credit.
            self.last_portal_id = None
            try:
                Run16GuardedExplorer._record_story_progress(self, event, telemetry)
            finally:
                self.last_portal_id = portal_id
            self.automatic_reward_events_suppressed += 1
            return

        if event == "discovered a new room":
            # Credit only the observed portal that directly produced the room
            # transition. Do not reward every stale action left in the trace.
            Run16GuardedExplorer._record_story_progress(self, event, telemetry)
            if self._portal_is_recent_selected_action(portal_id, telemetry):
                portal = self.world.portal_metadata(portal_id) or {}
                self._reward_key(
                    self._portal_key(str(portal_id)),
                    "room_discovery",
                    event=event,
                    kind="portal",
                    context={
                        "from_room": portal.get("from_room"),
                        "to_room": portal.get("to_room"),
                        "action": portal.get("action"),
                    },
                )
                self.direct_room_discovery_rewards += 1
            return

        eligible_portal = self._portal_is_recent_selected_action(portal_id, telemetry)
        if not eligible_portal:
            self.last_portal_id = None
        try:
            super()._record_story_progress(event, telemetry)
        finally:
            self.last_portal_id = portal_id

    def summary(self) -> dict:
        summary = super().summary()
        summary.update(
            {
                "post_entry_escape_plans": self.post_entry_escape_plans,
                "frontier_first_steps": self.frontier_first_steps,
                "unreachable_doorways_retired": self.unreachable_doorways_retired,
                "single_side_interactable_routes_suppressed": self.single_side_interactable_routes_suppressed,
                "automatic_reward_events_suppressed": self.automatic_reward_events_suppressed,
                "direct_room_discovery_rewards": self.direct_room_discovery_rewards,
            }
        )
        return summary
