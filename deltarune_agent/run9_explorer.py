from __future__ import annotations

from pathlib import Path

from .observer import Observation
from .policy import (
    DIRECTION_VECTORS,
    MOVEMENT_COMMIT_STEPS,
    OPPOSITE,
    SCREEN_ANALYSIS_INTERVAL,
)
from .run7_explorer import Run7Explorer
from .run8_explorer import (
    ANIMATED_CHARACTER_BONUS,
    ANIMATED_SPRITE_MIN_COLORFULNESS,
    DARK_VOID_RATIO,
    Run8Explorer,
)
from .telemetry import TelemetrySample


PINCH_RECENT_CELLS = 8
PINCH_MIN_ALTERNATIONS = 6
PINCH_RECOVERY_TICKS = 14
PINCH_CLEAR_RADIUS = 3
EMBEDDED_OPENING_MAX_CONFIDENCE = 0.44
EMBEDDED_OPENING_MIN_VIEWS = 3
SAME_VIEW_ANIMATION_CHANGES = 2
ANIMATED_EVIDENCE_NOTE = (
    "compact feature changed repeatedly from the same camera viewpoint"
)


class Run9Explorer(Run8Explorer):
    """Recovery and visual-grounding fixes learned from seven focused runs.

    Classroom telemetry showed a deterministic two-cell loop between a wall and
    the teacher's desk. Both outward vertical edges were correctly learned as
    blocked, but early collision recovery kept choosing the other vertical cell
    before the general oscillation planner ran. This layer detects that topology
    directly and commits to a perpendicular escape.

    Screenshot comparison also showed that raw appearance changes mostly came
    from camera scrolling. Animation evidence now requires repeated changes from
    the same exact camera viewpoint and can add its confidence bonus only once.
    """

    def __init__(self, seed: int = 0, memory_path: Path | None = None):
        super().__init__(seed, memory_path)
        self.pinch_recoveries = 0
        self.pinch_escape_moves = 0
        self.pinch_recovery_failures = 0
        self.pinch_recovery_successes = 0
        self._active_pinch_room: str | None = None
        self._active_pinch_cells: frozenset[tuple[int, int]] = frozenset()
        self._active_pinch_direction: str | None = None
        self._active_pinch_until = 0
        self._viewpoint_signatures: dict[
            tuple[str, int, int, int, int], str
        ] = {}
        self.embedded_openings_retained = 0
        self.same_view_motion_updates = 0

    @staticmethod
    def _screen_region_map_update(
        key: tuple[str, int, int],
        record: dict[str, object],
    ) -> dict[str, object]:
        update = Run8Explorer._screen_region_map_update(key, record)
        for field in (
            "motion",
            "embedded_opening",
            "animated_sprite_evidence",
        ):
            if record.get(field) is not None:
                update[field] = record[field]
        return update

    def _clear_active_pinch(self, *, successful: bool = False) -> None:
        if successful and self._active_pinch_room is not None:
            self.pinch_recovery_successes += 1
        self._active_pinch_room = None
        self._active_pinch_cells = frozenset()
        self._active_pinch_direction = None
        self._active_pinch_until = 0

    def _observe_room(self, telemetry: TelemetrySample) -> None:
        previous_room = self.observed_room
        super()._observe_room(telemetry)
        room = self._room_key(telemetry)
        if previous_room is not None and previous_room != room:
            self._clear_active_pinch()

    def _pinch_pattern(
        self,
        room: str,
    ) -> tuple[frozenset[tuple[int, int]], set[str], tuple[str, ...]] | None:
        recent = list(self.recent_cells)[-PINCH_RECENT_CELLS:]
        if len(recent) < PINCH_MIN_ALTERNATIONS:
            return None
        if any(recent_room != room for recent_room, _x, _y in recent):
            return None
        cells = [(x, y) for _recent_room, x, y in recent]
        unique = set(cells)
        if len(unique) != 2:
            return None
        first, second = sorted(unique)
        if abs(first[0] - second[0]) + abs(first[1] - second[1]) != 1:
            return None
        if any(left == right for left, right in zip(cells, cells[1:])):
            return None

        if first[0] == second[0]:
            top, bottom = sorted(unique, key=lambda cell: cell[1])
            if not (
                self._blocked_near(room, top, "up")
                and self._blocked_near(room, bottom, "down")
            ):
                return None
            return frozenset(unique), {"up", "down"}, ("left", "right")

        left_cell, right_cell = sorted(unique, key=lambda cell: cell[0])
        if not (
            self._blocked_near(room, left_cell, "left")
            and self._blocked_near(room, right_cell, "right")
        ):
            return None
        return frozenset(unique), {"left", "right"}, ("up", "down")

    def _activate_pinch_escape(
        self,
        room: str,
        cell: tuple[int, int],
        *,
        avoid: set[str] | None = None,
    ) -> str | None:
        pattern = self._pinch_pattern(room)
        if pattern is None:
            return None
        cells, _trapped_axis, perpendicular = pattern
        avoid = set(avoid or ())

        previous_direction = (
            self._active_pinch_direction
            if self._active_pinch_room == room
            and self._active_pinch_cells == cells
            else None
        )
        candidates = [
            direction
            for direction in perpendicular
            if direction not in avoid
            and not self._blocked_near(room, cell, direction)
            and not self._is_entry_warp_direction(room, cell, direction)
        ]
        if not candidates:
            if previous_direction is not None:
                self.pinch_recovery_failures += 1
            self._clear_active_pinch()
            return None

        if previous_direction is not None and previous_direction not in candidates:
            self.pinch_recovery_failures += 1

        def score(direction: str) -> tuple[int, int, int, str]:
            neighbor = self._known_open_neighbor(room, cell, direction)
            dx, dy = DIRECTION_VECTORS[direction]
            target = neighbor or (cell[0] + dx, cell[1] + dy)
            knowledge = (
                0
                if self._direction_is_unexplored(room, cell, direction)
                else 1 if neighbor is not None else 2
            )
            return (
                knowledge,
                self.visits[(room, *target)],
                self._recent_cell_cost(room, target),
                direction,
            )

        direction = min(candidates, key=score)
        new_episode = not (
            self._active_pinch_room == room
            and self._active_pinch_cells == cells
            and self.navigation_tick < self._active_pinch_until
        )
        if new_episode:
            self.pinch_recoveries += 1
            self.exit_search_goal = None
            if self.visual_goal is not None:
                self._finish_visual_goal(
                    "abandoned_loop",
                    "visual route entered a two-cell wall-and-object pinch",
                )
        self._active_pinch_room = room
        self._active_pinch_cells = cells
        self._active_pinch_direction = direction
        self._active_pinch_until = self.navigation_tick + PINCH_RECOVERY_TICKS
        self.pinch_escape_moves += 1
        return direction

    def _least_visited_direction(
        self,
        room: str,
        cell: tuple[int, int],
        previous: str,
        avoid: set[str] | None = None,
    ) -> str:
        escape = self._activate_pinch_escape(
            room,
            cell,
            avoid=set(avoid or ()),
        )
        if escape is not None:
            return escape
        return super()._least_visited_direction(
            room,
            cell,
            previous,
            avoid=avoid,
        )

    def _break_oscillation(
        self,
        room: str,
        cell: tuple[int, int],
        proposed: str,
    ) -> tuple[str, bool]:
        pattern = self._pinch_pattern(room)
        if pattern is not None and proposed in pattern[1]:
            escape = self._activate_pinch_escape(room, cell)
            if escape is not None:
                self.loop_reason = (
                    "detected two-cell pinch between opposing blocked edges"
                )
                self.oscillation_breaks += 1
                return escape, True
        return super()._break_oscillation(room, cell, proposed)

    def _plan_exploration(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> tuple[str, int, str]:
        if (
            self._active_pinch_room == room
            and self._active_pinch_direction in DIRECTION_VECTORS
        ):
            distance = min(
                (
                    max(abs(cell[0] - old[0]), abs(cell[1] - old[1]))
                    for old in self._active_pinch_cells
                ),
                default=99,
            )
            if distance > PINCH_CLEAR_RADIUS:
                self._clear_active_pinch(successful=True)
            elif self.navigation_tick >= self._active_pinch_until:
                self._clear_active_pinch()
            elif self._blocked_near(
                room,
                cell,
                str(self._active_pinch_direction),
            ):
                self.pinch_recovery_failures += 1
                self._active_pinch_direction = None
            else:
                self.pinch_escape_moves += 1
                return (
                    str(self._active_pinch_direction),
                    MOVEMENT_COMMIT_STEPS,
                    "continue perpendicular escape from two-cell pinch",
                )
        return super()._plan_exploration(room, cell)

    def _update_same_view_motion(
        self,
        observation: Observation,
        telemetry: TelemetrySample,
    ) -> None:
        if (
            not observation.visual_valid
            or observation.step % SCREEN_ANALYSIS_INTERVAL
        ):
            return
        room = self._room_key(telemetry)
        camera_x = int(round(float(telemetry.camera_x or 0.0)))
        camera_y = int(round(float(telemetry.camera_y or 0.0)))
        for key, record in self.screen_regions.items():
            if key[0] != room or int(record.get("last_seen_step", -1)) != observation.step:
                continue
            signature = str(record.get("last_signature") or "")
            if not signature:
                continue
            viewpoint_key = (room, key[1], key[2], camera_x, camera_y)
            previous = self._viewpoint_signatures.get(viewpoint_key)
            if previous is not None and previous != signature:
                record["motion"] = float(record.get("motion", 0.0) or 0.0) + 1.0
                self.same_view_motion_updates += 1
            self._viewpoint_signatures[viewpoint_key] = signature

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
            evidence = str(record.get("evidence_summary") or "")
            if ANIMATED_EVIDENCE_NOTE in evidence:
                continue
            confidence = float(record.get("guess_confidence", 0.0) or 0.0)
            record["guess_confidence"] = round(
                min(0.95, confidence + ANIMATED_CHARACTER_BONUS),
                3,
            )
            record["animated_sprite_evidence"] = True
            record["evidence_summary"] = (
                f"{evidence}; {ANIMATED_EVIDENCE_NOTE}"
                if evidence
                else ANIMATED_EVIDENCE_NOTE
            )
            self.animated_character_bonuses += 1
            self.map_updates.append(self._screen_region_map_update(key, record))

    def _retire_unsupported_visual_exits(self, room: str) -> None:
        for key, record in self.screen_regions.items():
            if key[0] != room or record.get("hypothesis") != "possible_exit":
                continue
            if self._known_warp_in_region(key) or record.get("confirmed_target_room"):
                record.pop("retired_reason", None)
                if str(record.get("guess_state") or "") in {"retired", "rejected"}:
                    record["guess_state"] = "proposed"
                continue
            if record.get("path_continuation"):
                record.pop("retired_reason", None)
                if str(record.get("guess_state") or "") in {"retired", "rejected"}:
                    record["guess_state"] = "proposed"
                continue
            if str(record.get("guess_state") or "") in {
                "confirmed",
                "rejected",
                "retired",
            }:
                continue

            contact = self._passage_contacts_true_room_edge(room, record)
            dark_ratio = float(record.get("dark_ratio", 0.0) or 0.0)
            walkable = bool(record.get("walkable_evidence", False))
            failed = int(record.get("failed_approaches", 0) or 0)
            views = int(record.get("independent_views", record.get("views", 0)) or 0)

            if (
                contact is True
                and dark_ratio >= DARK_VOID_RATIO
                and not walkable
            ):
                self._retire_visual_exit(
                    key,
                    record,
                    "edge feature was mostly dark void with no mapped walkable approach",
                )
                self.retired_dark_void_guesses += 1
                continue

            if contact is False:
                if walkable and failed < 2:
                    first_embedded = not bool(record.get("embedded_opening"))
                    record["embedded_opening"] = True
                    record["guess_confidence"] = round(
                        min(
                            float(record.get("guess_confidence", 0.0) or 0.0),
                            EMBEDDED_OPENING_MAX_CONFIDENCE,
                        ),
                        3,
                    )
                    evidence = str(record.get("evidence_summary") or "")
                    note = (
                        "opening is inset from the coordinate boundary; keep only "
                        "as a low-confidence wall-embedded doorway"
                    )
                    if note not in evidence:
                        record["evidence_summary"] = (
                            f"{evidence}; {note}" if evidence else note
                        )
                    if first_embedded:
                        self.embedded_openings_retained += 1
                        self.map_updates.append(
                            self._screen_region_map_update(key, record)
                        )
                    continue
                if failed >= 2 or views >= EMBEDDED_OPENING_MIN_VIEWS:
                    self._retire_visual_exit(
                        key,
                        record,
                        "inset dark landmark lacked a walkable approach or failed repeated routes",
                    )
                    self.retired_inset_exit_guesses += 1

    def _remember_path_continuation(self, probe) -> None:
        super()._remember_path_continuation(probe)
        room, cell_x, cell_y, _direction = probe
        key = (room, *self._region((cell_x, cell_y)))
        record = self.screen_regions.get(key)
        if record is not None and record.get("path_continuation"):
            record.pop("retired_reason", None)

    def _observe_screen(
        self,
        observation: Observation,
        telemetry: TelemetrySample,
    ) -> None:
        # Call the run-seven implementation directly so run-eight's raw
        # camera-scroll appearance bonus cannot run before same-view evidence is
        # calculated here.
        Run7Explorer._observe_screen(self, observation, telemetry)
        self._update_same_view_motion(observation, telemetry)
        room = self._room_key(telemetry)
        self._retire_unsupported_visual_exits(room)
        self._apply_animated_character_bonus(room)

    def summary(self) -> dict:
        summary = super().summary()
        summary["pinch_recoveries"] = self.pinch_recoveries
        summary["pinch_escape_moves"] = self.pinch_escape_moves
        summary["pinch_recovery_failures"] = self.pinch_recovery_failures
        summary["pinch_recovery_successes"] = self.pinch_recovery_successes
        summary["same_view_motion_updates"] = self.same_view_motion_updates
        summary["embedded_openings_retained"] = self.embedded_openings_retained
        return summary
