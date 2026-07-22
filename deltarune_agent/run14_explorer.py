from __future__ import annotations

from pathlib import Path

from .run13_explorer import Run13Explorer
from .run14_screen_regions import DOORWAY_FACADE_PREFIX


MAX_PATH_CONTINUATION_FAILURES = 2
MAX_PATH_CONTINUATION_ATTEMPTS = 12
MAX_PATH_CONTINUATION_REVIVALS = 1
MAX_DOORWAY_ROUTE_FAILURES = 3
MAX_DOORWAY_PROBE_STEPS = 8
DOORWAY_MIN_CONFIDENCE = 0.72
DOORWAY_DIRECTIONS = {
    "top": "up",
    "bottom": "down",
    "left": "left",
    "right": "right",
}


class Run14Explorer(Run13Explorer):
    """Screenshot-grounded doorway search with bounded geometry lead lifecycle.

    Run thirteen correctly found the bedroom's visible floor exit, but the latest
    classroom runs exposed a lifecycle bug in the older geometry planner. A
    rejected boundary lead was revived every time the same outline probe was
    rediscovered, resetting its failures and allowing one false passage to own
    more than a thousand decisions.

    This layer makes one geometry observation consumable only once, permanently
    retires repeatedly unreachable path continuations, and preserves structured
    doorway facades detected from screenshots even when their artwork is inset
    from the room's raw coordinate boundary.
    """

    def __init__(self, seed: int = 0, memory_path: Path | None = None):
        super().__init__(seed, memory_path)
        self.retired_runaway_path_continuations = 0
        self.blocked_path_continuation_revivals = 0
        self.doorway_facades_preserved = 0
        self.doorway_facade_priority_evaluations = 0
        self.doorway_probe_steps = 0
        self._retire_loaded_runaway_path_continuations()

    @staticmethod
    def _is_doorway_facade(record: dict[str, object]) -> bool:
        return bool(record.get("doorway_facade")) or str(
            record.get("visual_summary") or ""
        ).startswith(DOORWAY_FACADE_PREFIX)

    @staticmethod
    def _probe_token(probe: tuple[str, int, int, str]) -> list[object]:
        return [probe[1], probe[2], probe[3]]

    def _retire_geometry_guess(
        self,
        key: tuple[str, int, int],
        record: dict[str, object],
        reason: str,
    ) -> None:
        already_retired = (
            record.get("guess_state") == "retired"
            and not record.get("path_continuation")
            and record.get("path_continuation_locked")
        )
        record["hypothesis"] = None
        record["path_continuation"] = False
        record["path_continuation_locked"] = True
        record["guess_state"] = "retired"
        record["guess_confidence"] = 0.05
        record["completed_tests"] = max(
            2,
            int(record.get("completed_tests", record.get("inspections", 0)) or 0),
        )
        record["inspections"] = int(record["completed_tests"])
        record["retired_reason"] = reason
        record["last_failure_reason"] = reason
        self.visual_goal_cooldowns.pop(key, None)
        if self.visual_goal == key:
            self.visual_goal = None
            self.decision_visual_goal = None
        if not already_retired:
            self.retired_runaway_path_continuations += 1
            self.map_updates.append(self._screen_region_map_update(key, record))

    def _retire_loaded_runaway_path_continuations(self) -> None:
        for key, record in self.screen_regions.items():
            if not record.get("path_continuation") or self._is_doorway_facade(record):
                continue
            failures = int(record.get("failed_approaches", 0) or 0)
            attempts = int(record.get("approach_attempts", 0) or 0)
            state = str(record.get("guess_state") or "proposed")
            if (
                failures >= MAX_PATH_CONTINUATION_FAILURES
                or attempts >= MAX_PATH_CONTINUATION_ATTEMPTS
                or state == "rejected"
            ):
                self._retire_geometry_guess(
                    key,
                    record,
                    "same geometry passage was repeatedly unreachable and may not be revived",
                )

    @staticmethod
    def _screen_region_map_update(
        key: tuple[str, int, int],
        record: dict[str, object],
    ) -> dict[str, object]:
        update = Run13Explorer._screen_region_map_update(key, record)
        for field in (
            "doorway_facade",
            "doorway_box_world",
            "path_probe",
            "path_continuation_revivals",
            "path_continuation_locked",
            "doorway_probe_attempts",
        ):
            if record.get(field) is not None:
                update[field] = record[field]
        return update

    def _remember_path_continuation(
        self,
        probe: tuple[str, int, int, str],
    ) -> None:
        room, cell_x, cell_y, _direction = probe
        key = (room, *self._region((cell_x, cell_y)))
        record = self.screen_regions.get(key)
        token = self._probe_token(probe)
        if record is None:
            super()._remember_path_continuation(probe)
            created = self.screen_regions.get(key)
            if created is not None and created.get("path_continuation"):
                created["path_probe"] = token
                self.map_updates.append(self._screen_region_map_update(key, created))
            return
        if record.get("hypothesis") == "possible_character":
            super()._remember_path_continuation(probe)
            return

        same_probe = record.get("path_probe") == token
        failures = int(record.get("failed_approaches", 0) or 0)
        attempts = int(record.get("approach_attempts", 0) or 0)
        state = str(record.get("guess_state") or "proposed")
        doorway = self._is_doorway_facade(record)
        failure_limit = (
            MAX_DOORWAY_ROUTE_FAILURES
            if doorway
            else MAX_PATH_CONTINUATION_FAILURES
        )

        if (
            record.get("path_continuation_locked")
            or failures >= failure_limit
            or (not doorway and attempts >= MAX_PATH_CONTINUATION_ATTEMPTS)
        ):
            self.blocked_path_continuation_revivals += 1
            if not doorway:
                self._retire_geometry_guess(
                    key,
                    record,
                    "bounded path-continuation failure limit was reached",
                )
            return

        if same_probe and state in {"rejected", "retired"}:
            self.blocked_path_continuation_revivals += 1
            if not doorway:
                self._retire_geometry_guess(
                    key,
                    record,
                    "the same rejected outline probe cannot reset its own failures",
                )
            return

        if state in {"rejected", "retired"} and not doorway:
            revivals = int(record.get("path_continuation_revivals", 0) or 0)
            if revivals >= MAX_PATH_CONTINUATION_REVIVALS:
                self.blocked_path_continuation_revivals += 1
                self._retire_geometry_guess(
                    key,
                    record,
                    "no distinct new geometry remained after the one allowed revival",
                )
                return
            record["path_continuation_revivals"] = revivals + 1

        if same_probe and record.get("path_continuation"):
            return
        record["path_probe"] = token
        super()._remember_path_continuation(probe)

    def _finish_visual_goal(
        self,
        outcome: str = "tested",
        reason: str | None = None,
    ) -> None:
        goal = self.visual_goal
        super()._finish_visual_goal(outcome, reason)
        if goal is None:
            return
        record = self.screen_regions.get(goal)
        if record is None or not record.get("path_continuation"):
            return
        failures = int(record.get("failed_approaches", 0) or 0)
        limit = (
            MAX_DOORWAY_ROUTE_FAILURES
            if self._is_doorway_facade(record)
            else MAX_PATH_CONTINUATION_FAILURES
        )
        if failures >= limit and not self._is_doorway_facade(record):
            self._retire_geometry_guess(
                goal,
                record,
                reason
                or "geometry-backed visual route exceeded its bounded failure limit",
            )

    def _retire_unsupported_visual_exits(self, room: str) -> None:
        protected: list[tuple[tuple[str, int, int], dict[str, object], object]] = []
        for key, record in self.screen_regions.items():
            if (
                key[0] == room
                and record.get("hypothesis") == "possible_exit"
                and self._is_doorway_facade(record)
            ):
                protected.append((key, record, record.get("hypothesis")))
                # Run-nine's inset-opening retirement is intentionally bypassed
                # only for a screenshot-structured facade. Route failures remain
                # bounded separately and can still retire it.
                record["hypothesis"] = None

        super()._retire_unsupported_visual_exits(room)

        for key, record, hypothesis in protected:
            record["hypothesis"] = hypothesis or "possible_exit"
            changed = not bool(record.get("doorway_facade"))
            record["doorway_facade"] = True
            record["doorway_box_world"] = record.get(
                "feature_box_world",
                record.get("passage_box_world"),
            )
            record["path_continuation"] = False
            failures = int(record.get("failed_approaches", 0) or 0)
            if failures < MAX_DOORWAY_ROUTE_FAILURES and str(
                record.get("guess_state") or "proposed"
            ) in {"retired", "rejected"}:
                record["guess_state"] = "proposed"
                record.pop("retired_reason", None)
                changed = True
            previous_confidence = float(record.get("guess_confidence", 0.0) or 0.0)
            if previous_confidence < DOORWAY_MIN_CONFIDENCE:
                record["guess_confidence"] = DOORWAY_MIN_CONFIDENCE
                changed = True
            record["guess_label"] = "Possible framed doorway"
            record["evidence_kind"] = "rectangular_doorway_facade"
            record["evidence_summary"] = (
                "paired vertical frame edges and top/bottom rails form a doorway-sized "
                "facade near the upper wall"
            )
            if changed:
                self.doorway_facades_preserved += 1
                self.map_updates.append(self._screen_region_map_update(key, record))

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
            anchor = record.get("anchor_cell")
            if isinstance(anchor, (list, tuple)) and len(anchor) == 2:
                try:
                    anchor_cell = (int(anchor[0]), int(anchor[1]))
                except (TypeError, ValueError):
                    anchor_cell = None
                if anchor_cell is not None:
                    distance = abs(anchor_cell[0] - cell[0]) + abs(
                        anchor_cell[1] - cell[1]
                    )
                    if distance <= 1:
                        direction = DOORWAY_DIRECTIONS.get(
                            str(record.get("edge_hint") or "")
                        )
                        if direction is not None:
                            attempts = int(
                                record.get("doorway_probe_attempts", 0) or 0
                            ) + 1
                            record["doorway_probe_attempts"] = attempts
                            self.doorway_probe_steps += 1
                            if attempts > MAX_DOORWAY_PROBE_STEPS:
                                self._finish_visual_goal(
                                    "route_failed",
                                    "framed doorway did not transition after a bounded directional probe",
                                )
                                return None
                            self.decision_visual_goal = goal
                            return direction, "possible_exit", (goal[1], goal[2])
        return super()._direction_to_visual_hypothesis(
            room,
            cell,
            story_focus=story_focus,
            allowed_hypotheses=allowed_hypotheses,
        )

    def _visual_hypothesis_priority(
        self,
        record: dict[str, object],
        key: tuple[str, int, int],
        current_region: tuple[int, int],
        story_focus: bool,
    ) -> tuple[object, ...]:
        base = super()._visual_hypothesis_priority(
            record,
            key,
            current_region,
            story_focus,
        )
        doorway_rank = 0 if self._is_doorway_facade(record) else 1
        if doorway_rank == 0:
            self.doorway_facade_priority_evaluations += 1
        return (doorway_rank, *base)

    def summary(self) -> dict:
        summary = super().summary()
        summary["retired_runaway_path_continuations"] = (
            self.retired_runaway_path_continuations
        )
        summary["blocked_path_continuation_revivals"] = (
            self.blocked_path_continuation_revivals
        )
        summary["doorway_facades_preserved"] = self.doorway_facades_preserved
        summary["doorway_facade_priority_evaluations"] = (
            self.doorway_facade_priority_evaluations
        )
        summary["doorway_probe_steps"] = self.doorway_probe_steps
        return summary
