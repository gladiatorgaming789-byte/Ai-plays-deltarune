from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .autonomy_v1 import (
    AUTONOMY_TOP_OPTION_LOG,
    RECOVERY_LEVEL_NAMES,
    AutonomyOption,
    AutonomyV1Explorer,
    RecoveryLevel,
)
from .autonomy_v1_runtime import AutonomyV1RuntimeExplorer
from .policy import DIRECTION_VECTORS
from .strategy import STRATEGY_FILENAME, StrategyGenome


NAVIGATION_COHERENCE_VERSION = 1
COHERENCE_MIN_RECOVERY_RESIDENCE = 12
COHERENCE_ROUTE_STALL_TICKS = 12
COHERENCE_BROAD_RESET_COOLDOWN = 36
COHERENCE_ARRIVAL_LEASE_TICKS = 18
COHERENCE_PORTAL_SOURCE_RADIUS = 6
COHERENCE_PORTAL_ARRIVAL_RADIUS = 3
COHERENCE_MAX_STRAIGHT_COMMITMENT = 3


@dataclass
class GoalContract:
    """Persistent execution contract for one evidence-backed navigation goal."""

    goal_id: str
    kind: str
    room: str
    created_tick: int
    story_epoch: int
    option: AutonomyOption = field(repr=False)
    target_cell: tuple[int, int] | None = None
    target_room: str | None = None
    current_cell: tuple[int, int] | None = None
    planned_direction: str | None = None
    route_preview: tuple[tuple[int, int], ...] = ()
    expected_outcome: str = "collect new evidence"
    replan_triggers: tuple[str, ...] = ()
    action_budget: int = 12
    actions_spent: int = 0
    best_distance: int | None = None
    current_distance: int | None = None
    no_progress_ticks: int = 0
    last_checked_tick: int = 0
    material_marker: tuple[object, ...] = ()

    def payload(self, navigation_tick: int) -> dict[str, object]:
        return {
            "goal_id": self.goal_id,
            "kind": self.kind,
            "room": self.room,
            "target_cell": (
                list(self.target_cell) if self.target_cell is not None else None
            ),
            "target_room": self.target_room,
            "current_cell": (
                list(self.current_cell) if self.current_cell is not None else None
            ),
            "planned_direction": self.planned_direction,
            "route_preview": [list(cell) for cell in self.route_preview],
            "expected_outcome": self.expected_outcome,
            "replan_triggers": list(self.replan_triggers),
            "created_tick": self.created_tick,
            "age": max(0, navigation_tick - self.created_tick),
            "actions_spent": self.actions_spent,
            "action_budget": self.action_budget,
            "actions_remaining": max(0, self.action_budget - self.actions_spent),
            "best_route_distance": self.best_distance,
            "current_route_distance": self.current_distance,
            "no_progress_ticks": self.no_progress_ticks,
        }


