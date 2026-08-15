from __future__ import annotations

from pathlib import Path

from .autonomy_v1 import AutonomyOption, AutonomyV1Explorer, RecoveryLevel
from .policy import DIRECTION_VECTORS
from .warp_classification_v2 import _room_completion_pressure


AUTONOMY_FRONTIER_ESCALATION_GRACE = 48


class AutonomyV1RuntimeExplorer(AutonomyV1Explorer):
    """Final runtime composition for Autonomy v1.

    These overrides preserve lower-layer lifecycle contracts that are easy to
    miss when coordinating options above Run21: expired visual cooldowns are
    eligible again, a learned interaction retry exposes its region as a
    concrete interaction goal before moving into the learned approach edge,
    and a reachable frontier cannot pin recovery forever if repeatedly
    revisiting it produces no new learned evidence.
    """

    def __init__(self, seed: int = 0, memory_path: Path | None = None):
        super().__init__(seed, memory_path)
        self._frontier_pressure_since: int | None = None
        self._frontier_escalated = False
        self.frontier_recovery_escalations = 0
        self.frontier_ranked_actions = 0

    def _active_visual_record(self, record) -> bool:
        state = str(record.get("guess_state") or "proposed")
        return state not in {"confirmed", "rejected", "retired"}

    def _prepare_retry_interaction_goal(
        self,
        option: AutonomyOption,
    ) -> None:
        key = option.metadata.get("key")
        if not isinstance(key, tuple) or len(key) != 3:
            return
        room, target_x, target_y = key
        visual_key = (room, *self._region((target_x, target_y)))
        record = self.screen_regions.setdefault(
            visual_key,
            {"views": 1, "interest": 0.0, "inspections": 0},
        )
        # This does not assert that the target is a character. It preserves the
        # legacy routing surface for an interaction that the agent itself has
        # already observed and whose response space is still unresolved.
        record["hypothesis"] = "possible_character"
        record["choice_retry"] = True
        self._refresh_visual_guess_metadata(
            (visual_key[1], visual_key[2]),
            record,
        )
        self.visual_goal = visual_key

    def _reset_frontier_pressure(self) -> None:
        self._frontier_pressure_since = None
        self._frontier_escalated = False

    def _desired_recovery_level(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> RecoveryLevel:
        desired = super()._desired_recovery_level(room, cell)
        if desired is not RecoveryLevel.FRONTIER:
            self._reset_frontier_pressure()
            return desired

        if self._frontier_pressure_since is None:
            self._frontier_pressure_since = self.navigation_tick
        age = max(0, self.navigation_tick - self._frontier_pressure_since)
        if age < AUTONOMY_FRONTIER_ESCALATION_GRACE:
            return RecoveryLevel.FRONTIER

        # A frontier remains a high-value option, but it no longer blocks the
        # rest of the recovery ladder forever. Once this grace period expires,
        # use the same generic stall thresholds as frontier-exhausted recovery.
        # The frontier itself is added to the unified ranked option set below.
        stall = max(0, int(self.story_stall_steps))
        try:
            completion_pressure = _room_completion_pressure(self, room)
        except AttributeError:
            completion_pressure = False

        if not self._frontier_escalated:
            self._frontier_escalated = True
            self.frontier_recovery_escalations += 1

        if stall >= 260:
            return RecoveryLevel.BROAD_RESET
        if stall >= 180:
            return RecoveryLevel.CONTROLLED_BACKTRACK
        if completion_pressure or stall >= 96:
            return RecoveryLevel.LEARNED_ROUTE
        if stall >= 72:
            return RecoveryLevel.BOUNDED_TEST
        return RecoveryLevel.EVIDENCE

    def _update_recovery_state(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> RecoveryLevel:
        # A newly learned cell/open edge/interactable/warp is evidence that the
        # frontier attempt taught the agent something. Give frontier-first
        # navigation a fresh grace period instead of carrying old pressure over.
        marker = self._autonomy_evidence_marker()
        previous_marker = getattr(self, "_last_autonomy_evidence_marker", marker)
        if marker != previous_marker:
            self._reset_frontier_pressure()
        return super()._update_recovery_state(room, cell)

    def _collect_frontier_options(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> list[AutonomyOption]:
        options: list[AutonomyOption] = []
        avoid = self._loop_avoid_directions(room, cell)
        evidence = self._autonomy_evidence_marker()

        for direction in DIRECTION_VECTORS:
            if not self._direction_is_unexplored(room, cell, direction):
                continue
            if direction in avoid or self._blocked_near(room, cell, direction):
                continue
            if self._is_entry_warp_direction(room, cell, direction):
                continue
            options.append(
                AutonomyOption(
                    option_id=(
                        f"frontier:{room}:{cell[0]}:{cell[1]}:{direction}"
                    ),
                    kind="frontier",
                    required_level=RecoveryLevel.EVIDENCE,
                    base_score=10.1,
                    confidence=0.96,
                    information_value=0.95,
                    novelty=0.90,
                    distance=0,
                    budget_key=(
                        f"frontier:{room}:{cell[0]}:{cell[1]}:{direction}"
                    ),
                    budget_limit=4,
                    fingerprint=(*evidence, direction),
                    metadata={"direction": direction, "local": True},
                )
            )

        allowed_first = set(DIRECTION_VECTORS) - avoid
        route = self._route_to_nearest_frontier(
            room,
            cell,
            allowed_first=allowed_first,
        )
        if (
            route is not None
            and route in DIRECTION_VECTORS
            and not self._blocked_near(room, cell, route)
            and not self._is_entry_warp_direction(room, cell, route)
        ):
            options.append(
                AutonomyOption(
                    option_id=f"frontier_route:{room}:{route}",
                    kind="frontier_route",
                    required_level=RecoveryLevel.EVIDENCE,
                    base_score=9.6,
                    confidence=0.92,
                    information_value=0.84,
                    novelty=0.76,
                    distance=1,
                    budget_key=f"frontier_route:{room}:{route}",
                    budget_limit=8,
                    fingerprint=(*evidence, route),
                    metadata={"direction": route, "local": False},
                )
            )
        return options

    def _collect_recovery_options(
        self,
        room: str,
        cell: tuple[int, int],
        level: RecoveryLevel,
    ) -> list[AutonomyOption]:
        options = super()._collect_recovery_options(room, cell, level)
        if level < RecoveryLevel.EVIDENCE:
            return options

        existing = {option.option_id for option in options}
        for option in self._collect_frontier_options(room, cell):
            if option.option_id in existing:
                continue
            score = self._score_option(option)
            if score == float("-inf"):
                continue
            options.append(option)
        options.sort(key=lambda option: (-option.score, option.distance, option.option_id))
        return options

    def _execute_option(
        self,
        option: AutonomyOption,
        room: str,
        cell: tuple[int, int],
    ):
        if option.kind == "retry_interaction":
            source = option.metadata.get("source")
            interaction_direction = str(
                option.metadata.get("interaction_direction") or ""
            )
            first_direction = str(option.metadata.get("first_direction") or "")
            if (
                not isinstance(source, tuple)
                or interaction_direction not in DIRECTION_VECTORS
            ):
                return None
            self._prepare_retry_interaction_goal(option)
            direction = interaction_direction if source == cell else first_direction
            if direction not in DIRECTION_VECTORS:
                return None
            return (
                direction,
                1,
                "autonomy evidence: retry learned response-producing interaction",
            )
        if option.kind in {"frontier", "frontier_route"}:
            direction = str(option.metadata.get("direction") or "")
            if direction not in DIRECTION_VECTORS:
                return None
            self.frontier_ranked_actions += 1
            return (
                direction,
                1,
                "autonomy frontier: continue bounded learned frontier exploration",
            )
        return super()._execute_option(option, room, cell)

    def _reset_autonomy_after_progress(self, reason: str) -> None:
        self._reset_frontier_pressure()
        super()._reset_autonomy_after_progress(reason)

    def _set_recovery_level(
        self,
        level: RecoveryLevel,
        reason: str,
    ) -> None:
        previous = getattr(self, "recovery_level", RecoveryLevel.NORMAL)
        super()._set_recovery_level(level, reason)
        if (
            previous > RecoveryLevel.FRONTIER
            and RecoveryLevel(level) <= RecoveryLevel.FRONTIER
            and getattr(self, "active_autonomy_goal_id", None) is not None
        ):
            self._clear_autonomy_goal(
                "normal/frontier evidence became available"
            )

    def _option_payload(
        self,
        option: AutonomyOption,
        *,
        selected: bool = False,
    ) -> dict[str, object]:
        payload = super()._option_payload(option, selected=selected)
        payload["base_score"] = round(option.base_score, 4)
        return payload

    def summary(self) -> dict:
        summary = super().summary()
        summary["frontier_recovery_escalations"] = self.frontier_recovery_escalations
        summary["frontier_ranked_actions"] = self.frontier_ranked_actions
        summary["frontier_escalation_grace"] = AUTONOMY_FRONTIER_ESCALATION_GRACE
        return summary


__all__ = [
    "AUTONOMY_FRONTIER_ESCALATION_GRACE",
    "AutonomyV1RuntimeExplorer",
]
