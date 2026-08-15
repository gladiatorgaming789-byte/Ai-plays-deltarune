from __future__ import annotations

from .autonomy_v1 import AutonomyOption, AutonomyV1Explorer, RecoveryLevel
from .policy import DIRECTION_VECTORS


class AutonomyV1RuntimeExplorer(AutonomyV1Explorer):
    """Final runtime composition for Autonomy v1.

    These overrides preserve two lower-layer lifecycle contracts that are easy
    to miss when coordinating options above Run21: expired visual cooldowns are
    eligible again, and a learned interaction retry must expose its region as a
    concrete interaction goal before moving into the learned approach edge.
    """

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
        return super()._execute_option(option, room, cell)

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


__all__ = ["AutonomyV1RuntimeExplorer"]