class NavigationCoherenceExplorer(AutonomyV1RuntimeExplorer):
    """Event-driven navigation coordinator above the completed Autonomy stack.

    The lower layers still own collision inference, visual evidence, interaction
    semantics, and world persistence. This layer owns only planning coherence:
    stable goals, clustered frontiers and portals, route progress, room-cycle
    costs, recovery hysteresis, and bounded reset damping.
    """

    def __init__(self, seed: int = 0, memory_path: Path | None = None):
        super().__init__(seed, memory_path)
        strategy_path = (
            memory_path.parent / STRATEGY_FILENAME
            if memory_path is not None
            else None
        )
        self.strategy_genome, self.strategy_warning = StrategyGenome.load(
            strategy_path
        )
        self.active_goal_contract: GoalContract | None = None
        self.last_coherence_replan_reason = "initial planning"
        self.last_coherence_material_marker = self._coherence_material_marker()
        self._arrival_room: str | None = None
        self._arrival_from_room: str | None = None
        self._arrival_lease_until = 0
        self._broad_reset_cooldown_until = 0
        self._broad_reset_material_marker: tuple[object, ...] = ()

        self.coherence_goal_activations = 0
        self.coherence_goal_reuses = 0
        self.coherence_goal_completions = 0
        self.coherence_goal_failures = 0
        self.coherence_goal_interruptions = 0
        self.coherence_event_replans = 0
        self.coherence_route_stalls = 0
        self.coherence_hysteresis_holds = 0
        self.coherence_cycle_penalties = 0
        self.coherence_reset_suppressions = 0
        self.coherence_adaptive_commitments = 0
        self.last_portal_sample_count = 0
        self.last_portal_aperture_count = 0
        self.last_frontier_cluster_count = 0

    def _score_option(self, option: AutonomyOption) -> float:
        """Score through the versioned genome without changing legacy defaults."""
        budget_fraction = 0.0
        if option.budget_key is not None and option.budget_limit > 0:
            state = self._ensure_budget(
                option.budget_key,
                option.fingerprint,
                option.budget_limit,
            )
            option.budget_spent = state.spent
            option.budget_remaining = state.remaining
            if state.remaining <= 0:
                return float("-inf")
            budget_fraction = state.spent / max(1, state.limit)
        option.score = self.strategy_genome.score(
            base_score=option.base_score,
            confidence=option.confidence,
            information_value=option.information_value,
            novelty=option.novelty,
            distance=option.distance,
            loop_risk=option.loop_risk,
            failure_cost=option.failure_cost,
            budget_fraction=budget_fraction,
        )
        return option.score

    # ------------------------------------------------------------------
    # Evidence and recovery stability
    # ------------------------------------------------------------------
    def _coherence_material_marker(self) -> tuple[object, ...]:
        screen_regions = getattr(self, "screen_regions", {})
        confirmed_visuals = sum(
            str(record.get("guess_state") or "") == "confirmed"
            or bool(record.get("confirmed_interactable_cell"))
            or bool(record.get("confirmed_target_room"))
            for record in screen_regions.values()
            if isinstance(record, Mapping)
        )
        progress_interactions = sum(
            self._safe_int(record.get("progressions")) > 0
            for record in getattr(self, "interactables", {}).values()
            if isinstance(record, Mapping)
        )
        return (
            getattr(self, "story_epoch", 0),
            len(getattr(self, "interactables", {})),
            len(getattr(self, "warps", {})),
            confirmed_visuals,
            progress_interactions,
        )

    def _update_recovery_state(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> RecoveryLevel:
        full_marker = self._autonomy_evidence_marker()
        previous_full = getattr(self, "_last_autonomy_evidence_marker", full_marker)
        if full_marker != previous_full:
            # Ordinary geometry discovery earns a fresh frontier lease, but it
            # does not collapse an expensive recovery level immediately.
            self._reset_frontier_pressure()

        material = self._coherence_material_marker()
        previous_material = getattr(
            self,
            "last_coherence_material_marker",
            material,
        )
        material_changed = material != previous_material
        self.last_coherence_material_marker = material

        if full_marker[0] != getattr(self, "_last_autonomy_story_epoch", full_marker[0]):
            self._last_autonomy_story_epoch = full_marker[0]
            self._set_recovery_level(RecoveryLevel.NORMAL, "story epoch changed")
            self._clear_autonomy_goal("story epoch changed")

        desired = self._desired_recovery_level(room, cell)
        current = self.recovery_level
        age = max(0, self.navigation_tick - self.recovery_level_started_at)

        if desired > current:
            self._set_recovery_level(
                desired,
                f"recovery pressure reached {RECOVERY_LEVEL_NAMES[desired]}",
            )
        elif desired < current:
            can_step_down = age >= COHERENCE_MIN_RECOVERY_RESIDENCE and (
                material_changed
                or desired == RecoveryLevel.NORMAL
                or current - desired > 1
            )
            if can_step_down:
                next_level = RecoveryLevel(max(int(desired), int(current) - 1))
                reason = (
                    "material evidence changed"
                    if material_changed
                    else "lower recovery pressure remained stable"
                )
                self._set_recovery_level(next_level, reason)
            else:
                self.coherence_hysteresis_holds += 1
        elif desired == RecoveryLevel.FRONTIER:
            self._set_recovery_level(
                RecoveryLevel.FRONTIER,
                "reachable learned frontier remains",
            )

        self._last_autonomy_evidence_marker = full_marker
        self.max_recovery_level_age = max(
            self.max_recovery_level_age,
            max(0, self.navigation_tick - self.recovery_level_started_at),
        )
        return self.recovery_level

    # ------------------------------------------------------------------
    # Room-cycle evidence and portal apertures
    # ------------------------------------------------------------------
    @staticmethod
    def room_cycle_penalty(recent_rooms: list[str], target_room: str) -> float:
        if not target_room or not recent_rooms:
            return 0.0
        penalty = min(0.24, recent_rooms.count(target_room) * 0.06)
        if len(recent_rooms) >= 2 and target_room == recent_rooms[-2]:
            penalty += 0.56

        extended = [*recent_rooms, target_room]
        for period in (1, 2, 3):
            span = period * 2
            if len(extended) >= span and extended[-span:-period] == extended[-period:]:
                penalty += 0.18 + period * 0.04
        return min(1.0, penalty)

    def _arrival_cycle_penalty(self, room: str, target_room: str) -> float:
        penalty = self.room_cycle_penalty(
            list(getattr(self, "recent_rooms", ())),
            target_room,
        )
        if (
            room == getattr(self, "_arrival_room", None)
            and target_room == getattr(self, "_arrival_from_room", None)
            and self.navigation_tick < getattr(self, "_arrival_lease_until", 0)
        ):
            penalty = min(1.0, penalty + 0.45)
        if penalty > 0:
            self.coherence_cycle_penalties += 1
        return penalty

    @staticmethod
    def _portal_options_share_aperture(
        left: AutonomyOption,
        right: AutonomyOption,
    ) -> bool:
        left_warp = left.metadata.get("warp")
        right_warp = right.metadata.get("warp")
        if (
            not isinstance(left_warp, tuple)
            or not isinstance(right_warp, tuple)
            or len(left_warp) != 7
            or len(right_warp) != 7
        ):
            return False
        if (left_warp[0], left_warp[3], left_warp[4]) != (
            right_warp[0],
            right_warp[3],
            right_warp[4],
        ):
            return False
        source_gap = max(
            abs(left_warp[1] - right_warp[1]),
            abs(left_warp[2] - right_warp[2]),
        )
        arrival_gap = max(
            abs(left_warp[5] - right_warp[5]),
            abs(left_warp[6] - right_warp[6]),
        )
        return (
            source_gap <= COHERENCE_PORTAL_SOURCE_RADIUS
            and arrival_gap <= COHERENCE_PORTAL_ARRIVAL_RADIUS
        )

    def _collect_warp_options(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> list[AutonomyOption]:
        samples = super()._collect_warp_options(room, cell)
        groups: list[list[AutonomyOption]] = []
        for sample in samples:
            for group in groups:
                if all(
                    self._portal_options_share_aperture(sample, member)
                    for member in group
                ):
                    group.append(sample)
                    break
            else:
                groups.append([sample])

        results: list[AutonomyOption] = []
        role_rank = {
            "progression": 0,
            "new_area": 1,
            "unknown": 2,
            "loop_suppressed": 3,
        }
        for group in groups:
            representative = min(
                group,
                key=lambda option: (
                    option.distance,
                    role_rank.get(str(option.metadata.get("role") or "unknown"), 4),
                    -self._safe_int(option.metadata.get("crossings")),
                    option.option_id,
                ),
            )
            warps = [
                option.metadata["warp"]
                for option in group
                if isinstance(option.metadata.get("warp"), tuple)
                and len(option.metadata["warp"]) == 7
            ]
            if not warps:
                continue
            crossings = sum(
                max(1, self._safe_int(option.metadata.get("crossings"), 1))
                for option in group
            )
            source_bounds = [
                min(warp[1] for warp in warps),
                min(warp[2] for warp in warps),
                max(warp[1] for warp in warps),
                max(warp[2] for warp in warps),
            ]
            arrival_bounds = [
                min(warp[5] for warp in warps),
                min(warp[6] for warp in warps),
                max(warp[5] for warp in warps),
                max(warp[6] for warp in warps),
            ]
            best_role = min(
                (str(option.metadata.get("role") or "unknown") for option in group),
                key=lambda role: role_rank.get(role, 4),
            )
            warp = representative.metadata["warp"]
            cycle_penalty = self._arrival_cycle_penalty(room, str(warp[4]))
            representative.option_id = (
                f"portal_aperture:{room}:{warp[4]}:{warp[3]}:"
                f"{source_bounds[0]}:{source_bounds[1]}"
            )
            representative.base_score = max(option.base_score for option in group)
            representative.confidence = max(option.confidence for option in group)
            representative.information_value = min(
                1.0,
                max(option.information_value for option in group)
                + min(0.25, (len(group) - 1) * 0.06),
            )
            representative.loop_risk = min(
                1.0,
                max(option.loop_risk for option in group) + cycle_penalty,
            )
            representative.metadata.update(
                {
                    "role": best_role,
                    "crossings": crossings,
                    "aperture_members": len(group),
                    "source_bounds": source_bounds,
                    "arrival_bounds": arrival_bounds,
                    "target_cell": [warp[1], warp[2]],
                    "room_cycle_penalty": round(cycle_penalty, 3),
                }
            )
            results.append(representative)

        self.last_portal_sample_count = len(samples)
        self.last_portal_aperture_count = len(results)
        return results

    def _collect_long_horizon_options(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> list[AutonomyOption]:
        options = super()._collect_long_horizon_options(room, cell)
        for option in options:
            path = option.metadata.get("path_rooms")
            path_rooms = [str(value) for value in path] if isinstance(path, list) else []
            penalties = [
                self.room_cycle_penalty(
                    list(getattr(self, "recent_rooms", ())),
                    target,
                )
                for target in path_rooms[1:]
            ]
            cycle_penalty = max(penalties, default=0.0)
            option.loop_risk = min(1.0, option.loop_risk + cycle_penalty)
            option.metadata["room_cycle_penalty"] = round(cycle_penalty, 3)
        return options

    def _observe_room(self, telemetry) -> None:
        previous = getattr(self, "observed_room", None)
        super()._observe_room(telemetry)
        room = self._room_key(telemetry)
        if previous is not None and room != previous:
            self._arrival_room = room
            self._arrival_from_room = previous
            self._arrival_lease_until = (
                self.navigation_tick + COHERENCE_ARRIVAL_LEASE_TICKS
            )

    # ------------------------------------------------------------------
    # Information-gain frontier clustering
    # ------------------------------------------------------------------
    def _frontier_directions(
        self,
        room: str,
        cell: tuple[int, int],
        *,
        respect_loop_avoid: bool = True,
    ) -> list[str]:
        avoid = (
            self._loop_avoid_directions(room, cell)
            if respect_loop_avoid
            else set()
        )
        return [
            direction
            for direction in DIRECTION_VECTORS
            if direction not in avoid
            and self._direction_is_unexplored(room, cell, direction)
            and not self._is_entry_warp_direction(room, cell, direction)
        ]

    def _frontier_information_gain(
        self,
        room: str,
        cells: list[tuple[int, int]],
    ) -> tuple[float, int, int, int]:
        unknown_edges = 0
        unseen_cells: set[tuple[int, int]] = set()
        unseen_regions: set[tuple[int, int]] = set()
        seen = getattr(self, "seen_cells", set())
        seen_regions = getattr(self, "seen_regions", set())
        for frontier in cells:
            for direction in self._frontier_directions(room, frontier):
                unknown_edges += 1
                dx, dy = DIRECTION_VECTORS[direction]
                target = (frontier[0] + dx, frontier[1] + dy)
                if (room, *target) not in seen:
                    unseen_cells.add(target)
                region = self._region(target)
                if (room, *region) not in seen_regions:
                    unseen_regions.add(region)
        raw_gain = unknown_edges + len(unseen_cells) * 0.75 + len(unseen_regions) * 1.5
        return (
            min(1.0, raw_gain / 10.0),
            unknown_edges,
            len(unseen_cells),
            len(unseen_regions),
        )

    def _collect_frontier_options(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> list[AutonomyOption]:
        clusters: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for seen_room, x, y in sorted(getattr(self, "seen_cells", set())):
            if seen_room != room:
                continue
            candidate = (x, y)
            if self._frontier_directions(room, candidate):
                clusters.setdefault(self._region(candidate), []).append(candidate)

        options: list[AutonomyOption] = []
        evidence = self._autonomy_evidence_marker()
        for region, candidates in sorted(clusters.items()):
            reachable: list[
                tuple[int, int, tuple[int, int], str, list[str]]
            ] = []
            for target in candidates:
                directions = self._frontier_directions(room, target)
                if target == cell:
                    first = directions[0]
                    distance = 0
                else:
                    route = self._route_distance(room, cell, target)
                    if route is None:
                        continue
                    first, distance = route
                visit_count = self.visits[(room, *target)]
                reachable.append((distance, visit_count, target, first, directions))
            if not reachable:
                continue
            distance, _visits, target, first, directions = min(reachable)
            gain, unknown_edges, unseen_cells, unseen_regions = (
                self._frontier_information_gain(room, candidates)
            )
            probe_direction = min(
                directions,
                key=lambda direction: self._exploration_direction_score(
                    room,
                    target,
                    direction,
                ),
            )
            option_id = f"frontier_cluster:{room}:{region[0]}:{region[1]}"
            options.append(
                AutonomyOption(
                    option_id=option_id,
                    kind="frontier_cluster",
                    required_level=RecoveryLevel.FRONTIER,
                    base_score=9.3 + gain * 1.6,
                    confidence=0.94,
                    information_value=gain,
                    novelty=min(1.0, 0.45 + unseen_regions * 0.2),
                    distance=distance,
                    loop_risk=min(
                        0.75,
                        self._recent_cell_cost(room, target) / 1200.0,
                    ),
                    budget_key=option_id,
                    budget_limit=min(24, max(8, distance * 2 + unknown_edges + 4)),
                    fingerprint=(
                        evidence[0],
                        region,
                        len(candidates),
                        unknown_edges,
                    ),
                    metadata={
                        "direction": first,
                        "probe_direction": probe_direction,
                        "target_cell": list(target),
                        "cluster_region": list(region),
                        "frontier_cells": len(candidates),
                        "unknown_edges": unknown_edges,
                        "unseen_cells": unseen_cells,
                        "unseen_regions": unseen_regions,
                        "expected_gain": round(gain, 3),
                    },
                )
            )
        self.last_frontier_cluster_count = len(options)
        return options

    def _frontier_cluster_exhausted(self, contract: GoalContract) -> bool:
        region = contract.option.metadata.get("cluster_region")
        if not isinstance(region, (list, tuple)) or len(region) != 2:
            return False
        region_key = (self._safe_int(region[0]), self._safe_int(region[1]))
        return not any(
            seen_room == contract.room
            and self._region((x, y)) == region_key
            and bool(
                self._frontier_directions(
                    contract.room,
                    (x, y),
                    respect_loop_avoid=False,
                )
            )
            for seen_room, x, y in getattr(self, "seen_cells", set())
        )

    # ------------------------------------------------------------------
    # Goal contracts and event-driven replanning
    # ------------------------------------------------------------------
    def _target_for_option(self, option: AutonomyOption) -> tuple[int, int] | None:
        target = option.metadata.get("target_cell")
        if isinstance(target, (list, tuple)) and len(target) == 2:
            return self._safe_int(target[0]), self._safe_int(target[1])
        source = option.metadata.get("source")
        if isinstance(source, tuple) and len(source) == 2:
            return self._safe_int(source[0]), self._safe_int(source[1])
        probe = option.metadata.get("probe")
        if isinstance(probe, tuple) and len(probe) == 4:
            return self._safe_int(probe[1]), self._safe_int(probe[2])
        warp = option.metadata.get("warp")
        if isinstance(warp, tuple) and len(warp) == 7:
            return self._safe_int(warp[1]), self._safe_int(warp[2])
        key = option.metadata.get("key")
        if isinstance(key, tuple) and len(key) == 3:
            record = getattr(self, "screen_regions", {}).get(key, {})
            anchor = record.get("anchor_cell") if isinstance(record, Mapping) else None
            if isinstance(anchor, (list, tuple)) and len(anchor) == 2:
                return self._safe_int(anchor[0]), self._safe_int(anchor[1])
        return None

    @staticmethod
    def _contract_expectation(option: AutonomyOption) -> str:
        return {
            "frontier_cluster": "reveal new cells or learned edges in the selected frontier cluster",
            "retry_interaction": "observe a new response, choice outcome, or story-state change",
            "semantic_entity": "reach and test the observed entity candidate once",
            "semantic_exit": "confirm or reject the observed passage candidate",
            "information_probe": "collect a new independent view of the uncertain region",
            "weak_entity_test": "confirm or reject the bounded entity candidate",
            "geometry_exit_test": "confirm or reject the observed boundary continuation",
            "learned_warp": "cross the observed portal into its target room",
            "controlled_backtrack": "return through the selected observed portal",
            "long_horizon_route": "complete the next observed portal leg toward the opportunity room",
        }.get(option.kind, "collect new evidence from the selected option")

    def _contract_action_budget(self, option: AutonomyOption) -> int:
        return min(
            64,
            max(
                10,
                option.budget_limit,
                option.distance * 3 + 8,
            ),
        )

    def _activate_goal(self, option: AutonomyOption) -> None:
        existing = getattr(self, "active_goal_contract", None)
        super()._activate_goal(option)
        if option.kind == "broad_reset":
            return
        if existing is not None and existing.goal_id == option.option_id:
            return
        target = self._target_for_option(option)
        self.active_goal_contract = GoalContract(
            goal_id=option.option_id,
            kind=option.kind,
            room=str(getattr(self, "observed_room", None) or option.metadata.get("room") or ""),
            created_tick=self.navigation_tick,
            story_epoch=self.story_epoch,
            option=option,
            target_cell=target,
            target_room=str(option.metadata.get("target_room") or "") or None,
            expected_outcome=self._contract_expectation(option),
            replan_triggers=(
                "story or room transition",
                "material learned evidence",
                "target becomes unreachable",
                "route stops getting closer",
                "bounded action budget is exhausted",
            ),
            action_budget=self._contract_action_budget(option),
            best_distance=option.distance if target is not None else None,
            current_distance=option.distance if target is not None else None,
            last_checked_tick=self.navigation_tick,
            material_marker=self._coherence_material_marker(),
        )
        self.coherence_goal_activations += 1
        self.last_coherence_replan_reason = (
            f"ranked and activated {option.kind} goal {option.option_id}"
        )
        self.map_updates.append(
            {
                "type": "navigation_goal_contract",
                "version": NAVIGATION_COHERENCE_VERSION,
                **self.active_goal_contract.payload(self.navigation_tick),
            }
        )

    def _clear_autonomy_goal(self, reason: str) -> None:
        contract = getattr(self, "active_goal_contract", None)
        if contract is not None:
            lowered = reason.lower()
            room_change_completed = (
                "room changed" in lowered
                and contract.kind
                in {"learned_warp", "controlled_backtrack", "long_horizon_route"}
            )
            if (
                "progress" in lowered
                or "completed" in lowered
                or "exhausted frontier" in lowered
                or room_change_completed
            ):
                outcome = "completed"
                self.coherence_goal_completions += 1
            elif any(
                marker in lowered
                for marker in ("unreachable", "stalled", "budget", "invalid", "failed")
            ):
                outcome = "failed"
                self.coherence_goal_failures += 1
            else:
                outcome = "interrupted"
                self.coherence_goal_interruptions += 1
            self.last_coherence_replan_reason = reason
            self.coherence_event_replans += 1
            self.map_updates.append(
                {
                    "type": "navigation_goal_contract_end",
                    "version": NAVIGATION_COHERENCE_VERSION,
                    "goal_id": contract.goal_id,
                    "kind": contract.kind,
                    "outcome": outcome,
                    "reason": reason,
                    "actions_spent": contract.actions_spent,
                    "navigation_tick": self.navigation_tick,
                }
            )
        self.active_goal_contract = None
        super()._clear_autonomy_goal(reason)

    def _contract_distance(
        self,
        contract: GoalContract,
        room: str,
        cell: tuple[int, int],
    ) -> int | None:
        if contract.target_cell is None or room != contract.room:
            return None
        if cell == contract.target_cell:
            return 0
        route = self._route_distance(
            room,
            cell,
            contract.target_cell,
            allow_backtrack=bool(contract.option.metadata.get("allow_backtrack")),
        )
        return None if route is None else route[1]

    def _contract_route_preview(
        self,
        contract: GoalContract,
        room: str,
        cell: tuple[int, int],
    ) -> tuple[tuple[int, int], ...]:
        target = contract.target_cell
        if target is None or room != contract.room:
            return ()
        if cell == target:
            return (cell,)
        adjacency = self._adjacency(
            room,
            avoid_backtrack=not bool(
                contract.option.metadata.get("allow_backtrack")
            ),
        )
        queue = deque([(cell, (cell,))])
        visited = {cell}
        while queue:
            current, path = queue.popleft()
            if len(path) > 64:
                continue
            for _direction, neighbor in sorted(adjacency.get(current, [])):
                if neighbor in visited:
                    continue
                route = (*path, neighbor)
                if neighbor == target:
                    return route
                visited.add(neighbor)
                queue.append((neighbor, route))
        return ()

    def _active_contract_is_valid(
        self,
        contract: GoalContract,
        room: str,
        cell: tuple[int, int],
    ) -> bool:
        if contract.room and room != contract.room:
            self._clear_autonomy_goal("room transition requires a new route plan")
            return False
        if self.story_epoch != contract.story_epoch:
            self._clear_autonomy_goal("story progress changed the planning state")
            return False
        if contract.actions_spent >= contract.action_budget:
            self._clear_autonomy_goal("goal action budget exhausted")
            return False
        if contract.kind == "frontier_cluster" and self._frontier_cluster_exhausted(contract):
            self._clear_autonomy_goal("completed: exhausted frontier cluster")
            return False

        material = self._coherence_material_marker()
        if material != contract.material_marker:
            contract.material_marker = material
            if contract.kind != "frontier_cluster" and contract.actions_spent > 0:
                self._clear_autonomy_goal("material learned evidence changed")
                return False

        if contract.target_cell is not None:
            distance = self._contract_distance(contract, room, cell)
            if distance is None:
                self._clear_autonomy_goal("selected target became unreachable")
                return False
            elapsed = max(1, self.navigation_tick - contract.last_checked_tick)
            contract.last_checked_tick = self.navigation_tick
            contract.current_distance = distance
            contract.current_cell = cell
            contract.route_preview = self._contract_route_preview(
                contract,
                room,
                cell,
            )
            if contract.best_distance is None or distance < contract.best_distance:
                contract.best_distance = distance
                contract.no_progress_ticks = 0
            else:
                contract.no_progress_ticks += elapsed
            if contract.no_progress_ticks >= COHERENCE_ROUTE_STALL_TICKS:
                self.coherence_route_stalls += 1
                self._clear_autonomy_goal("geodesic route progress stalled")
                return False

        if contract.option.budget_key:
            state = getattr(self, "uncertainty_budgets", {}).get(
                contract.option.budget_key
            )
            if state is not None and state.remaining <= 0:
                self._clear_autonomy_goal("uncertainty budget exhausted")
                return False
        return True

    def _safe_straight_commitment(
        self,
        room: str,
        cell: tuple[int, int],
        direction: str,
        distance: int | None,
    ) -> int:
        if direction not in DIRECTION_VECTORS or distance is None or distance <= 2:
            return 1
        cursor = cell
        safe_steps = 0
        for _ in range(min(COHERENCE_MAX_STRAIGHT_COMMITMENT, distance - 1)):
            if self._blocked_near(room, cursor, direction):
                break
            if self._is_entry_warp_direction(room, cursor, direction):
                break
            neighbor = self._known_open_neighbor(room, cursor, direction)
            if neighbor is None:
                break
            safe_steps += 1
            cursor = neighbor
        return max(1, safe_steps)

    def _finalize_contract_plan(
        self,
        plan: tuple[str, int, str],
        room: str,
        cell: tuple[int, int],
    ) -> tuple[str, int, str]:
        contract = getattr(self, "active_goal_contract", None)
        if contract is None:
            return plan
        direction, steps, reason = plan
        contract.current_cell = cell
        contract.planned_direction = direction
        contract.route_preview = self._contract_route_preview(
            contract,
            room,
            cell,
        )
        commitment = self._safe_straight_commitment(
            room,
            cell,
            direction,
            contract.current_distance,
        )
        if commitment > steps:
            steps = commitment
            self.coherence_adaptive_commitments += 1
            reason = f"{reason}; {commitment}-step verified corridor commitment"
        contract.actions_spent += max(1, steps)
        return direction, steps, reason

    def _reuse_active_contract(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> tuple[str, int, str] | None:
        contract = getattr(self, "active_goal_contract", None)
        if contract is None or not self._active_contract_is_valid(contract, room, cell):
            return None
        plan = self._execute_option(contract.option, room, cell)
        if plan is None:
            self._clear_autonomy_goal("selected option became unreachable")
            return None
        self._consume_budget(contract.option)
        self.coherence_goal_reuses += 1
        self.autonomy_goal_commitment_holds += 1
        self.last_autonomy_commitment_hold = True
        self.autonomy_selections[contract.kind] = (
            self.autonomy_selections.get(contract.kind, 0) + 1
        )
        self.last_autonomy_selected_id = contract.goal_id
        self.last_ranked_autonomy_options = [
            self._option_payload(contract.option, selected=True)
        ]
        return self._finalize_contract_plan(plan, room, cell)

    def _execute_option(
        self,
        option: AutonomyOption,
        room: str,
        cell: tuple[int, int],
    ):
        if option.kind == "frontier_cluster":
            target = self._target_for_option(option)
            if target is None:
                return None
            if cell != target:
                route = self._route_distance(room, cell, target)
                if route is None:
                    return None
                direction = route[0]
                reason = (
                    "navigation coherence: follow learned route to information-gain "
                    f"frontier cluster {option.metadata.get('cluster_region')}"
                )
            else:
                choices = self._frontier_directions(room, cell)
                preferred = str(option.metadata.get("probe_direction") or "")
                if not choices:
                    return None
                direction = preferred if preferred in choices else min(
                    choices,
                    key=lambda candidate: self._exploration_direction_score(
                        room,
                        cell,
                        candidate,
                    ),
                )
                reason = (
                    "navigation coherence: probe the highest information edge in "
                    f"frontier cluster {option.metadata.get('cluster_region')}"
                )
            self.frontier_ranked_actions += 1
            return direction, 1, reason

        if option.kind == "retry_interaction":
            source = option.metadata.get("source")
            interaction_direction = str(
                option.metadata.get("interaction_direction") or ""
            )
            if (
                not isinstance(source, tuple)
                or len(source) != 2
                or interaction_direction not in DIRECTION_VECTORS
            ):
                return None
            self._prepare_retry_interaction_goal(option)
            if source == cell:
                direction = interaction_direction
            else:
                route = self._route_distance(room, cell, source)
                if route is None:
                    return None
                direction = route[0]
            return (
                direction,
                1,
                "navigation coherence: continue toward learned interaction response",
            )

        plan = super()._execute_option(option, room, cell)
        if option.kind == "broad_reset" and plan is not None:
            self._broad_reset_cooldown_until = (
                self.navigation_tick + COHERENCE_BROAD_RESET_COOLDOWN
            )
            self._broad_reset_material_marker = self._coherence_material_marker()
            self._clear_autonomy_goal("broad reset dispatched; cooldown active")
        return plan

    def _collect_recovery_options(
        self,
        room: str,
        cell: tuple[int, int],
        level: RecoveryLevel,
    ) -> list[AutonomyOption]:
        options = super()._collect_recovery_options(room, cell, level)
        cooling = (
            self.navigation_tick < getattr(self, "_broad_reset_cooldown_until", 0)
            and self._coherence_material_marker()
            == getattr(self, "_broad_reset_material_marker", ())
        )
        if cooling:
            kept = [option for option in options if option.kind != "broad_reset"]
            if len(kept) != len(options):
                self.coherence_reset_suppressions += 1
            options = kept
        return options

    def _plan_autonomy_recovery(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> tuple[str, int, str] | None:
        reused = self._reuse_active_contract(room, cell)
        if reused is not None:
            return reused
        plan = super()._plan_autonomy_recovery(room, cell)
        if plan is None:
            return None
        return self._finalize_contract_plan(plan, room, cell)

    def _plan_frontier_contract(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> tuple[str, int, str] | None:
        reused = self._reuse_active_contract(room, cell)
        if reused is not None:
            return reused
        options = self._collect_frontier_options(room, cell)
        ranked = [option for option in options if self._score_option(option) != float("-inf")]
        ranked.sort(key=lambda option: (-option.score, option.distance, option.option_id))
        if not ranked:
            return None
        selected = ranked[0]
        self._activate_goal(selected)
        plan = self._execute_option(selected, room, cell)
        if plan is None:
            self._consume_budget(selected)
            self._clear_autonomy_goal("selected frontier cluster became unreachable")
            return None
        self._consume_budget(selected)
        self.autonomy_selections[selected.kind] = (
            self.autonomy_selections.get(selected.kind, 0) + 1
        )
        self.last_autonomy_selected_id = selected.option_id
        self.last_ranked_autonomy_options = [
            self._option_payload(
                option,
                selected=option.option_id == selected.option_id,
            )
            for option in ranked[:AUTONOMY_TOP_OPTION_LOG]
        ]
        return self._finalize_contract_plan(plan, room, cell)

    def _plan_exploration(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> tuple[str, int, str]:
        level = self._update_recovery_state(room, cell)
        if level == RecoveryLevel.NORMAL:
            if getattr(self, "active_goal_contract", None) is not None:
                self._clear_autonomy_goal("normal exploration resumed")
            self.last_ranked_autonomy_options = []
            self.last_autonomy_selected_id = None
            self.last_autonomy_commitment_hold = False
            return super(AutonomyV1Explorer, self)._plan_exploration(room, cell)
        if level == RecoveryLevel.FRONTIER:
            plan = self._plan_frontier_contract(room, cell)
            if plan is not None:
                return plan
            return super(AutonomyV1Explorer, self)._plan_exploration(room, cell)
        plan = self._plan_autonomy_recovery(room, cell)
        if plan is not None:
            return plan
        return super(AutonomyV1Explorer, self)._plan_exploration(room, cell)

    # ------------------------------------------------------------------
    # Saved diagnostics
    # ------------------------------------------------------------------
    def autonomy_snapshot(self) -> dict[str, object]:
        snapshot = super().autonomy_snapshot()
        contract = getattr(self, "active_goal_contract", None)
        snapshot["coherence"] = {
            "version": NAVIGATION_COHERENCE_VERSION,
            "goal_contract": (
                contract.payload(self.navigation_tick) if contract is not None else None
            ),
            "last_replan_reason": getattr(
                self,
                "last_coherence_replan_reason",
                "",
            ),
            "recent_rooms": list(getattr(self, "recent_rooms", ())),
            "arrival_lease": {
                "room": getattr(self, "_arrival_room", None),
                "from_room": getattr(self, "_arrival_from_room", None),
                "remaining": max(
                    0,
                    getattr(self, "_arrival_lease_until", 0) - self.navigation_tick,
                ),
            },
            "broad_reset_cooldown_remaining": max(
                0,
                getattr(self, "_broad_reset_cooldown_until", 0)
                - self.navigation_tick,
            ),
            "frontier_clusters": getattr(self, "last_frontier_cluster_count", 0),
            "portal_samples": getattr(self, "last_portal_sample_count", 0),
            "portal_apertures": getattr(self, "last_portal_aperture_count", 0),
        }
        return snapshot

    def summary(self) -> dict:
        summary = super().summary()
        summary.update(
            {
                "navigation_coherence_version": NAVIGATION_COHERENCE_VERSION,
                "coherence_goal_activations": self.coherence_goal_activations,
                "coherence_goal_reuses": self.coherence_goal_reuses,
                "coherence_goal_completions": self.coherence_goal_completions,
                "coherence_goal_failures": self.coherence_goal_failures,
                "coherence_goal_interruptions": self.coherence_goal_interruptions,
                "coherence_event_replans": self.coherence_event_replans,
                "coherence_route_stalls": self.coherence_route_stalls,
                "coherence_hysteresis_holds": self.coherence_hysteresis_holds,
                "coherence_cycle_penalties": self.coherence_cycle_penalties,
                "coherence_reset_suppressions": self.coherence_reset_suppressions,
                "coherence_adaptive_commitments": self.coherence_adaptive_commitments,
                "last_frontier_cluster_count": self.last_frontier_cluster_count,
                "last_portal_sample_count": self.last_portal_sample_count,
                "last_portal_aperture_count": self.last_portal_aperture_count,
                "strategy_genome": self.strategy_genome.to_dict(),
                "strategy_warning": self.strategy_warning,
            }
        )
        return summary


__all__ = [
    "COHERENCE_ARRIVAL_LEASE_TICKS",
    "COHERENCE_BROAD_RESET_COOLDOWN",
    "COHERENCE_MAX_STRAIGHT_COMMITMENT",
    "COHERENCE_MIN_RECOVERY_RESIDENCE",
    "COHERENCE_PORTAL_ARRIVAL_RADIUS",
    "COHERENCE_PORTAL_SOURCE_RADIUS",
    "COHERENCE_ROUTE_STALL_TICKS",
    "GoalContract",
    "NAVIGATION_COHERENCE_VERSION",
    "NavigationCoherenceExplorer",
]
