from __future__ import annotations

from pathlib import Path

from .policy import DIRECTION_VECTORS
from .run15_explorer import Run15Explorer
from .run16_semantics import (
    CARDINAL_DIRECTIONS,
    repair_portal_action_conflicts,
)
from .telemetry import TelemetrySample
from .world_model import CELL_SIZE, EXPLORATION_REGION_CELLS


DIALOGUE_ECOLOGY_MIN_INTERACTIONS = 2
MOTION_SPRITE_MIN_MOTION = 3.0
MOTION_SPRITE_MIN_INTEREST = 0.25
MOTION_SPRITE_MIN_COLORFULNESS = 0.12
MOTION_SPRITE_MAX_DARK_RATIO = 0.78
MOTION_SPRITE_MAX_FEATURE_SIZE = 26.0
MOTION_SPRITE_CONFIDENCE = 0.48
MAX_MOTION_SPRITE_CANDIDATES = 6
MOTION_SPRITE_APPROACH_RADIUS = 5
LOCKED_RETIREMENT_REASONS = {
    "visual lead was reselected too many times without reaching a concrete test",
    "tiny floor contact was insufficient evidence of a room transition",
    "same geometry passage was repeatedly unreachable and may not be revived",
    "bounded path-continuation failure limit was reached",
    "the same rejected outline probe cannot reset its own failures",
}


