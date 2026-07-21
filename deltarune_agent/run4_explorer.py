from __future__ import annotations

from pathlib import Path

from .run3_explorer import Run3Explorer
from .world_model import Edge, Warp


ROOM_EXIT_PRIORITY_STEPS = 240
ROOM_EXIT_PRIORITY_STORY_STALL = 180
ROOM_EXIT_PRIORITY_MIN_CELLS = 24
MAX_FLAVOR_INTERACTIONS_BEFORE_EXIT = 2
EXIT_PRIORITY_COMMIT_STEPS = 2
EXIT_PRIORITY_EPISODE_STEPS = 72
EXIT_PRIORITY_COOLDOWN_STEPS = 180
EXIT_PRIORITY_FAILURE_LIMIT = 2
STRONG_GEOMETRY_APPROACH_CELLS = 2
STRONG_VISUAL_EXIT_CONFIDENCE = 0.50
STRONG_VISUAL_OPENING_SCORE = 0.52
MAX_VISUAL_OPENING_WIDTH = 0.62
DEPRIORITIZED_WARP_ROLES = {
    "likely_optional",
    "return/backtrack",
    "loop_suppressed",
}
WARP_ROLE_PRIORITY = {
    "progression": 0,
    "new_area": 1,
    "unknown": 2,
}


