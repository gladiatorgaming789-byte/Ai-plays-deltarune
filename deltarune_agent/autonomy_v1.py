from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum
from math import log
from pathlib import Path
from typing import Any, Mapping

from .entity_detection_v2 import entity_candidate_state, single_side_entity_candidate
from .guessing_v3 import (
    FINAL_GUESS_STATES,
    MAX_INFORMATION_PROBES,
    UNKNOWN_BUT_INTERESTING,
    information_gain_probe_plan,
)
from .policy import (
    DIRECTION_VECTORS,
    MAX_EXIT_PROBES,
    MIN_VISUAL_GUESS_CONFIDENCE,
)
from .run21_final import Run21Explorer
from .warp_classification_v2 import _room_completion_pressure
from .world_model import Warp


AUTONOMY_VERSION = 1
AUTONOMY_GOAL_COMMIT_STEPS = 6
AUTONOMY_GOAL_BREAK_MARGIN = 1.35
AUTONOMY_MAX_LONG_HORIZON_HOPS = 4
AUTONOMY_TOP_OPTION_LOG = 8


class RecoveryLevel(IntEnum):
    NORMAL = 0
    FRONTIER = 1
    EVIDENCE = 2
    BOUNDED_TEST = 3
    LEARNED_ROUTE = 4
    CONTROLLED_BACKTRACK = 5
    BROAD_RESET = 6


RECOVERY_LEVEL_NAMES = {
    RecoveryLevel.NORMAL: "normal",
    RecoveryLevel.FRONTIER: "frontier",
    RecoveryLevel.EVIDENCE: "evidence",
    RecoveryLevel.BOUNDED_TEST: "bounded_test",
    RecoveryLevel.LEARNED_ROUTE: "learned_route",
    RecoveryLevel.CONTROLLED_BACKTRACK: "controlled_backtrack",
    RecoveryLevel.BROAD_RESET: "broad_reset",
}


@dataclass
class BudgetState:
    fingerprint: tuple[object, ...]
    limit: int
    spent: int = 0
    created_tick: int = 0
    last_evidence_tick: int = 0
    exhausted_tick: int | None = None

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.spent)


@dataclass
class AutonomyOption:
    option_id: str
    kind: str
    required_level: RecoveryLevel
    base_score: float
    confidence: float = 0.0
    information_value: float = 0.0
    novelty: float = 0.0
    distance: int = 0
    loop_risk: float = 0.0
    failure_cost: float = 0.0
    budget_key: str | None = None
    budget_limit: int = 0
    fingerprint: tuple[object, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)
    score: float = 0.0
    budget_spent: int = 0
    budget_remaining: int = 0


