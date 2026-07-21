from __future__ import annotations

from pathlib import Path

from .actions import Action
from .observer import Observation
from .perception import GameState, Perception
from .run7_explorer import Run7Explorer
from .telemetry import TelemetrySample


EXIT_EDGE_TOLERANCE_WORLD = 2.0
DARK_VOID_RATIO = 0.72
ANIMATED_SPRITE_MIN_CHANGES = 4
ANIMATED_SPRITE_MIN_COLORFULNESS = 0.08
ANIMATED_CHARACTER_BONUS = 0.12
CHOICE_SIGNATURE_MERGE_DISTANCE = 48
SAVE_MENU_OBJECTS = {"obj_savemenu"}


class Run8Explorer(Run7Explorer):
    """Fixes learned from the screenshot-rich second implementation trial.

    The supplied run used the older run-six revision, but its screenshots expose
    problems that are independent of the bounded run-seven transition fix:

    * near-edge black scenery was treated as an exit even when the detected
      opening stopped a full 32-pixel region before the actual room boundary;
    * bottomless dark pits were treated as traversable passages;
    * compact animated, collision-backed figures were ranked like static
      furniture instead of receiving modest character evidence; and
    * ``obj_savemenu`` was learned as a story choice and retried repeatedly.

    This layer keeps pixel guesses conservative and lets exact movement,
    collision, object-name, and room-bound evidence overrule visual ambiguity.
    """

    def __init__(self, seed: int = 0, memory_path: Path | None = None):
        super().__init__(seed, memory_path)
        self.room_dimensions: dict[str, tuple[float, float]] = {}
        self.retired_inset_exit_guesses = 0
        self.retired_dark_void_guesses = 0
        self.animated_character_bonuses = 0
        self.save_menu_confirms = 0
        self.save_menu_waits = 0
        self.discarded_system_choice_trials = 0
        self.merged_choice_records = 0
        self.cleared_bidirectional_suppressions = 0
        self._active_save_menu_context: tuple[str, int, int] | None = None
        self._save_menu_confirmed_this_session = False
        self._merge_near_duplicate_choice_records()

    @staticmethod
    def _signature_distance(first: str, second: str) -> int:
        if len(first) != len(second):
            return max(len(first), len(second))
        return sum(left != right for left, right in zip(first, second))

    def _merge_near_duplicate_choice_records(self) -> None:
        """Merge animation variants of one unresolved menu at one location."""
        records = list(self.choice_trials)
        kept: list[dict[str, object]] = []
        for record in records:
            match = next(
                (
                    candidate
                    for candidate in kept
                    if candidate.get("room") == record.get("room")
                    and max(
                        abs(
                            int(candidate.get("context_x", -99))
                            - int(record.get("context_x", -199))
                        ),
                        abs(
                            int(candidate.get("context_y", -99))
                            - int(record.get("context_y", -199))
                        ),
                    )
                    <= 2
                    and candidate.get("successful_pattern") is None
                    and record.get("successful_pattern") is None
                    and self._signature_distance(
                        str(candidate.get("signature") or ""),
                        str(record.get("signature") or ""),
                    )
                    <= CHOICE_SIGNATURE_MERGE_DISTANCE
                ),
                None,
            )
            if match is None:
                kept.append(record)
                continue
            for field in ("attempts", "failures", "successes"):
                old = list(match.get(field, []))
                new = list(record.get(field, []))
                size = max(len(old), len(new))
                old.extend([0] * (size - len(old)))
                new.extend([0] * (size - len(new)))
                match[field] = [max(int(old[i]), int(new[i])) for i in range(size)]
            self.merged_choice_records += 1
        if len(kept) != len(records):
            self.choice_trials[:] = kept

    def _find_choice_record(
        self,
        observation: Observation,
        telemetry: TelemetrySample | None,
    ) -> dict[str, object]:
        room, context_x, context_y = self._menu_context(telemetry)
        signature = self._menu_signature(observation)
        matches = [
            record
            for record in self.choice_trials
            if record.get("room") == room
            and max(
                abs(int(record.get("context_x", -99)) - context_x),
                abs(int(record.get("context_y", -99)) - context_y),
            )
            <= 2
            and self._signature_distance(
                str(record.get("signature") or ""),
                signature,
            )
            <= CHOICE_SIGNATURE_MERGE_DISTANCE
        ]
        if matches:
            return min(
                matches,
                key=lambda record: self._signature_distance(
                    str(record.get("signature") or ""),
                    signature,
                ),
            )
        return super()._find_choice_record(observation, telemetry)

    def _observe_room(self, telemetry: TelemetrySample) -> None:
        room = self._room_key(telemetry)
        if (
            telemetry.room_width is not None
            and telemetry.room_height is not None
            and float(telemetry.room_width) > 0
            and float(telemetry.room_height) > 0
        ):
            self.room_dimensions[room] = (
                float(telemetry.room_width),
                float(telemetry.room_height),
            )
        super()._observe_room(telemetry)
        self._clear_bidirectional_suppressions()

    def _clear_bidirectional_suppressions(self) -> None:
        """A confirmed two-way doorway is not itself evidence of a bad loop."""
        for link in list(self.suppressed_room_links):
            if len(link) != 2:
                continue
            first, second = tuple(link)
            forward = any(warp[0] == first and warp[4] == second for warp in self.warps)
            reverse = any(warp[0] == second and warp[4] == first for warp in self.warps)
            if not (forward and reverse):
                continue
            self.suppressed_room_links.discard(link)
            self.world.suppressed_room_links.discard(link)
            self.cleared_bidirectional_suppressions += 1

    def _known_warp_in_region(self, key: tuple[str, int, int]) -> bool:
        room, region_x, region_y = key
        return any(
            source_room == room
            and self._region((source_x, source_y)) == (region_x, region_y)
            for (
                source_room,
                source_x,
                source_y,
                _action,
                _target_room,
                _target_x,
                _target_y,
            ) in self.warps
        )

    def _passage_contacts_true_room_edge(
        self,
        room: str,
        record: dict[str, object],
    ) -> bool | None:
        dimensions = self.room_dimensions.get(room)
        if dimensions is None:
            return None
        value = record.get("passage_box_world") or record.get("feature_box_world")
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            return None
        try:
            left, top, right, bottom = (float(component) for component in value)
        except (TypeError, ValueError):
            return None
        room_width, room_height = dimensions
        edge = str(record.get("edge_hint") or "")
        if edge == "left":
            return left <= EXIT_EDGE_TOLERANCE_WORLD
        if edge == "right":
            return right >= room_width - EXIT_EDGE_TOLERANCE_WORLD
        if edge == "top":
            return top <= EXIT_EDGE_TOLERANCE_WORLD
        if edge == "bottom":
            return bottom >= room_height - EXIT_EDGE_TOLERANCE_WORLD
        return None

    def _retire_visual_exit(
        self,
        key: tuple[str, int, int],
        record: dict[str, object],
        reason: str,
    ) -> None:
        record["hypothesis"] = None
        record["guess_state"] = "retired"
        record["completed_tests"] = max(
            2,
            int(record.get("completed_tests", record.get("inspections", 0)) or 0),
        )
        record["inspections"] = int(record["completed_tests"])
        record["retired_reason"] = reason
        if self.visual_goal == key:
            self.visual_goal = None
            self.decision_visual_goal = None
        self.map_updates.append(self._screen_region_map_update(key, record))

    def _retire_unsupported_visual_exits(self, room: str) -> None:
        for key, record in self.screen_regions.items():
            if key[0] != room or record.get("hypothesis") != "possible_exit":
                continue
            if str(record.get("guess_state") or "") in {
                "confirmed",
                "rejected",
                "retired",
            }:
                continue
            if self._known_warp_in_region(key) or record.get("confirmed_target_room"):
                continue
            contact = self._passage_contacts_true_room_edge(room, record)
            if contact is False:
                self._retire_visual_exit(
                    key,
                    record,
                    "detected dark opening stopped before the true telemetry room boundary",
                )
                self.retired_inset_exit_guesses += 1
                continue
            dark_ratio = float(record.get("dark_ratio", 0.0) or 0.0)
            walkable = self._region_has_walkable_evidence(
                room,
                (key[1], key[2]),
            )
            if (
                contact is True
                and dark_ratio >= DARK_VOID_RATIO
                and not walkable
                and not bool(record.get("confirmed_target_room"))
            ):
                self._retire_visual_exit(
                    key,
                    record,
                    "edge feature was mostly dark void with no mapped walkable approach",
                )
                self.retired_dark_void_guesses += 1

    def _apply_animated_character_bonus(self, room: str) -> None:
        for key, record in self.screen_regions.items():
            if key[0] != room or record.get("hypothesis") != "possible_character":
                continue
            summary = str(record.get("visual_summary") or "").casefold()
            if not (summary.startswith("compact") or summary.startswith("tall")):
                continue
            changes = int(record.get("appearance_changes", 0) or 0)
            colorfulness = float(record.get("colorfulness", 0.0) or 0.0)
            sequence = int(record.get("last_seen_sequence", 0) or 0)
            if (
                changes < ANIMATED_SPRITE_MIN_CHANGES
                or colorfulness < ANIMATED_SPRITE_MIN_COLORFULNESS
                or record.get("screenshot_bonus_sequence") == sequence
            ):
                continue
            confidence = float(record.get("guess_confidence", 0.0) or 0.0)
            record["guess_confidence"] = round(
                min(0.95, confidence + ANIMATED_CHARACTER_BONUS),
                3,
            )
            record["screenshot_bonus_sequence"] = sequence
            record["animated_sprite_evidence"] = True
            evidence = str(record.get("evidence_summary") or "")
            note = "compact feature changed across screenshots like an animated sprite"
            if note not in evidence:
                record["evidence_summary"] = f"{evidence}; {note}" if evidence else note
            self.animated_character_bonuses += 1
            self.map_updates.append(self._screen_region_map_update(key, record))

    def _observe_screen(
        self,
        observation: Observation,
        telemetry: TelemetrySample,
    ) -> None:
        super()._observe_screen(observation, telemetry)
        room = self._room_key(telemetry)
        self._retire_unsupported_visual_exits(room)
        self._apply_animated_character_bonus(room)

    def _discard_choice_trials_for_context(
        self,
        context: tuple[str, int, int],
    ) -> None:
        room, x, y = context
        kept = []
        for record in self.choice_trials:
            same_context = (
                record.get("room") == room
                and max(
                    abs(int(record.get("context_x", -99)) - x),
                    abs(int(record.get("context_y", -99)) - y),
                )
                <= 2
            )
            if same_context and record.get("successful_pattern") is None:
                self.discarded_system_choice_trials += 1
                continue
            kept.append(record)
        self.choice_trials[:] = kept
        self.active_choice_record = None
        self.pending_choice_record = None
        self.pending_choice_pattern = None
        self.pending_choice_confirmed = False
        self.pending_choice_started_at = None
        self.menu_action_queue.clear()
        self.choice_settle_steps = 0

    def _save_menu_context(
        self,
        telemetry: TelemetrySample,
    ) -> tuple[str, int, int]:
        if self.active_interaction_key is not None:
            return self.active_interaction_key
        cell = self._cell(telemetry)
        return self._room_key(telemetry), cell[0], cell[1]

    def _mark_active_interaction_as_save_point(
        self,
        context: tuple[str, int, int],
    ) -> None:
        record = self.interactables.get(context)
        if record is None:
            return
        outcomes = dict(record.get("outcome_counts", {}))
        outcomes["save_menu"] = int(outcomes.get("save_menu", 0)) + 1
        record["classification"] = "save_point"
        record["usefulness"] = "utility"
        record["choice_menus"] = 0
        record["last_outcome"] = "save_menu"
        record["outcome_counts"] = outcomes
        record["system_menu_object"] = "obj_savemenu"
        self.map_updates.append(
            {
                "type": "interaction_outcome",
                "room": context[0],
                "cell": [context[1], context[2]],
                "classification": "save_point",
                "usefulness": "utility",
                "choice_menus": 0,
                "last_outcome": "save_menu",
                "outcome_counts": outcomes,
            }
        )

    def _choose_save_menu(
        self,
        telemetry: TelemetrySample,
    ) -> Action:
        interaction_was_pending = self.interaction_candidate is not None
        self._complete_pending_interaction()
        context = self._save_menu_context(telemetry)
        self._discard_choice_trials_for_context(context)
        self._mark_active_interaction_as_save_point(context)
        self._observe_story_state(
            GameState.MENU,
            telemetry,
            interaction_was_pending,
        )
        self._suspend_movement_learning()

        if self._active_save_menu_context != context:
            self._active_save_menu_context = context
            self._save_menu_confirmed_this_session = False
        if not self._save_menu_confirmed_this_session:
            self._save_menu_confirmed_this_session = True
            self.save_menu_confirms += 1
            return self._select(
                "confirm",
                "save point utility menu; confirm Save once and do not learn a story choice",
                telemetry,
            )
        self.save_menu_waits += 1
        return self._select(
            "wait",
            "save point already confirmed; wait for the menu to close",
            telemetry,
        )

    def choose(
        self,
        observation: Observation,
        perception: Perception,
        telemetry: TelemetrySample | None = None,
    ) -> Action:
        object_name = str(telemetry.object_name or "") if telemetry is not None else ""
        if telemetry is not None and object_name.casefold() in SAVE_MENU_OBJECTS:
            return self._choose_save_menu(telemetry)
        if self._active_save_menu_context is not None:
            self._active_save_menu_context = None
            self._save_menu_confirmed_this_session = False
        return super().choose(observation, perception, telemetry)

    def _story_interaction_retryable(self, key: tuple[str, int, int]) -> bool:
        record = self.interactables.get(key)
        if record is not None and (
            str(record.get("classification") or "") == "save_point"
            or str(record.get("usefulness") or "") == "utility"
            or str(record.get("system_menu_object") or "").casefold()
            in SAVE_MENU_OBJECTS
        ):
            return False
        return super()._story_interaction_retryable(key)

    def summary(self) -> dict:
        summary = super().summary()
        rooms = set(summary.get("rooms_seen", []))
        for source, target in self.transitions:
            rooms.update((source, target))
        for warp in self.warps:
            rooms.update((warp[0], warp[4]))
        if self.observed_room:
            rooms.add(self.observed_room)
        summary["rooms_seen"] = sorted(room for room in rooms if room)
        summary["retired_inset_exit_guesses"] = self.retired_inset_exit_guesses
        summary["retired_dark_void_guesses"] = self.retired_dark_void_guesses
        summary["animated_character_bonuses"] = self.animated_character_bonuses
        summary["save_menu_confirms"] = self.save_menu_confirms
        summary["save_menu_waits"] = self.save_menu_waits
        summary["discarded_system_choice_trials"] = (
            self.discarded_system_choice_trials
        )
        summary["merged_choice_records"] = self.merged_choice_records
        summary["cleared_bidirectional_suppressions"] = (
            self.cleared_bidirectional_suppressions
        )
        summary["expected_navigation_map_pixels"] = {
            room: [round(width * 4), round(height * 4)]
            for room, (width, height) in sorted(self.room_dimensions.items())
        }
        return summary
