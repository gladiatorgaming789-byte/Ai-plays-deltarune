from __future__ import annotations

from pathlib import Path

from .policy import DIRECTION_VECTORS
from .run8_explorer import (
    ANIMATED_CHARACTER_BONUS,
    ANIMATED_SPRITE_MIN_COLORFULNESS,
)
from .run9_explorer import ANIMATED_EVIDENCE_NOTE, SAME_VIEW_ANIMATION_CHANGES
from .run13_screen_regions import FLOOR_EVIDENCE_PREFIX
from .run14_explorer import (
    DOORWAY_DIRECTIONS,
    MAX_DOORWAY_PROBE_STEPS,
    Run14Explorer,
)
from .telemetry import TelemetrySample
from .world_model import CELL_SIZE


LONG_SCROLLING_ROOM_RATIO = 1.35
MAX_UNTESTED_VISUAL_SELECTIONS = 16
MIN_LOCAL_FLOOR_EXIT_SPAN = 0.055
DOORWAY_APPROACH_DEPTH_CELLS = 6
DOORWAY_APPROACH_SIDE_MARGIN_CELLS = 1


class Run15Explorer(Run14Explorer):
    """Story-sensitive doors and frontier-first navigation for scrolling rooms.

    Run fourteen reached the required Noelle conversation and later entered the
    Dark World, but the new runs exposed three lifecycle problems:

    * the classroom door was rejected while it was story-locked and stayed
      rejected after the observed conversation changed the story epoch;
    * screenshot animation bonuses were counted again whenever generic metadata
      rewrote the human-readable evidence string; and
    * a local floor fragment touching the bottom of a very wide scrolling room
      activated room-exit search while reachable map frontiers still existed.

    This layer keeps those rules evidence-driven. A structured doorway is retried
    only after an observed story-progress epoch, animation bonuses use a durable
    flag, and long rooms exhaust reachable frontiers before boundary speculation.
    """

    def __init__(self, seed: int = 0, memory_path: Path | None = None):
        super().__init__(seed, memory_path)
        self.room_camera_dimensions: dict[str, tuple[float, float]] = {}
        self.story_unlocked_doorway_retries = 0
        self.overselected_visual_leads_retired = 0
        self.scrolling_floor_leads_retired = 0
        self.long_room_frontier_deferrals = 0
        self.animation_bonus_duplicates_prevented = 0
        self._planning_cell: tuple[int, int] | None = None
        self._repair_loaded_visual_lifecycles()

    @staticmethod
    def _screen_region_map_update(
        key: tuple[str, int, int],
        record: dict[str, object],
    ) -> dict[str, object]:
        update = Run14Explorer._screen_region_map_update(key, record)
        for field in (
            "animated_bonus_applied",
            "doorway_failed_story_epoch",
            "doorway_story_retry_epoch",
            "story_sensitive_doorway",
        ):
            if record.get(field) is not None:
                update[field] = record[field]
        return update

    def _retire_visual_lead(
        self,
        key: tuple[str, int, int],
        record: dict[str, object],
        reason: str,
    ) -> None:
        already_retired = (
            record.get("hypothesis") is None
            and str(record.get("guess_state") or "") == "retired"
            and str(record.get("retired_reason") or "") == reason
        )
        record["hypothesis"] = None
        record["guess_state"] = "retired"
        record["guess_confidence"] = 0.05
        record["retired_reason"] = reason
        record["last_failure_reason"] = reason
        record["completed_tests"] = max(
            2,
            int(record.get("completed_tests", record.get("inspections", 0)) or 0),
        )
        record["inspections"] = int(record["completed_tests"])
        self.visual_goal_cooldowns.pop(key, None)
        if self.visual_goal == key:
            self.visual_goal = None
            self.decision_visual_goal = None
        if not already_retired:
            self.map_updates.append(self._screen_region_map_update(key, record))

    def _repair_loaded_visual_lifecycles(self) -> None:
        for key, record in self.screen_regions.items():
            if record.get("animated_sprite_evidence"):
                record["animated_bonus_applied"] = True

            if self._is_doorway_facade(record):
                record["story_sensitive_doorway"] = True
                if "doorway_failed_story_epoch" not in record and str(
                    record.get("guess_state") or ""
                ) in {"rejected", "retired"}:
                    progress_epoch = max(
                        (
                            int(candidate.get("last_story_epoch", 0) or 0)
                            for interaction_key, candidate in self.interactables.items()
                            if interaction_key[0] == key[0]
                            and str(candidate.get("usefulness") or "") == "progress"
                        ),
                        default=0,
                    )
                    record["doorway_failed_story_epoch"] = (
                        max(0, progress_epoch - 1)
                        if progress_epoch > 0
                        else self.story_epoch
                    )
                continue

            summary = str(record.get("visual_summary") or "")
            width = float(record.get("edge_width_ratio", 0.0) or 0.0)
            if (
                summary.startswith(FLOOR_EVIDENCE_PREFIX)
                and not record.get("path_continuation")
                and width < MIN_LOCAL_FLOOR_EXIT_SPAN
                and str(record.get("guess_state") or "proposed")
                not in {"confirmed", "retired"}
            ):
                self._retire_visual_lead(
                    key,
                    record,
                    "tiny floor contact was insufficient evidence of a room transition",
                )
                self.scrolling_floor_leads_retired += 1
                continue

            if (
                int(record.get("approach_attempts", 0) or 0)
                >= MAX_UNTESTED_VISUAL_SELECTIONS
                and int(record.get("completed_tests", record.get("inspections", 0)) or 0)
                == 0
                and not record.get("path_continuation")
                and str(record.get("guess_state") or "proposed")
                not in {"confirmed", "retired"}
            ):
                self._retire_visual_lead(
                    key,
                    record,
                    "visual lead was reselected too many times without reaching a concrete test",
                )
                self.overselected_visual_leads_retired += 1

    def _observe_room(self, telemetry: TelemetrySample) -> None:
        room = self._room_key(telemetry)
        if (
            telemetry.camera_width is not None
            and telemetry.camera_height is not None
            and float(telemetry.camera_width) > 0
            and float(telemetry.camera_height) > 0
        ):
            self.room_camera_dimensions[room] = (
                float(telemetry.camera_width),
                float(telemetry.camera_height),
            )
        super()._observe_room(telemetry)
        self._revive_doorways_after_story_progress(room)

    def _room_is_long_scrolling(self, room: str) -> bool:
        room_dimensions = self.room_dimensions.get(room)
        camera_dimensions = self.room_camera_dimensions.get(room)
        if room_dimensions is None or camera_dimensions is None:
            return False
        room_width, room_height = room_dimensions
        camera_width, camera_height = camera_dimensions
        return (
            room_width > camera_width * LONG_SCROLLING_ROOM_RATIO
            or room_height > camera_height * LONG_SCROLLING_ROOM_RATIO
        )

    def _has_reachable_frontier(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> bool:
        if any(
            self._direction_is_unexplored(room, cell, direction)
            for direction in DIRECTION_VECTORS
        ):
            return True
        return self._route_to_nearest_frontier(room, cell) is not None

    def _progress_pressure(self, room: str, cell: tuple[int, int]) -> bool:
        if self._room_is_long_scrolling(room) and self._has_reachable_frontier(
            room,
            cell,
        ):
            self.long_room_frontier_deferrals += 1
            return False
        return super()._progress_pressure(room, cell)

    def _exit_priority_active(self, room: str) -> bool:
        cell = self._planning_cell
        if (
            cell is not None
            and self._room_is_long_scrolling(room)
            and self._has_reachable_frontier(room, cell)
        ):
            self.exit_priority_started_at.pop(room, None)
            self.long_room_frontier_deferrals += 1
            return False
        return super()._exit_priority_active(room)

    def _retire_overselected_visual_leads(self, room: str) -> None:
        for key, record in self.screen_regions.items():
            if key[0] != room or self._is_doorway_facade(record):
                continue
            if record.get("path_continuation"):
                continue
            if str(record.get("guess_state") or "proposed") in {
                "confirmed",
                "rejected",
                "retired",
            }:
                continue
            if (
                int(record.get("approach_attempts", 0) or 0)
                < MAX_UNTESTED_VISUAL_SELECTIONS
                or int(
                    record.get(
                        "completed_tests",
                        record.get("inspections", 0),
                    )
                    or 0
                )
                > 0
            ):
                continue
            self._retire_visual_lead(
                key,
                record,
                "visual lead was reselected too many times without reaching a concrete test",
            )
            self.overselected_visual_leads_retired += 1

    def _plan_exploration(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> tuple[str, int, str]:
        self._retire_overselected_visual_leads(room)
        self._planning_cell = cell
        try:
            return super()._plan_exploration(room, cell)
        finally:
            self._planning_cell = None

    def _finish_visual_goal(
        self,
        outcome: str = "tested",
        reason: str | None = None,
    ) -> None:
        goal = self.visual_goal
        if goal is not None:
            record = self.screen_regions.get(goal)
            if (
                record is not None
                and self._is_doorway_facade(record)
                and outcome in {"route_failed", "abandoned_loop", "no_response"}
            ):
                record["story_sensitive_doorway"] = True
                record["doorway_failed_story_epoch"] = self.story_epoch
        super()._finish_visual_goal(outcome, reason)

    def _revive_doorways_after_story_progress(self, room: str) -> None:
        for key, record in self.screen_regions.items():
            if key[0] != room or not self._is_doorway_facade(record):
                continue
            state = str(record.get("guess_state") or "proposed")
            if state not in {"rejected", "retired"}:
                continue
            failed_epoch = int(
                record.get("doorway_failed_story_epoch", self.story_epoch) or 0
            )
            retry_epoch = int(record.get("doorway_story_retry_epoch", -1) or -1)
            if self.story_epoch <= failed_epoch or retry_epoch >= self.story_epoch:
                continue
            record["hypothesis"] = "possible_exit"
            record["guess_state"] = "proposed"
            record["failed_approaches"] = 0
            record["completed_tests"] = 0
            record["inspections"] = 0
            record["approach_attempts"] = 0
            record["doorway_probe_attempts"] = 0
            record["doorway_story_retry_epoch"] = self.story_epoch
            record["last_failure_reason"] = (
                "observed story progress invalidated the earlier locked-door result"
            )
            record.pop("retired_reason", None)
            self.visual_goal_cooldowns.pop(key, None)
            if self.visual_goal is not None and self.visual_goal != key:
                self.visual_goal = None
                self.decision_visual_goal = None
            self.story_unlocked_doorway_retries += 1
            self.map_updates.append(self._screen_region_map_update(key, record))

    def _doorway_approach_cells(
        self,
        room: str,
        record: dict[str, object],
    ) -> set[tuple[int, int]]:
        value = record.get("doorway_box_world") or record.get("feature_box_world")
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            return set()
        try:
            left, top, right, bottom = (float(component) for component in value)
        except (TypeError, ValueError):
            return set()
        edge = str(record.get("edge_hint") or "")
        known_cells = {
            (x, y)
            for seen_room, x, y in self.seen_cells
            if seen_room == room
        }
        if not known_cells:
            return set()
        margin = DOORWAY_APPROACH_SIDE_MARGIN_CELLS * CELL_SIZE
        depth = DOORWAY_APPROACH_DEPTH_CELLS * CELL_SIZE

        def eligible(cell: tuple[int, int]) -> bool:
            center_x = (cell[0] + 0.5) * CELL_SIZE
            center_y = (cell[1] + 0.5) * CELL_SIZE
            if edge == "top":
                return (
                    left - margin <= center_x <= right + margin
                    and bottom <= center_y <= bottom + depth
                )
            if edge == "bottom":
                return (
                    left - margin <= center_x <= right + margin
                    and top - depth <= center_y <= top
                )
            if edge == "left":
                return (
                    right <= center_x <= right + depth
                    and top - margin <= center_y <= bottom + margin
                )
            if edge == "right":
                return (
                    left - depth <= center_x <= left
                    and top - margin <= center_y <= bottom + margin
                )
            return False

        return {cell for cell in known_cells if eligible(cell)}

    def _route_to_doorway_facade(
        self,
        room: str,
        cell: tuple[int, int],
        goal: tuple[str, int, int],
        record: dict[str, object],
    ) -> tuple[str, str, tuple[int, int]] | None:
        direction = DOORWAY_DIRECTIONS.get(str(record.get("edge_hint") or ""))
        if direction is None:
            return None
        approaches = self._doorway_approach_cells(room, record)
        if cell in approaches:
            attempts = int(record.get("doorway_probe_attempts", 0) or 0) + 1
            record["doorway_probe_attempts"] = attempts
            self.doorway_probe_steps += 1
            if attempts > MAX_DOORWAY_PROBE_STEPS:
                self._finish_visual_goal(
                    "route_failed",
                    "structured doorway did not transition after a bounded directional probe",
                )
                return None
            self.decision_visual_goal = goal
            return direction, "possible_exit", (goal[1], goal[2])

        adjacency = self._adjacency(room)
        routes = [
            route
            for approach in sorted(approaches)
            if (route := self._route_to_target(adjacency, cell, approach)) is not None
        ]
        if not routes:
            return None
        first_direction, _distance = min(routes, key=lambda route: (route[1], route[0]))
        self.decision_visual_goal = goal
        return first_direction, "possible_exit", (goal[1], goal[2])

    def _direction_to_visual_hypothesis(
        self,
        room: str,
        cell: tuple[int, int],
        *,
        story_focus: bool = False,
        allowed_hypotheses: set[str] | None = None,
    ) -> tuple[str, str, tuple[int, int]] | None:
        goal = self.visual_goal
        record = self.screen_regions.get(goal) if goal is not None else None
        allowed = allowed_hypotheses or {
            "possible_exit",
            "possible_character",
            "possible_interactable",
        }
        if (
            goal is not None
            and record is not None
            and "possible_exit" in allowed
            and record.get("hypothesis") == "possible_exit"
            and self._is_doorway_facade(record)
        ):
            route = self._route_to_doorway_facade(room, cell, goal, record)
            if route is not None:
                return route
        return super()._direction_to_visual_hypothesis(
            room,
            cell,
            story_focus=story_focus,
            allowed_hypotheses=allowed_hypotheses,
        )

    def _apply_animated_character_bonus(self, room: str) -> None:
        for key, record in self.screen_regions.items():
            if key[0] != room or record.get("hypothesis") != "possible_character":
                continue
            summary = str(record.get("visual_summary") or "").casefold()
            if not (summary.startswith("compact") or summary.startswith("tall")):
                continue
            if int(record.get("entity_approach_directions", 0) or 0) < 2:
                continue
            if float(record.get("motion", 0.0) or 0.0) < SAME_VIEW_ANIMATION_CHANGES:
                continue
            if (
                float(record.get("colorfulness", 0.0) or 0.0)
                < ANIMATED_SPRITE_MIN_COLORFULNESS
            ):
                continue
            if record.get("animated_bonus_applied") or record.get(
                "animated_sprite_evidence"
            ):
                self.animation_bonus_duplicates_prevented += 1
                record["animated_bonus_applied"] = True
                continue
            confidence = float(record.get("guess_confidence", 0.0) or 0.0)
            record["guess_confidence"] = round(
                min(0.95, confidence + ANIMATED_CHARACTER_BONUS),
                3,
            )
            record["animated_sprite_evidence"] = True
            record["animated_bonus_applied"] = True
            evidence = str(record.get("evidence_summary") or "")
            record["evidence_summary"] = (
                f"{evidence}; {ANIMATED_EVIDENCE_NOTE}"
                if evidence
                else ANIMATED_EVIDENCE_NOTE
            )
            self.animated_character_bonuses += 1
            self.map_updates.append(self._screen_region_map_update(key, record))

    def summary(self) -> dict:
        summary = super().summary()
        summary["story_unlocked_doorway_retries"] = self.story_unlocked_doorway_retries
        summary["overselected_visual_leads_retired"] = (
            self.overselected_visual_leads_retired
        )
        summary["scrolling_floor_leads_retired"] = self.scrolling_floor_leads_retired
        summary["long_room_frontier_deferrals"] = self.long_room_frontier_deferrals
        summary["animation_bonus_duplicates_prevented"] = (
            self.animation_bonus_duplicates_prevented
        )
        return summary