class AutonomyV1Explorer(Run21Explorer):
    """Evidence-first recovery coordinator layered above the Run21 policy.

    Autonomy v1 does not know which room, object, warp, or dialogue advances the
    game. It ranks only options the agent has already observed and escalates the
    cost of recovery as those learned options are exhausted.
    """

    def __init__(self, seed: int = 0, memory_path: Path | None = None):
        super().__init__(seed, memory_path)
        self.recovery_level = RecoveryLevel.NORMAL
        self.recovery_level_started_at = self.navigation_tick
        self.recovery_reason = "normal exploration"
        self.max_recovery_level = RecoveryLevel.NORMAL
        self.max_recovery_level_age = 0
        self.recovery_level_changes = 0
        self.recovery_escalations = 0
        self.recovery_deescalations = 0

        self.uncertainty_budgets: dict[str, BudgetState] = {}
        self.uncertainty_budget_actions = 0
        self.uncertainty_budget_exhaustions = 0
        self.uncertainty_budget_evidence_resets = 0

        self.active_autonomy_goal_id: str | None = None
        self.active_autonomy_goal_kind: str | None = None
        self.active_autonomy_goal_started_at = 0
        self.autonomy_goal_activations = 0
        self.autonomy_goal_switches = 0
        self.autonomy_goal_commitment_holds = 0
        self.autonomy_goal_breaks_for_stronger_evidence = 0

        self.autonomy_selections: dict[str, int] = {}
        self.long_horizon_plans = 0
        self.loop_risk_avoids = 0
        self.broad_recovery_resets = 0
        self.empty_tier_escalations = 0
        self.last_autonomy_selected_id: str | None = None
        self.last_autonomy_commitment_hold = False
        self.last_ranked_autonomy_options: list[dict[str, object]] = []

        self._last_autonomy_story_epoch = self.story_epoch
        self._last_autonomy_evidence_marker = self._autonomy_evidence_marker()

    # ------------------------------------------------------------------
    # Recovery state
    # ------------------------------------------------------------------
    def _autonomy_evidence_marker(self) -> tuple[int, int, int, int, int]:
        return (
            self.story_epoch,
            len(self.seen_cells),
            len(self.open_edges),
            len(self.interactables),
            len(self.warps),
        )

    def _has_reachable_autonomy_frontier(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> bool:
        return bool(
            any(
                self._direction_is_unexplored(room, cell, direction)
                for direction in DIRECTION_VECTORS
            )
            or self._route_to_nearest_frontier(room, cell) is not None
        )

    def _desired_recovery_level(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> RecoveryLevel:
        pressure = self._progress_pressure(room, cell)
        if not pressure:
            return RecoveryLevel.NORMAL
        if self._has_reachable_autonomy_frontier(room, cell):
            return RecoveryLevel.FRONTIER

        stall = max(0, int(self.story_stall_steps))
        try:
            completion_pressure = _room_completion_pressure(self, room)
        except AttributeError:
            completion_pressure = False

        if stall >= 260:
            return RecoveryLevel.BROAD_RESET
        if stall >= 180:
            return RecoveryLevel.CONTROLLED_BACKTRACK
        if completion_pressure or stall >= 96:
            return RecoveryLevel.LEARNED_ROUTE
        if stall >= 72:
            return RecoveryLevel.BOUNDED_TEST
        return RecoveryLevel.EVIDENCE

    def _set_recovery_level(
        self,
        level: RecoveryLevel,
        reason: str,
    ) -> None:
        level = RecoveryLevel(level)
        if level == self.recovery_level:
            self.recovery_reason = reason
            return
        previous = self.recovery_level
        age = max(0, self.navigation_tick - self.recovery_level_started_at)
        self.max_recovery_level_age = max(self.max_recovery_level_age, age)
        self.recovery_level = level
        self.recovery_level_started_at = self.navigation_tick
        self.recovery_reason = reason
        self.max_recovery_level = max(self.max_recovery_level, level)
        self.recovery_level_changes += 1
        if level > previous:
            self.recovery_escalations += 1
        else:
            self.recovery_deescalations += 1
        self.map_updates.append(
            {
                "type": "autonomy_recovery",
                "version": AUTONOMY_VERSION,
                "from": RECOVERY_LEVEL_NAMES[previous],
                "to": RECOVERY_LEVEL_NAMES[level],
                "reason": reason,
                "story_epoch": self.story_epoch,
                "story_stall_steps": self.story_stall_steps,
                "navigation_tick": self.navigation_tick,
            }
        )

    def _clear_autonomy_goal(self, reason: str) -> None:
        if self.active_autonomy_goal_id is not None:
            self.map_updates.append(
                {
                    "type": "autonomy_goal_end",
                    "goal_id": self.active_autonomy_goal_id,
                    "kind": self.active_autonomy_goal_kind,
                    "reason": reason,
                    "navigation_tick": self.navigation_tick,
                }
            )
        self.active_autonomy_goal_id = None
        self.active_autonomy_goal_kind = None
        self.active_autonomy_goal_started_at = self.navigation_tick

    def _reset_autonomy_after_progress(self, reason: str) -> None:
        if not hasattr(self, "recovery_level"):
            return
        if self.recovery_level != RecoveryLevel.NORMAL:
            self._set_recovery_level(
                RecoveryLevel.NORMAL,
                f"observed progress: {reason}",
            )
        self._clear_autonomy_goal(f"observed progress: {reason}")
        self._last_autonomy_story_epoch = self.story_epoch
        self._last_autonomy_evidence_marker = self._autonomy_evidence_marker()

    def _record_story_progress(self, event: str, telemetry) -> None:
        super()._record_story_progress(event, telemetry)
        self._reset_autonomy_after_progress(event)

    def _observe_room(self, telemetry) -> None:
        previous = self.observed_room
        super()._observe_room(telemetry)
        room = self._room_key(telemetry)
        if previous is not None and room != previous and hasattr(
            self, "active_autonomy_goal_id"
        ):
            self._clear_autonomy_goal("room changed")

    def _update_recovery_state(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> RecoveryLevel:
        marker = self._autonomy_evidence_marker()
        if marker[0] != self._last_autonomy_story_epoch:
            self._last_autonomy_story_epoch = marker[0]
            self._set_recovery_level(
                RecoveryLevel.NORMAL,
                "story epoch changed",
            )
            self._clear_autonomy_goal("story epoch changed")
        elif (
            self.recovery_level >= RecoveryLevel.BOUNDED_TEST
            and marker[1:] != self._last_autonomy_evidence_marker[1:]
        ):
            # A newly mapped cell/open edge/interactable/warp is actual new
            # information. Restart from evidence-level reasoning instead of
            # continuing an expensive recovery escalation blindly.
            self._set_recovery_level(
                RecoveryLevel.EVIDENCE,
                "new learned map evidence appeared",
            )
            self._clear_autonomy_goal("new learned map evidence appeared")

        self._last_autonomy_evidence_marker = marker
        desired = self._desired_recovery_level(room, cell)
        if desired == RecoveryLevel.FRONTIER:
            self._set_recovery_level(
                RecoveryLevel.FRONTIER,
                "reachable learned frontier remains",
            )
        elif desired > self.recovery_level:
            self._set_recovery_level(
                desired,
                f"recovery pressure reached {RECOVERY_LEVEL_NAMES[desired]}",
            )
        elif desired == RecoveryLevel.NORMAL and self.recovery_level <= RecoveryLevel.FRONTIER:
            self._set_recovery_level(RecoveryLevel.NORMAL, "normal exploration has options")

        age = max(0, self.navigation_tick - self.recovery_level_started_at)
        self.max_recovery_level_age = max(self.max_recovery_level_age, age)
        return self.recovery_level

    # ------------------------------------------------------------------
    # Uncertainty budgets
    # ------------------------------------------------------------------
    @staticmethod
    def _safe_int(value: object, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return default

    @staticmethod
    def _safe_float(value: object, default: float = 0.0) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError):
            return default
        return default if result != result else result

    @staticmethod
    def _belief_entropy(record: Mapping[str, object]) -> float:
        beliefs = record.get("guess_beliefs")
        if not isinstance(beliefs, Mapping):
            return 0.45
        probabilities = []
        for kind in (
            "possible_exit",
            "possible_character",
            "possible_interactable",
            "scenery",
        ):
            try:
                probabilities.append(max(0.0, float(beliefs.get(kind, 0.0))))
            except (TypeError, ValueError, OverflowError):
                probabilities.append(0.0)
        total = sum(probabilities)
        if total <= 0:
            return 0.45
        probabilities = [value / total for value in probabilities]
        entropy = -sum(value * log(value) for value in probabilities if value > 1e-9)
        return min(1.0, max(0.0, entropy / log(4.0)))

    def _visual_evidence_fingerprint(
        self,
        record: Mapping[str, object],
    ) -> tuple[object, ...]:
        beliefs = record.get("guess_beliefs")
        belief_buckets: tuple[int, ...] = ()
        if isinstance(beliefs, Mapping):
            belief_buckets = tuple(
                int(max(0.0, min(1.0, self._safe_float(beliefs.get(kind)))) * 5)
                for kind in (
                    "possible_exit",
                    "possible_character",
                    "possible_interactable",
                    "scenery",
                )
            )
        return (
            self.story_epoch,
            str(record.get("guess_semantic_state") or ""),
            str(record.get("entity_candidate_state") or ""),
            str(record.get("exit_candidate_state") or ""),
            min(3, self._safe_int(record.get("independent_views"))),
            min(3, self._safe_int(record.get("multi_view_sample_count"))),
            int(max(0.0, min(1.0, self._safe_float(record.get("multi_view_consistency"), 0.5))) * 4),
            belief_buckets,
            bool(record.get("confirmed_interactable_cell")),
            bool(record.get("confirmed_target_room")),
        )

    def _interaction_evidence_fingerprint(
        self,
        record: Mapping[str, object],
    ) -> tuple[object, ...]:
        return (
            self.story_epoch,
            str(record.get("usefulness") or "unknown"),
            str(record.get("classification") or "unknown"),
            str(record.get("last_outcome") or "unknown"),
            min(2, self._safe_int(record.get("choice_menus"))),
            bool(self._safe_int(record.get("progressions"))),
        )

    def _ensure_budget(
        self,
        key: str,
        fingerprint: tuple[object, ...],
        limit: int,
    ) -> BudgetState:
        limit = max(1, int(limit))
        existing = self.uncertainty_budgets.get(key)
        if existing is None:
            state = BudgetState(
                fingerprint=fingerprint,
                limit=limit,
                created_tick=self.navigation_tick,
                last_evidence_tick=self.navigation_tick,
            )
            self.uncertainty_budgets[key] = state
            return state
        if existing.fingerprint != fingerprint:
            state = BudgetState(
                fingerprint=fingerprint,
                limit=limit,
                created_tick=self.navigation_tick,
                last_evidence_tick=self.navigation_tick,
            )
            self.uncertainty_budgets[key] = state
            self.uncertainty_budget_evidence_resets += 1
            self.map_updates.append(
                {
                    "type": "autonomy_budget_reset",
                    "budget_key": key,
                    "reason": "new independent evidence changed the candidate fingerprint",
                    "limit": limit,
                    "navigation_tick": self.navigation_tick,
                }
            )
            return state
        existing.limit = limit
        return existing

    def _consume_budget(self, option: AutonomyOption) -> None:
        if option.budget_key is None:
            return
        state = self._ensure_budget(
            option.budget_key,
            option.fingerprint,
            option.budget_limit,
        )
        if state.remaining <= 0:
            return
        state.spent += 1
        self.uncertainty_budget_actions += 1
        if state.remaining == 0 and state.exhausted_tick is None:
            state.exhausted_tick = self.navigation_tick
            self.uncertainty_budget_exhaustions += 1
            self.map_updates.append(
                {
                    "type": "autonomy_budget_exhausted",
                    "budget_key": option.budget_key,
                    "kind": option.kind,
                    "spent": state.spent,
                    "limit": state.limit,
                    "navigation_tick": self.navigation_tick,
                }
            )

    # ------------------------------------------------------------------
    # Option scoring and collection
    # ------------------------------------------------------------------
    def _score_option(self, option: AutonomyOption) -> float:
        budget_penalty = 0.0
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
            budget_penalty = 2.2 * (state.spent / max(1, state.limit))
        score = (
            option.base_score
            + option.confidence * 3.0
            + option.information_value * 2.8
            + option.novelty * 2.0
            - min(12, option.distance) * 0.30
            - option.loop_risk * 4.0
            - option.failure_cost * 1.3
            - budget_penalty
        )
        option.score = round(score, 4)
        return option.score

    def _route_distance(
        self,
        room: str,
        start: tuple[int, int],
        target: tuple[int, int],
        *,
        allow_backtrack: bool = False,
    ) -> tuple[str, int] | None:
        if start == target:
            return self.direction, 0
        adjacency = self._adjacency(room, avoid_backtrack=not allow_backtrack)
        route = self._route_to_target(adjacency, start, target)
        if route is not None:
            return route
        return self._route_to_region_target(
            room,
            start,
            target,
            allow_backtrack=allow_backtrack,
        )

    def _collect_retry_interactions(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> list[AutonomyOption]:
        options: list[AutonomyOption] = []
        for key, interaction in self.interactables.items():
            if key[0] != room or not self._story_interaction_retryable(key):
                continue
            best: tuple[int, str, tuple[int, int], str] | None = None
            for approach in interaction.get("approaches", []):
                if not isinstance(approach, Mapping):
                    continue
                direction = str(approach.get("direction") or "")
                if direction not in DIRECTION_VECTORS:
                    continue
                source = (
                    self._safe_int(approach.get("x"), -999),
                    self._safe_int(approach.get("y"), -999),
                )
                if source == cell:
                    candidate = (0, direction, source, direction)
                else:
                    route = self._route_distance(room, cell, source)
                    if route is None:
                        continue
                    first, distance = route
                    candidate = (distance, first, source, direction)
                if best is None or candidate < best:
                    best = candidate
            if best is None:
                continue
            distance, first, source, direction = best
            usefulness = str(interaction.get("usefulness") or "unknown")
            confidence = {
                "choice_pending": 0.92,
                "progress": 0.98,
                "unknown": 0.62,
                "flavor": 0.35,
            }.get(usefulness, 0.55)
            budget_key = f"interaction:{key[0]}:{key[1]}:{key[2]}"
            options.append(
                AutonomyOption(
                    option_id=budget_key,
                    kind="retry_interaction",
                    required_level=RecoveryLevel.EVIDENCE,
                    base_score=10.2,
                    confidence=confidence,
                    information_value=0.78,
                    distance=distance,
                    failure_cost=min(3.0, self._safe_int(interaction.get("attempts")) * 0.35),
                    budget_key=budget_key,
                    budget_limit=min(18, max(6, distance + 5)),
                    fingerprint=self._interaction_evidence_fingerprint(interaction),
                    metadata={
                        "key": key,
                        "source": source,
                        "interaction_direction": direction,
                        "first_direction": first,
                        "usefulness": usefulness,
                    },
                )
            )
        return options

    def _active_visual_record(self, record: Mapping[str, object]) -> bool:
        state = str(record.get("guess_state") or "proposed")
        return state not in FINAL_GUESS_STATES and state != "cooldown"

    def _collect_visual_options(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> list[AutonomyOption]:
        options: list[AutonomyOption] = []
        visible = getattr(self, "current_visible_regions", set())
        for key, record in self.screen_regions.items():
            if key[0] != room or not self._active_visual_record(record):
                continue
            if self._visual_goal_is_cooling(key):
                continue
            hypothesis = str(record.get("hypothesis") or "")
            semantic = str(record.get("guess_semantic_state") or "")
            anchor = record.get("anchor_cell")
            anchor_cell = None
            if isinstance(anchor, (list, tuple)) and len(anchor) == 2:
                try:
                    anchor_cell = (int(anchor[0]), int(anchor[1]))
                except (TypeError, ValueError):
                    anchor_cell = None
            distance = (
                abs(anchor_cell[0] - cell[0]) + abs(anchor_cell[1] - cell[1])
                if anchor_cell is not None
                else abs(key[1] - self._region(cell)[0])
                + abs(key[2] - self._region(cell)[1])
            )
            confidence = max(0.0, min(1.0, self._safe_float(record.get("guess_confidence"))))
            entropy = self._belief_entropy(record)
            fingerprint = self._visual_evidence_fingerprint(record)
            guess_id = str(record.get("guess_id") or f"{room}@{key[1]},{key[2]}")

            if hypothesis in {"possible_character", "possible_interactable"}:
                if not self._visual_lead_is_actionable(
                    key,
                    dict(record),
                    hypotheses={hypothesis},
                ):
                    continue
                options.append(
                    AutonomyOption(
                        option_id=f"visual:{guess_id}",
                        kind="semantic_entity",
                        required_level=RecoveryLevel.EVIDENCE,
                        base_score=8.7,
                        confidence=confidence,
                        information_value=entropy * 0.72,
                        distance=distance,
                        failure_cost=self._safe_int(record.get("failed_approaches")) * 0.55,
                        budget_key=f"visual:{guess_id}",
                        budget_limit=min(12, max(4, distance + 3)),
                        fingerprint=fingerprint,
                        metadata={"key": key, "hypothesis": hypothesis},
                    )
                )
            elif hypothesis == "possible_exit" and self._visual_exit_is_actionable(key, dict(record)):
                options.append(
                    AutonomyOption(
                        option_id=f"visual:{guess_id}",
                        kind="semantic_exit",
                        required_level=RecoveryLevel.EVIDENCE,
                        base_score=8.9,
                        confidence=confidence,
                        information_value=entropy * 0.55,
                        distance=distance,
                        failure_cost=self._safe_int(record.get("failed_approaches")) * 0.65,
                        budget_key=f"visual:{guess_id}",
                        budget_limit=min(10, max(4, distance + 2)),
                        fingerprint=fingerprint,
                        metadata={"key": key, "hypothesis": hypothesis},
                    )
                )

            if (
                semantic == UNKNOWN_BUT_INTERESTING
                and (room, key[1], key[2]) in visible
                and self._safe_int(record.get("information_probe_attempts")) < MAX_INFORMATION_PROBES
            ):
                options.append(
                    AutonomyOption(
                        option_id=f"information:{guess_id}",
                        kind="information_probe",
                        required_level=RecoveryLevel.EVIDENCE,
                        base_score=7.4,
                        confidence=confidence,
                        information_value=entropy,
                        distance=distance,
                        budget_key=f"information:{guess_id}",
                        budget_limit=MAX_INFORMATION_PROBES,
                        fingerprint=fingerprint,
                        metadata={"key": key},
                    )
                )
        return options

    def _collect_weak_entity_options(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> list[AutonomyOption]:
        options: list[AutonomyOption] = []
        for score, key, source, direction, first_direction in self._weak_entity_routes(room, cell):
            record = self.screen_regions.get(key)
            if record is None or not single_side_entity_candidate(record):
                continue
            guess_id = str(record.get("guess_id") or f"{room}@{key[1]},{key[2]}")
            distance = int(score[1]) if len(score) > 1 else 0
            stability = entity_candidate_state(record) == "single_side_stable"
            options.append(
                AutonomyOption(
                    option_id=f"weak_entity:{guess_id}",
                    kind="weak_entity_test",
                    required_level=RecoveryLevel.BOUNDED_TEST,
                    base_score=6.2 + (0.45 if stability else 0.0),
                    confidence=max(0.0, min(0.65, self._safe_float(record.get("guess_confidence")))),
                    information_value=0.72,
                    distance=distance,
                    failure_cost=self._safe_int(record.get("failed_approaches")) * 0.8,
                    budget_key=f"weak_entity:{guess_id}",
                    budget_limit=min(6, max(2, distance + 2)),
                    fingerprint=self._visual_evidence_fingerprint(record),
                    metadata={
                        "key": key,
                        "source": source,
                        "interaction_direction": direction,
                        "first_direction": first_direction,
                    },
                )
            )
        return options

    def _collect_geometry_exit_options(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> list[AutonomyOption]:
        options: list[AutonomyOption] = []
        for key, record in self.screen_regions.items():
            if key[0] != room or not self._active_visual_record(record):
                continue
            state = str(record.get("exit_candidate_state") or "")
            if not record.get("path_continuation") and state not in {
                "geometry_candidate",
                "needs_approach_evidence",
                "visual_candidate",
            }:
                continue
            direction = str(record.get("edge_hint") or "")
            anchor = record.get("anchor_cell")
            if direction not in DIRECTION_VECTORS or not isinstance(anchor, (list, tuple)) or len(anchor) != 2:
                continue
            try:
                probe = (room, int(anchor[0]), int(anchor[1]), direction)
            except (TypeError, ValueError):
                continue
            if self.exit_probes[probe] >= MAX_EXIT_PROBES:
                continue
            if self._blocked_near(room, (probe[1], probe[2]), direction):
                continue
            route = self._route_to_exit_approach(
                self._adjacency(room),
                cell,
                probe,
            )
            if self._within_exit_probe_approach(cell, probe):
                distance = 0
            elif route is not None:
                distance = route[1]
            else:
                continue
            guess_id = str(record.get("guess_id") or f"{room}@{key[1]},{key[2]}")
            approach = self._straight_approach_length(room, (probe[1], probe[2]), direction)
            options.append(
                AutonomyOption(
                    option_id=f"geometry_exit:{guess_id}:{direction}",
                    kind="geometry_exit_test",
                    required_level=RecoveryLevel.BOUNDED_TEST,
                    base_score=6.4 + min(1.0, approach * 0.25),
                    confidence=max(0.0, min(0.7, self._safe_float(record.get("exit_candidate_visual_score"), 0.35))),
                    information_value=0.70,
                    distance=distance,
                    failure_cost=self._safe_int(record.get("failed_approaches")) * 0.7,
                    budget_key=f"geometry_exit:{guess_id}:{direction}",
                    budget_limit=min(8, max(3, distance + 2)),
                    fingerprint=self._visual_evidence_fingerprint(record),
                    metadata={"key": key, "probe": probe},
                )
            )
        return options

    def _warp_loop_risk(self, warp: Warp) -> float:
        room, _x, _y, _action, target, _tx, _ty = warp
        metadata = self.world.portal_metadata(warp) or {}
        return_tendency = self._safe_float(metadata.get("return_tendency"), 0.0)
        recorded_loop = self._safe_float(metadata.get("loop_risk"), 0.0)
        recent_hits = sum(candidate == target for candidate in self.recent_rooms)
        entry_penalty = 0.25 if self.room_entry_from.get(room) == target else 0.0
        suppressed_penalty = 0.35 if frozenset((room, target)) in self.suppressed_room_links else 0.0
        return min(
            1.0,
            max(recorded_loop, return_tendency * 0.65)
            + min(0.30, recent_hits * 0.08)
            + entry_penalty
            + suppressed_penalty,
        )

    def _warp_route(
        self,
        room: str,
        cell: tuple[int, int],
        warp: Warp,
        *,
        allow_backtrack: bool,
    ) -> tuple[str, int] | None:
        source_room, source_x, source_y, action, target_room, _tx, _ty = warp
        if source_room != room or action not in DIRECTION_VECTORS:
            return None
        if self._run21_link_hold_active(room, target_room):
            return None
        source = (source_x, source_y)
        if source == cell:
            if self._blocked_near(room, cell, action):
                return None
            if self._is_entry_warp_direction(room, cell, action):
                return None
            return action, 0
        return self._route_distance(
            room,
            cell,
            source,
            allow_backtrack=allow_backtrack,
        )

    def _collect_warp_options(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> list[AutonomyOption]:
        options: list[AutonomyOption] = []
        for warp, crossings in self._reliable_warps():
            if warp[0] != room:
                continue
            target = warp[4]
            link = frozenset((room, target))
            role = self._portal_role(warp)
            is_backtrack = (
                self.room_entry_from.get(room) == target
                or link in self.suppressed_room_links
                or role == "loop_suppressed"
            )
            if self._run21_link_hold_active(room, target):
                self.loop_risk_avoids += 1
                continue
            if self._link_is_cooling_down(room, target):
                continue
            level = (
                RecoveryLevel.CONTROLLED_BACKTRACK
                if is_backtrack
                else RecoveryLevel.EVIDENCE
                if role == "progression"
                else RecoveryLevel.LEARNED_ROUTE
            )
            route = self._warp_route(
                room,
                cell,
                warp,
                allow_backtrack=is_backtrack,
            )
            if route is None:
                continue
            _first, distance = route
            confidence = {
                "progression": 0.98,
                "new_area": 0.78,
                "unknown": 0.48,
                "loop_suppressed": 0.22,
            }.get(role, 0.45)
            target_regions = len(
                {
                    self._region((x, y))
                    for seen_room, x, y in self.seen_cells
                    if seen_room == target
                }
            )
            novelty = 1.0 / (1.0 + target_regions)
            loop_risk = self._warp_loop_risk(warp)
            option_id = (
                f"warp:{room}:{warp[1]}:{warp[2]}:{warp[3]}:{target}"
            )
            options.append(
                AutonomyOption(
                    option_id=option_id,
                    kind="controlled_backtrack" if is_backtrack else "learned_warp",
                    required_level=level,
                    base_score=(
                        11.5 if role == "progression"
                        else 8.8 if role == "new_area"
                        else 7.2 if not is_backtrack
                        else 5.6
                    ),
                    confidence=confidence,
                    information_value=0.28 if crossings > 1 else 0.58,
                    novelty=novelty,
                    distance=distance,
                    loop_risk=loop_risk,
                    failure_cost=self._safe_int((self.world.portal_metadata(warp) or {}).get("loop_suppressions")) * 0.45,
                    metadata={
                        "warp": warp,
                        "role": role,
                        "target_room": target,
                        "crossings": crossings,
                        "allow_backtrack": is_backtrack,
                    },
                )
            )
        return options

    def _room_frontier_count(self, room: str) -> int:
        count = 0
        for seen_room, x, y in self.seen_cells:
            if seen_room != room:
                continue
            cell = (x, y)
            if any(
                self._direction_is_unexplored(room, cell, direction)
                for direction in DIRECTION_VECTORS
            ):
                count += 1
                if count >= 6:
                    break
        return count

    def _room_opportunity_score(self, room: str) -> float:
        frontiers = self._room_frontier_count(room)
        unresolved = sum(
            key[0] == room
            and str(record.get("guess_state") or "proposed") not in FINAL_GUESS_STATES
            and str(record.get("guess_semantic_state") or "")
            in {
                UNKNOWN_BUT_INTERESTING,
                "possible_character",
                "possible_interactable",
                "possible_exit",
            }
            for key, record in self.screen_regions.items()
        )
        retries = sum(
            key[0] == room and self._story_interaction_retryable(key)
            for key in self.interactables
        )
        regions = len(
            {
                self._region((x, y))
                for seen_room, x, y in self.seen_cells
                if seen_room == room
            }
        )
        novelty = 1.0 / (1.0 + regions)
        return (
            min(6, frontiers) * 1.15
            + min(4, unresolved) * 0.75
            + min(3, retries) * 1.45
            + novelty * 2.0
        )

    def _collect_long_horizon_options(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> list[AutonomyOption]:
        outgoing: dict[str, list[Warp]] = {}
        for warp, _crossings in self._reliable_warps():
            if warp[3] in DIRECTION_VECTORS:
                outgoing.setdefault(warp[0], []).append(warp)
        for values in outgoing.values():
            values.sort()

        queue: deque[tuple[str, tuple[Warp, ...]]] = deque([(room, ())])
        visited_depth: dict[str, int] = {room: 0}
        options: list[AutonomyOption] = []
        while queue:
            current, path = queue.popleft()
            if len(path) >= AUTONOMY_MAX_LONG_HORIZON_HOPS:
                continue
            for warp in outgoing.get(current, []):
                target = warp[4]
                new_path = (*path, warp)
                depth = len(new_path)
                if target == room:
                    continue
                previous_depth = visited_depth.get(target)
                if previous_depth is not None and previous_depth <= depth:
                    continue
                visited_depth[target] = depth
                queue.append((target, new_path))
                if depth < 2:
                    continue

                first = new_path[0]
                first_target = first[4]
                first_link = frozenset((room, first_target))
                backtrack = (
                    self.room_entry_from.get(room) == first_target
                    or first_link in self.suppressed_room_links
                )
                if self._run21_link_hold_active(room, first_target):
                    continue
                if self._link_is_cooling_down(room, first_target):
                    continue
                route = self._warp_route(
                    room,
                    cell,
                    first,
                    allow_backtrack=backtrack,
                )
                if route is None:
                    continue
                _direction, distance = route
                opportunity = self._room_opportunity_score(target)
                if opportunity <= 0.25:
                    continue
                path_loop = sum(self._warp_loop_risk(edge) for edge in new_path) / depth
                options.append(
                    AutonomyOption(
                        option_id=f"long_horizon:{target}:{first[4]}:{first[1]}:{first[2]}",
                        kind="long_horizon_route",
                        required_level=(
                            RecoveryLevel.CONTROLLED_BACKTRACK
                            if backtrack
                            else RecoveryLevel.LEARNED_ROUTE
                        ),
                        base_score=7.0 + min(4.0, opportunity * 0.45),
                        confidence=0.58,
                        information_value=min(1.0, opportunity / 7.0),
                        novelty=min(1.0, opportunity / 6.0),
                        distance=distance + depth - 1,
                        loop_risk=path_loop,
                        metadata={
                            "warp": first,
                            "target_room": target,
                            "path_rooms": [room] + [edge[4] for edge in new_path],
                            "path_hops": depth,
                            "opportunity_score": round(opportunity, 3),
                            "allow_backtrack": backtrack,
                        },
                    )
                )
        return options

    def _collect_broad_reset_option(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> AutonomyOption:
        return AutonomyOption(
            option_id=f"broad_reset:{room}:{self._region(cell)[0]}:{self._region(cell)[1]}",
            kind="broad_reset",
            required_level=RecoveryLevel.BROAD_RESET,
            base_score=2.0,
            information_value=0.45,
            novelty=0.4,
            metadata={},
        )

    def _collect_recovery_options(
        self,
        room: str,
        cell: tuple[int, int],
        level: RecoveryLevel,
    ) -> list[AutonomyOption]:
        options = []
        options.extend(self._collect_retry_interactions(room, cell))
        options.extend(self._collect_visual_options(room, cell))
        options.extend(self._collect_weak_entity_options(room, cell))
        options.extend(self._collect_geometry_exit_options(room, cell))
        options.extend(self._collect_warp_options(room, cell))
        if level >= RecoveryLevel.LEARNED_ROUTE:
            options.extend(self._collect_long_horizon_options(room, cell))
        if level >= RecoveryLevel.BROAD_RESET:
            options.append(self._collect_broad_reset_option(room, cell))

        eligible: list[AutonomyOption] = []
        for option in options:
            if option.required_level > level:
                continue
            score = self._score_option(option)
            if score == float("-inf"):
                continue
            eligible.append(option)
        eligible.sort(key=lambda option: (-option.score, option.distance, option.option_id))
        return eligible

    # ------------------------------------------------------------------
    # Goal commitment and execution
    # ------------------------------------------------------------------
    def _option_payload(
        self,
        option: AutonomyOption,
        *,
        selected: bool = False,
    ) -> dict[str, object]:
        return {
            "id": option.option_id,
            "kind": option.kind,
            "required_level": RECOVERY_LEVEL_NAMES[option.required_level],
            "score": option.score,
            "confidence": round(option.confidence, 3),
            "information_value": round(option.information_value, 3),
            "novelty": round(option.novelty, 3),
            "distance": option.distance,
            "loop_risk": round(option.loop_risk, 3),
            "failure_cost": round(option.failure_cost, 3),
            "budget_key": option.budget_key,
            "budget_limit": option.budget_limit,
            "budget_spent": option.budget_spent,
            "budget_remaining": option.budget_remaining,
            "selected": selected,
            "metadata": {
                key: value
                for key, value in option.metadata.items()
                if key not in {"warp", "key", "source", "probe"}
            },
        }

    def _choose_committed_option(
        self,
        options: list[AutonomyOption],
    ) -> AutonomyOption:
        best = options[0]
        self.last_autonomy_commitment_hold = False
        active = next(
            (
                option
                for option in options
                if option.option_id == self.active_autonomy_goal_id
            ),
            None,
        )
        if active is not None:
            age = max(0, self.navigation_tick - self.active_autonomy_goal_started_at)
            if (
                age < AUTONOMY_GOAL_COMMIT_STEPS
                and active.score >= best.score - AUTONOMY_GOAL_BREAK_MARGIN
            ):
                self.autonomy_goal_commitment_holds += 1
                self.last_autonomy_commitment_hold = True
                return active
            if best.option_id != active.option_id and best.score > active.score + AUTONOMY_GOAL_BREAK_MARGIN:
                self.autonomy_goal_breaks_for_stronger_evidence += 1
        return best

    def _activate_goal(self, option: AutonomyOption) -> None:
        if self.active_autonomy_goal_id == option.option_id:
            return
        previous = self.active_autonomy_goal_id
        if previous is None:
            self.autonomy_goal_activations += 1
        else:
            self.autonomy_goal_switches += 1
        self.active_autonomy_goal_id = option.option_id
        self.active_autonomy_goal_kind = option.kind
        self.active_autonomy_goal_started_at = self.navigation_tick
        self.map_updates.append(
            {
                "type": "autonomy_goal",
                "goal_id": option.option_id,
                "kind": option.kind,
                "previous_goal_id": previous,
                "score": option.score,
                "recovery_level": RECOVERY_LEVEL_NAMES[self.recovery_level],
                "navigation_tick": self.navigation_tick,
            }
        )

    def _execute_visual_option(
        self,
        option: AutonomyOption,
        room: str,
        cell: tuple[int, int],
    ) -> tuple[str, int, str] | None:
        key = option.metadata.get("key")
        hypothesis = str(option.metadata.get("hypothesis") or "")
        if not isinstance(key, tuple) or len(key) != 3 or hypothesis not in {
            "possible_character",
            "possible_interactable",
            "possible_exit",
        }:
            return None
        self.visual_goal = key
        plan = self._direction_to_visual_hypothesis(
            room,
            cell,
            story_focus=True,
            allowed_hypotheses={hypothesis},
        )
        if plan is None:
            return None
        direction, _actual_hypothesis, region = plan
        return (
            direction,
            1,
            f"autonomy evidence: pursue {hypothesis.replace('_', ' ')} near region {region}",
        )

    def _execute_option(
        self,
        option: AutonomyOption,
        room: str,
        cell: tuple[int, int],
    ) -> tuple[str, int, str] | None:
        if option.kind == "retry_interaction":
            source = option.metadata.get("source")
            interaction_direction = str(option.metadata.get("interaction_direction") or "")
            first_direction = str(option.metadata.get("first_direction") or "")
            if not isinstance(source, tuple) or interaction_direction not in DIRECTION_VECTORS:
                return None
            direction = interaction_direction if source == cell else first_direction
            if direction not in DIRECTION_VECTORS:
                return None
            return direction, 1, "autonomy evidence: retry learned response-producing interaction"

        if option.kind in {"semantic_entity", "semantic_exit"}:
            return self._execute_visual_option(option, room, cell)

        if option.kind == "information_probe":
            return information_gain_probe_plan(self, room, cell)

        if option.kind == "weak_entity_test":
            key = option.metadata.get("key")
            source = option.metadata.get("source")
            interaction_direction = str(option.metadata.get("interaction_direction") or "")
            if (
                not isinstance(key, tuple)
                or not isinstance(source, tuple)
                or interaction_direction not in DIRECTION_VECTORS
            ):
                return None
            if not self._set_weak_entity_probe((key, source, interaction_direction)):
                return None
            return self._plan_weak_entity_probe(room, cell)

        if option.kind == "geometry_exit_test":
            probe = option.metadata.get("probe")
            if not isinstance(probe, tuple) or len(probe) != 4:
                return None
            self.exit_search_goal = probe
            if self._within_exit_probe_approach(cell, probe):
                self.exit_probes[probe] += 1
                return (
                    str(probe[3]),
                    2,
                    "autonomy bounded test: probe observed boundary continuation",
                )
            route = self._route_to_exit_approach(self._adjacency(room), cell, probe)
            if route is None:
                return None
            return (
                route[0],
                1,
                "autonomy bounded test: approach observed boundary continuation",
            )

        if option.kind in {"learned_warp", "controlled_backtrack", "long_horizon_route"}:
            warp = option.metadata.get("warp")
            if not isinstance(warp, tuple) or len(warp) != 7:
                return None
            allow_backtrack = bool(option.metadata.get("allow_backtrack"))
            route = self._warp_route(room, cell, warp, allow_backtrack=allow_backtrack)
            if route is None:
                return None
            direction, _distance = route
            if option.kind == "long_horizon_route":
                self.long_horizon_plans += 1
                path = option.metadata.get("path_rooms")
                return (
                    direction,
                    1,
                    f"autonomy learned-map plan: move toward observed opportunity path {path}",
                )
            return (
                direction,
                1,
                "autonomy recovery: use observed warp "
                f"toward {option.metadata.get('target_room')} ({option.metadata.get('role', 'unknown')})",
            )

        if option.kind == "broad_reset":
            self.broad_recovery_resets += 1
            direction = self._least_visited_direction(room, cell, self.direction)
            return (
                direction,
                1,
                "autonomy broad reset: diversify through least-visited safe learned direction",
            )
        return None

    def _plan_autonomy_recovery(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> tuple[str, int, str] | None:
        level = self.recovery_level
        options = self._collect_recovery_options(room, cell, level)
        while not options and level < RecoveryLevel.BROAD_RESET:
            level = RecoveryLevel(level + 1)
            self.empty_tier_escalations += 1
            self._set_recovery_level(
                level,
                "current recovery tier had no unexhausted learned options",
            )
            options = self._collect_recovery_options(room, cell, level)

        if not options:
            return None

        selected = self._choose_committed_option(options)
        self._activate_goal(selected)
        plan = self._execute_option(selected, room, cell)
        if plan is None:
            # A candidate can become unreachable between ranking and execution.
            # Spend one bounded attempt, close this goal, and let the existing
            # planner provide a safe action for this step.
            self._consume_budget(selected)
            self._clear_autonomy_goal("selected option became unreachable")
            self.last_autonomy_selected_id = None
            self.last_ranked_autonomy_options = [
                self._option_payload(option, selected=False)
                for option in options[:AUTONOMY_TOP_OPTION_LOG]
            ]
            return None

        self._consume_budget(selected)
        self.autonomy_selections[selected.kind] = self.autonomy_selections.get(selected.kind, 0) + 1
        self.last_autonomy_selected_id = selected.option_id
        self.last_ranked_autonomy_options = [
            self._option_payload(
                option,
                selected=option.option_id == selected.option_id,
            )
            for option in options[:AUTONOMY_TOP_OPTION_LOG]
        ]
        return plan

    def _plan_exploration(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> tuple[str, int, str]:
        level = self._update_recovery_state(room, cell)
        if level <= RecoveryLevel.FRONTIER:
            self.last_ranked_autonomy_options = []
            self.last_autonomy_selected_id = None
            self.last_autonomy_commitment_hold = False
            return super()._plan_exploration(room, cell)

        plan = self._plan_autonomy_recovery(room, cell)
        if plan is not None:
            return plan
        return super()._plan_exploration(room, cell)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------
    def autonomy_snapshot(self) -> dict[str, object]:
        active_budget = None
        if self.active_autonomy_goal_id is not None:
            for state_key, state in self.uncertainty_budgets.items():
                if state_key in self.active_autonomy_goal_id or self.active_autonomy_goal_id in state_key:
                    active_budget = {
                        "key": state_key,
                        "spent": state.spent,
                        "limit": state.limit,
                        "remaining": state.remaining,
                    }
                    break
        return {
            "version": AUTONOMY_VERSION,
            "recovery_level": RECOVERY_LEVEL_NAMES[self.recovery_level],
            "recovery_level_value": int(self.recovery_level),
            "recovery_reason": self.recovery_reason,
            "recovery_level_age": max(0, self.navigation_tick - self.recovery_level_started_at),
            "story_epoch": self.story_epoch,
            "story_stall_steps": self.story_stall_steps,
            "active_goal_id": self.active_autonomy_goal_id,
            "active_goal_kind": self.active_autonomy_goal_kind,
            "active_goal_age": (
                max(0, self.navigation_tick - self.active_autonomy_goal_started_at)
                if self.active_autonomy_goal_id is not None
                else 0
            ),
            "selected_option_id": self.last_autonomy_selected_id,
            "commitment_hold": self.last_autonomy_commitment_hold,
            "active_budget": active_budget,
            "ranked_options": list(self.last_ranked_autonomy_options),
        }

    def prediction_snapshot(self) -> dict[str, object]:
        snapshot = super().prediction_snapshot()
        snapshot["autonomy"] = self.autonomy_snapshot()
        return snapshot

    def summary(self) -> dict:
        summary = super().summary()
        summary.update(
            {
                "autonomy_version": AUTONOMY_VERSION,
                "recovery_level": RECOVERY_LEVEL_NAMES[self.recovery_level],
                "max_recovery_level": RECOVERY_LEVEL_NAMES[self.max_recovery_level],
                "recovery_level_changes": self.recovery_level_changes,
                "recovery_escalations": self.recovery_escalations,
                "recovery_deescalations": self.recovery_deescalations,
                "max_recovery_level_age": max(
                    self.max_recovery_level_age,
                    max(0, self.navigation_tick - self.recovery_level_started_at),
                ),
                "uncertainty_budget_actions": self.uncertainty_budget_actions,
                "uncertainty_budget_exhaustions": self.uncertainty_budget_exhaustions,
                "uncertainty_budget_evidence_resets": self.uncertainty_budget_evidence_resets,
                "active_uncertainty_budgets": sum(
                    state.remaining > 0 for state in self.uncertainty_budgets.values()
                ),
                "autonomy_goal_activations": self.autonomy_goal_activations,
                "autonomy_goal_switches": self.autonomy_goal_switches,
                "autonomy_goal_commitment_holds": self.autonomy_goal_commitment_holds,
                "autonomy_goal_breaks_for_stronger_evidence": (
                    self.autonomy_goal_breaks_for_stronger_evidence
                ),
                "autonomy_selections": dict(sorted(self.autonomy_selections.items())),
                "long_horizon_plans": self.long_horizon_plans,
                "loop_risk_avoids": self.loop_risk_avoids,
                "broad_recovery_resets": self.broad_recovery_resets,
                "empty_tier_escalations": self.empty_tier_escalations,
            }
        )
        return summary


__all__ = [
    "AUTONOMY_VERSION",
    "AutonomyOption",
    "AutonomyV1Explorer",
    "BudgetState",
    "RECOVERY_LEVEL_NAMES",
    "RecoveryLevel",
]