class Run4Explorer(Run3Explorer):
    """Explorer fixes learned from the fourth recorded playthrough.

    The run eventually reached Toriel's house, but spent 1,155 actions in
    Kris's bedroom and another 672 actions testing kitchen and living-room
    scenery. Once a room is well sampled, genuine room-edge exit evidence must
    outrank weak character guesses and exhaustive furniture inspection.
    """

    def __init__(self, seed: int = 0, memory_path: Path | None = None):
        super().__init__(seed, memory_path)
        self.exit_priority_activations = 0
        self.prioritized_exit_steps = 0
        self.retired_weak_character_leads = 0
        self.max_room_navigation_age = 0
        self.exit_priority_started_at: dict[str, int] = {}
        self.exit_priority_cooldown_until: dict[str, int] = {}
        self.exit_priority_timeouts = 0
        self.exit_priority_no_route_cooldowns = 0
        self.priority_known_warp_steps = 0
        self.priority_geometry_steps = 0
        self.priority_visual_steps = 0

    def _room_navigation_age(self, room: str) -> int:
        entered_at = self.room_entered_at.get(room)
        if entered_at is None:
            return 0
        return max(0, self.navigation_tick - entered_at)

    def _room_seen_cell_count(self, room: str) -> int:
        return sum(
            seen_room == room
            for seen_room, _x, _y in self.seen_cells
        )

    def _room_flavor_count(self, room: str) -> int:
        return sum(
            key[0] == room
            and str(record.get("usefulness") or "") == "flavor"
            for key, record in self.interactables.items()
        )

    def _portal_role(self, warp: Warp) -> str:
        metadata = self.world.portal_metadata(warp)
        return str(metadata.get("role") or "unknown") if metadata else "unknown"

    def _warp_is_priority_candidate(self, warp: Warp) -> bool:
        source_room, _x, _y, _action, target_room, _tx, _ty = warp
        if self._link_is_cooling_down(source_room, target_room):
            return False
        role = self._portal_role(warp)
        if (
            self.room_entry_from.get(source_room) == target_room
            and role != "progression"
        ):
            return False
        return role not in DEPRIORITIZED_WARP_ROLES

    def _has_preferred_learned_warp(self, room: str) -> bool:
        return any(
            warp[0] == room and self._warp_is_priority_candidate(warp)
            for warp, _crossings in self._reliable_warps()
        )

    def _visual_exit_is_actionable(
        self,
        key: tuple[str, int, int],
        record: dict[str, object],
    ) -> bool:
        if not self._visual_lead_is_actionable(
            key,
            record,
            hypotheses={"possible_exit"},
        ):
            return False
        if record.get("path_continuation"):
            return True
        confidence = float(record.get("guess_confidence", 0.0) or 0.0)
        opening_score = float(record.get("edge_opening_score", 0.0) or 0.0)
        opening_width = float(record.get("edge_width_ratio", 0.0) or 0.0)
        return (
            confidence >= STRONG_VISUAL_EXIT_CONFIDENCE
            and opening_score >= STRONG_VISUAL_OPENING_SCORE
            and (opening_width <= 0.0 or opening_width <= MAX_VISUAL_OPENING_WIDTH)
        )

    def _strong_geometry_exit_probes(self, room: str) -> list[Edge]:
        adjacency = self._adjacency(room)
        candidates: list[Edge] = []
        for probe in self._possible_exit_probes(room):
            _probe_room, x, y, direction = probe
            cell = (x, y)
            path_degree = len(adjacency.get(cell, []))
            straight_approach = self._straight_approach_length(
                room,
                cell,
                direction,
            )
            if (
                path_degree <= 2
                and straight_approach >= STRONG_GEOMETRY_APPROACH_CELLS
            ):
                candidates.append(probe)
        return candidates

    def _has_exit_lead(self, room: str) -> bool:
        if self._has_preferred_learned_warp(room):
            return True
        if self._strong_geometry_exit_probes(room):
            return True
        return any(
            key[0] == room
            and self._visual_exit_is_actionable(key, record)
            for key, record in self.screen_regions.items()
        )

    def _exit_priority_active(self, room: str) -> bool:
        cooldown_until = self.exit_priority_cooldown_until.get(room, 0)
        if self.navigation_tick < cooldown_until:
            return False
        if not self._has_exit_lead(room):
            self.exit_priority_started_at.pop(room, None)
            return False
        room_age = self._room_navigation_age(room)
        seen_cells = self._room_seen_cell_count(room)
        flavor_count = self._room_flavor_count(room)
        triggered = (
            flavor_count >= MAX_FLAVOR_INTERACTIONS_BEFORE_EXIT
            or self.story_stall_steps >= ROOM_EXIT_PRIORITY_STORY_STALL
            or (
                room_age >= ROOM_EXIT_PRIORITY_STEPS
                and seen_cells >= ROOM_EXIT_PRIORITY_MIN_CELLS
            )
        )
        if not triggered:
            return False

        entered_at = self.room_entered_at.get(room, 0)
        started_at = self.exit_priority_started_at.get(room)
        if started_at is not None and started_at < entered_at:
            self.exit_priority_started_at.pop(room, None)
            started_at = None
        if started_at is None:
            self.exit_priority_started_at[room] = self.navigation_tick
            self.exit_priority_activations += 1
            return True
        if self.navigation_tick - started_at < EXIT_PRIORITY_EPISODE_STEPS:
            return True

        self._cool_down_exit_priority(
            room,
            reason="exit-priority search window expired without a transition",
            penalize_visual_goal=True,
        )
        self.exit_priority_timeouts += 1
        return False

    def _region_has_useful_interaction(
        self,
        room: str,
        region_x: int,
        region_y: int,
    ) -> bool:
        for key, record in self.interactables.items():
            if key[0] != room:
                continue
            if self._region((key[1], key[2])) != (region_x, region_y):
                continue
            if str(record.get("usefulness") or "") in {
                "choice_pending",
                "progress",
            }:
                return True
        return False

    def _retire_weak_character_hypotheses(self, room: str) -> None:
        """Stop revisiting scenery after stronger exit evidence exists."""
        for key, record in self.screen_regions.items():
            if key[0] != room or record.get("hypothesis") != "possible_character":
                continue
            if record.get("choice_retry") or self._region_has_useful_interaction(
                room,
                key[1],
                key[2],
            ):
                continue
            inspections = int(record.get("inspections", 0))
            completed_tests = int(
                record.get("completed_tests", inspections) or 0
            )
            failed_approaches = int(record.get("failed_approaches", 0) or 0)
            approaches = int(record.get("entity_approach_directions", 0))
            obstruction_targets = int(record.get("obstruction_target_cells", 99))
            weak = (
                completed_tests >= 2
                or failed_approaches >= 2
                or approaches < 2
                or obstruction_targets > 4
            )
            if not weak:
                continue
            record["hypothesis"] = None
            record["inspections"] = max(3, inspections)
            record["completed_tests"] = max(
                2,
                int(record.get("completed_tests", 0)),
            )
            record["guess_state"] = "retired"
            record["retired_reason"] = "exit evidence outranked weak scenery lead"
            self.retired_weak_character_leads += 1
            self.map_updates.append(
                {
                    "type": "screen_region",
                    "room": room,
                    "region": [key[1], key[2]],
                    "views": int(record.get("views", 1)),
                    "interest": round(float(record.get("interest", 0.0)), 3),
                    "hypothesis": None,
                    "inspections": int(record["inspections"]),
                    "completed_tests": int(record["completed_tests"]),
                    "guess_state": "retired",
                    "retired_reason": record["retired_reason"],
                }
            )

    def _cool_down_exit_priority(
        self,
        room: str,
        *,
        reason: str,
        penalize_visual_goal: bool,
    ) -> None:
        self.exit_priority_started_at.pop(room, None)
        cooldown_until = self.navigation_tick + EXIT_PRIORITY_COOLDOWN_STEPS
        self.exit_priority_cooldown_until[room] = cooldown_until
        self.exit_search_goal = None

        goal = self.visual_goal
        if (
            penalize_visual_goal
            and goal is not None
            and goal[0] == room
            and goal in self.screen_regions
        ):
            record = self.screen_regions[goal]
            if record.get("hypothesis") == "possible_exit":
                failures = int(record.get("failed_approaches", 0)) + 1
                record["failed_approaches"] = failures
                record["last_failure_reason"] = reason
                record["cooldown_until_tick"] = cooldown_until
                record["guess_state"] = (
                    "rejected"
                    if failures >= EXIT_PRIORITY_FAILURE_LIMIT
                    else "cooldown"
                )
                self._refresh_visual_guess_metadata(
                    (goal[1], goal[2]),
                    record,
                )
                self.map_updates.append(
                    self._screen_region_map_update(goal, record)
                )
        self.visual_goal = None
        self.decision_visual_goal = None
        self.visual_goal_age = 0
        self.visual_goal_stalls = 0
        self.visual_goal_best_distance = None

    def _route_to_priority_warp(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> tuple[str, Warp, str] | None:
        """Route to an observed non-return portal, ranked by learned outcome."""

        forward_adjacency = self._adjacency(room)
        candidates: list[
            tuple[tuple[int, int, int, int], str, Warp, str]
        ] = []
        for warp, crossings in self._reliable_warps():
            source_room, source_x, source_y, action, _target, _tx, _ty = warp
            if source_room != room or not self._warp_is_priority_candidate(warp):
                continue
            source = (source_x, source_y)
            if source == cell:
                if self._blocked_near(room, cell, action):
                    continue
                direction = action
                distance = 0
            else:
                route = self._route_to_target(forward_adjacency, cell, source)
                if route is None:
                    route = self._route_to_region_target(
                        room,
                        cell,
                        source,
                        allow_backtrack=False,
                    )
                if route is None:
                    continue
                direction, distance = route
            role = self._portal_role(warp)
            score = (
                WARP_ROLE_PRIORITY.get(role, WARP_ROLE_PRIORITY["unknown"]),
                distance,
                -crossings,
                source_x + source_y,
            )
            candidates.append((score, direction, warp, role))
        if not candidates:
            return None
        _score, direction, warp, role = min(candidates, key=lambda item: item[0])
        return direction, warp, role

    def _route_to_strong_geometry_exit(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> tuple[str, Edge] | None:
        adjacency = self._adjacency(room)
        candidates = self._strong_geometry_exit_probes(room)
        candidate_set = set(candidates)
        if self.exit_search_goal not in candidate_set:
            self.exit_search_goal = None
        if self.exit_search_goal is not None:
            goal = self.exit_search_goal
            if self._within_exit_probe_approach(cell, goal):
                return goal[3], goal
            route = self._route_to_exit_approach(adjacency, cell, goal)
            if route is not None:
                return route[0], goal
            self.exit_search_goal = None

        routes: list[tuple[tuple[int, int, int, int, str], str, Edge]] = []
        for probe in candidates:
            _probe_room, x, y, direction = probe
            goal_cell = (x, y)
            if self._within_exit_probe_approach(cell, probe):
                first_direction, distance = direction, 0
            else:
                route = self._route_to_exit_approach(adjacency, cell, probe)
                if route is None:
                    continue
                first_direction, distance = route
            routes.append(
                (
                    (
                        -self._straight_approach_length(room, goal_cell, direction),
                        len(adjacency.get(goal_cell, [])),
                        distance,
                        self.visits[(room, x, y)],
                        direction,
                    ),
                    first_direction,
                    probe,
                )
            )
        if not routes:
            return None
        _score, direction, probe = min(routes, key=lambda item: item[0])
        self.exit_search_goal = probe
        self._remember_path_continuation(probe)
        return direction, probe

    def _prioritized_exit_plan(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> tuple[str, int, str] | None:
        learned_warp = self._route_to_priority_warp(room, cell)
        if learned_warp is not None:
            direction, warp, role = learned_warp
            self.priority_known_warp_steps += 1
            return (
                direction,
                1,
                "room completion: follow observed "
                f"{role.replace('_', ' ')} warp to {warp[4]} via {direction} "
                f"from ({warp[1]},{warp[2]})",
            )

        exit_route = self._route_to_strong_geometry_exit(room, cell)
        if exit_route is not None:
            direction, probe = exit_route
            _probe_room, probe_x, probe_y, probe_direction = probe
            self.priority_geometry_steps += 1
            if self._within_exit_probe_approach(cell, probe):
                self.exit_probes[probe] += 1
                return (
                    probe_direction,
                    EXIT_PRIORITY_COMMIT_STEPS,
                    "room completion: commit to strong mapped passage "
                    f"{probe_direction} at ({probe_x},{probe_y})",
                )
            return (
                direction,
                1,
                "room completion: route to strong mapped passage "
                f"{probe_direction} at ({probe_x},{probe_y}) via {direction}",
            )

        actionable_visuals = {
            key
            for key, record in self.screen_regions.items()
            if key[0] == room and self._visual_exit_is_actionable(key, record)
        }
        if actionable_visuals:
            current_region = self._region(cell)
            self.visual_goal = min(
                actionable_visuals,
                key=lambda key: self._visual_hypothesis_priority(
                    self.screen_regions[key],
                    key,
                    current_region,
                    True,
                ),
            )
        visual_exit = (
            self._direction_to_visual_hypothesis(
                room,
                cell,
                story_focus=True,
                allowed_hypotheses={"possible_exit"},
            )
            if actionable_visuals
            else None
        )
        if (
            visual_exit is not None
            and self.decision_visual_goal in actionable_visuals
        ):
            direction, _hypothesis, target_region = visual_exit
            self.priority_visual_steps += 1
            return (
                direction,
                1,
                "room completion: inspect high-confidence visible passage "
                f"via {direction} toward region {target_region}",
            )
        if self.visual_goal not in actionable_visuals:
            self.visual_goal = None
            self.decision_visual_goal = None
        return None

    def _plan_exploration(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> tuple[str, int, str]:
        room_age = self._room_navigation_age(room)
        self.max_room_navigation_age = max(
            self.max_room_navigation_age,
            room_age,
        )
        if self._exit_priority_active(room):
            self._retire_weak_character_hypotheses(room)
            plan = self._prioritized_exit_plan(room, cell)
            if plan is not None:
                self.prioritized_exit_steps += 1
                return plan
            self._cool_down_exit_priority(
                room,
                reason="no reachable high-confidence exit route remained",
                penalize_visual_goal=False,
            )
            self.exit_priority_no_route_cooldowns += 1
        return super()._plan_exploration(room, cell)

    def summary(self) -> dict:
        summary = super().summary()
        summary["exit_priority_activations"] = self.exit_priority_activations
        summary["prioritized_exit_steps"] = self.prioritized_exit_steps
        summary["retired_weak_character_leads"] = self.retired_weak_character_leads
        summary["max_room_navigation_age"] = self.max_room_navigation_age
        summary["exit_priority_timeouts"] = self.exit_priority_timeouts
        summary["exit_priority_no_route_cooldowns"] = self.exit_priority_no_route_cooldowns
        summary["priority_known_warp_steps"] = self.priority_known_warp_steps
        summary["priority_geometry_steps"] = self.priority_geometry_steps
        summary["priority_visual_steps"] = self.priority_visual_steps
        summary["active_exit_priority_cooldowns"] = sum(
            expires > self.navigation_tick
            for expires in self.exit_priority_cooldown_until.values()
        )
        return summary