class Run16Explorer(Run15Explorer):
    """Persistent evidence, source-informed sprite guesses, and cleaner portals.

    Chapter-one ``data.win`` strings distinguish dedicated scene controllers and
    generic room-goto events from cardinal door objects. The language data also
    confirms that some writer dialogue contains choices before a long scripted
    sequence. Those sources are used only to improve generic classification:
    automatic transitions are not routed as doors, and a room that has already
    demonstrated several dialogue interactions may promote remaining compact,
    repeatedly animated sprite regions for a bounded interaction test.

    No room name, NPC location, response text, or required route is hardcoded.
    """

    def __init__(self, seed: int = 0, memory_path: Path | None = None):
        super().__init__(seed, memory_path)
        self.motion_sprite_candidates_promoted = 0
        self.motion_sprite_interaction_attempts = 0
        self.motion_sprite_failures = 0
        self.portal_action_conflicts_repaired = 0
        self.automatic_warps_deprioritized = 0
        self.locked_visual_leads_restored = 0
        self._motion_sprite_pending_goal: tuple[str, int, int] | None = None
        persisted_dimensions = getattr(self.world, "room_dimensions", {})
        if isinstance(persisted_dimensions, dict):
            self.room_dimensions.update(persisted_dimensions)
        self._restore_lifecycle_locks()
        self.portal_action_conflicts_repaired += repair_portal_action_conflicts(
            self.world
        )

    @staticmethod
    def _screen_region_map_update(
        key: tuple[str, int, int],
        record: dict[str, object],
    ) -> dict[str, object]:
        update = Run15Explorer._screen_region_map_update(key, record)
        for field in (
            "motion_sprite_candidate",
            "motion_sprite_tested",
            "lifecycle_locked",
            "source_evidence_kind",
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
        record["lifecycle_locked"] = True
        super()._retire_visual_lead(key, record, reason)

    def _restore_lifecycle_locks(self) -> None:
        for key, record in self.screen_regions.items():
            reason = str(record.get("retired_reason") or "")
            if record.get("lifecycle_locked") or reason in LOCKED_RETIREMENT_REASONS:
                record["lifecycle_locked"] = True
                record["hypothesis"] = None
                record["guess_state"] = "retired"
                self.visual_goal_cooldowns.pop(key, None)
                self.locked_visual_leads_restored += 1

    def _observe_room(self, telemetry: TelemetrySample) -> None:
        room = self._room_key(telemetry)
        if (
            telemetry.room_width is not None
            and telemetry.room_height is not None
            and float(telemetry.room_width) > 0
            and float(telemetry.room_height) > 0
        ):
            dimensions = (
                float(telemetry.room_width),
                float(telemetry.room_height),
            )
            self.room_dimensions[room] = dimensions
            if not hasattr(self.world, "room_dimensions"):
                self.world.room_dimensions = {}
            self.world.room_dimensions[room] = dimensions
        super()._observe_room(telemetry)
        self.portal_action_conflicts_repaired += repair_portal_action_conflicts(
            self.world
        )

    def _warp_is_priority_candidate(self, warp) -> bool:
        metadata = self.world.portal_metadata(warp)
        action = str(warp[3] if len(warp) > 3 else "")
        role = str(metadata.get("role") or "") if metadata else ""
        if action not in CARDINAL_DIRECTIONS or role == "automatic_sequence":
            self.automatic_warps_deprioritized += 1
            return False
        return super()._warp_is_priority_candidate(warp)

    def _observe_screen(self, observation, telemetry: TelemetrySample) -> None:
        super()._observe_screen(observation, telemetry)
        room = self._room_key(telemetry)
        self._enforce_lifecycle_locks(room)
        self._promote_motion_sprite_candidates(room)

    def _enforce_lifecycle_locks(self, room: str) -> None:
        for key, record in self.screen_regions.items():
            if key[0] != room or not record.get("lifecycle_locked"):
                continue
            if self._is_doorway_facade(record) and record.get(
                "doorway_story_retry_epoch"
            ) == self.story_epoch:
                continue
            changed = (
                record.get("hypothesis") is not None
                or str(record.get("guess_state") or "") != "retired"
            )
            record["hypothesis"] = None
            record["guess_state"] = "retired"
            self.visual_goal_cooldowns.pop(key, None)
            if self.visual_goal == key:
                self.visual_goal = None
                self.decision_visual_goal = None
            if changed:
                self.map_updates.append(self._screen_region_map_update(key, record))

    def _dialogue_interaction_count(self, room: str) -> int:
        return sum(
            key[0] == room
            and str(record.get("classification") or "")
            in {"tested_nonchoice", "confirmed_npc", "story_interaction"}
            for key, record in self.interactables.items()
        )

    @staticmethod
    def _feature_dimensions(record: dict[str, object]) -> tuple[float, float] | None:
        value = record.get("feature_box_world") or record.get("visual_box_world")
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            return None
        try:
            left, top, right, bottom = (float(component) for component in value)
        except (TypeError, ValueError):
            return None
        return max(0.0, right - left), max(0.0, bottom - top)

    def _region_has_learned_interaction(
        self,
        room: str,
        region: tuple[int, int],
    ) -> bool:
        return any(
            key[0] == room and self._region((key[1], key[2])) == region
            for key in self.interactables
        )

    def _promote_motion_sprite_candidates(self, room: str) -> None:
        if self._dialogue_interaction_count(room) < DIALOGUE_ECOLOGY_MIN_INTERACTIONS:
            return
        existing = sum(
            1
            for key, record in self.screen_regions.items()
            if key[0] == room
            and bool(record.get("motion_sprite_candidate"))
            and str(record.get("guess_state") or "proposed")
            not in {"confirmed", "rejected", "retired"}
        )
        if existing >= MAX_MOTION_SPRITE_CANDIDATES:
            return

        candidates = []
        for key, record in self.screen_regions.items():
            if key[0] != room:
                continue
            if record.get("hypothesis") is not None:
                continue
            if record.get("lifecycle_locked"):
                continue
            if str(record.get("guess_state") or "proposed") in {
                "confirmed",
                "rejected",
                "retired",
            }:
                continue
            region = (key[1], key[2])
            if self._region_has_learned_interaction(room, region):
                continue
            dimensions = self._feature_dimensions(record)
            if dimensions is None:
                continue
            width, height = dimensions
            if max(width, height) > MOTION_SPRITE_MAX_FEATURE_SIZE:
                continue
            motion = float(record.get("motion", 0.0) or 0.0)
            interest = float(record.get("interest", 0.0) or 0.0)
            colorfulness = float(record.get("colorfulness", 0.0) or 0.0)
            dark_ratio = float(record.get("dark_ratio", 0.0) or 0.0)
            if (
                motion < MOTION_SPRITE_MIN_MOTION
                or interest < MOTION_SPRITE_MIN_INTEREST
                or colorfulness < MOTION_SPRITE_MIN_COLORFULNESS
                or dark_ratio > MOTION_SPRITE_MAX_DARK_RATIO
            ):
                continue
            candidates.append(
                (
                    (
                        -motion,
                        -colorfulness,
                        -interest,
                        key[2],
                        key[1],
                    ),
                    key,
                    record,
                )
            )

        for _score, key, record in sorted(candidates)[: max(
            0,
            MAX_MOTION_SPRITE_CANDIDATES - existing,
        )]:
            record["hypothesis"] = "possible_character"
            record["motion_sprite_candidate"] = True
            record["source_evidence_kind"] = "repeated_compact_sprite_motion"
            record["guess_state"] = "proposed"
            record["guess_confidence"] = max(
                MOTION_SPRITE_CONFIDENCE,
                float(record.get("guess_confidence", 0.0) or 0.0),
            )
            record["anchor_cell"] = [
                key[1] * EXPLORATION_REGION_CELLS
                + EXPLORATION_REGION_CELLS // 2,
                key[2] * EXPLORATION_REGION_CELLS
                + EXPLORATION_REGION_CELLS // 2,
            ]
            record["guess_label"] = "Possible animated dialogue sprite"
            record["evidence_kind"] = "repeated_compact_sprite_motion"
            record["evidence_summary"] = (
                "compact colorful feature changed repeatedly at a fixed camera "
                "position in a room that already demonstrated multiple dialogue "
                "interactions; requires one bounded interaction test"
            )
            self.motion_sprite_candidates_promoted += 1
            self.map_updates.append(self._screen_region_map_update(key, record))

    def _motion_sprite_approaches(
        self,
        room: str,
        record: dict[str, object],
    ) -> list[tuple[int, int]]:
        value = record.get("anchor_cell")
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            return []
        try:
            anchor = int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return []
        known = [
            (x, y)
            for seen_room, x, y in self.seen_cells
            if seen_room == room
            and 1
            <= max(abs(x - anchor[0]), abs(y - anchor[1]))
            <= MOTION_SPRITE_APPROACH_RADIUS
        ]
        return sorted(
            known,
            key=lambda cell: (
                0 if cell[1] >= anchor[1] else 1,
                abs(cell[0] - anchor[0]) + abs(cell[1] - anchor[1]),
                abs(cell[0] - anchor[0]),
                -cell[1],
                cell[0],
            ),
        )

    @staticmethod
    def _face_toward(
        source: tuple[int, int],
        target: tuple[int, int],
    ) -> str:
        dx = target[0] - source[0]
        dy = target[1] - source[1]
        if abs(dx) > abs(dy):
            return "right" if dx > 0 else "left"
        if dy:
            return "down" if dy > 0 else "up"
        return "right" if dx >= 0 else "left"

    def _motion_sprite_contact_cells(
        self,
        room: str,
        record: dict[str, object],
    ) -> set[tuple[int, int]]:
        value = record.get("anchor_cell")
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            return set()
        try:
            anchor = int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return set()
        return {
            (x, y)
            for seen_room, x, y in self.seen_cells
            if seen_room == room
            and 1 <= max(abs(x - anchor[0]), abs(y - anchor[1])) <= 2
        }

    def _motion_sprite_ready(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> tuple[tuple[str, int, int], dict[str, object], str, tuple[int, int]] | None:
        goal = self.visual_goal
        if goal is None or goal[0] != room:
            return None
        record = self.screen_regions.get(goal)
        if (
            record is None
            or not record.get("motion_sprite_candidate")
            or record.get("motion_sprite_tested")
            or record.get("hypothesis") != "possible_character"
        ):
            return None
        if cell not in self._motion_sprite_contact_cells(room, record):
            return None
        anchor_value = record.get("anchor_cell")
        assert isinstance(anchor_value, (list, tuple))
        target = int(anchor_value[0]), int(anchor_value[1])
        return goal, record, self._face_toward(cell, target), target

    def _finish_failed_motion_sprite_probe(self) -> None:
        goal = self._motion_sprite_pending_goal
        if goal is None or self.interaction_candidate is None or not self.interaction_tried:
            return
        record = self.screen_regions.get(goal)
        if record is not None:
            self.visual_goal = goal
            self._remember_failed_character_probe()
            record["motion_sprite_tested"] = True
            record["last_failure_reason"] = (
                "bounded motion-backed sprite interaction produced no state change"
            )
            self.motion_sprite_failures += 1
            self.map_updates.append(self._screen_region_map_update(goal, record))
        self.interaction_candidate = None
        self.interaction_tried = False
        self._motion_sprite_pending_goal = None

    def _complete_pending_interaction(self) -> None:
        pending = self._motion_sprite_pending_goal
        super()._complete_pending_interaction()
        if pending is None:
            return
        record = self.screen_regions.get(pending)
        if record is not None:
            record["motion_sprite_tested"] = True
            self.map_updates.append(self._screen_region_map_update(pending, record))
        self._motion_sprite_pending_goal = None

    def _explore(self, telemetry: TelemetrySample):
        self._finish_failed_motion_sprite_probe()
        room = self._room_key(telemetry)
        cell = self._cell(telemetry)
        ready = self._motion_sprite_ready(room, cell)
        if ready is not None:
            goal, _record, direction, target = ready
            if (
                telemetry.facing_direction is not None
                and telemetry.facing_direction != direction
            ):
                return self._select(
                    direction,
                    f"align with motion-backed sprite candidate {direction}",
                    telemetry,
                )
            self.interaction_tried = True
            self.interaction_candidate = (
                room,
                *cell,
                direction,
                None,
                None,
                *target,
            )
            self._motion_sprite_pending_goal = goal
            self.motion_sprite_interaction_attempts += 1
            return self._select(
                "confirm",
                "test one source-informed motion-backed dialogue sprite",
                telemetry,
            )
        return super()._explore(telemetry)

    def _direction_to_visual_hypothesis(
        self,
        room: str,
        cell: tuple[int, int],
        *,
        story_focus: bool = False,
        allowed_hypotheses: set[str] | None = None,
    ):
        goal = self.visual_goal
        record = self.screen_regions.get(goal) if goal is not None else None
        if (
            goal is not None
            and record is not None
            and record.get("motion_sprite_candidate")
            and not record.get("motion_sprite_tested")
            and record.get("hypothesis") == "possible_character"
        ):
            adjacency = self._adjacency(room)
            routes = [
                route
                for approach in self._motion_sprite_approaches(room, record)[:16]
                if (
                    route := self._route_to_target(
                        adjacency,
                        cell,
                        approach,
                    )
                )
                is not None
            ]
            if routes:
                direction, _distance = min(
                    routes,
                    key=lambda route: (route[1], route[0]),
                )
                self.decision_visual_goal = goal
                return direction, "possible_character", (goal[1], goal[2])
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
        motion_rank = (
            0
            if story_focus
            and record.get("motion_sprite_candidate")
            and not record.get("motion_sprite_tested")
            else 1
        )
        return (motion_rank, *base)

    def summary(self) -> dict:
        summary = super().summary()
        summary["motion_sprite_candidates_promoted"] = (
            self.motion_sprite_candidates_promoted
        )
        summary["motion_sprite_interaction_attempts"] = (
            self.motion_sprite_interaction_attempts
        )
        summary["motion_sprite_failures"] = self.motion_sprite_failures
        summary["portal_action_conflicts_repaired"] = (
            self.portal_action_conflicts_repaired
        )
        summary["automatic_warps_deprioritized"] = (
            self.automatic_warps_deprioritized
        )
        summary["locked_visual_leads_restored"] = (
            self.locked_visual_leads_restored
        )
        return summary
