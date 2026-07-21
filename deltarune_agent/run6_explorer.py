from __future__ import annotations

from collections import Counter, deque
from pathlib import Path

from .actions import Action
from .navigation_semantics import refresh_portal_classification
from .observer import Observation
from .perception import GameState, Perception
from .policy import DIRECTION_VECTORS, OPPOSITE
from .run4_explorer import Run4Explorer
from .telemetry import TelemetrySample
from .world_model import CELL_SIZE


ENTRY_EDGE_MARGIN_CELLS = 4
ENTRY_ESCAPE_RADIUS_CELLS = 5
ENTRY_ESCAPE_STEPS = 18
AUTOMATIC_SEQUENCE_SETTLE_STEPS = 12
PERSISTENT_LINK_BOUNCES = 2
CHOICE_OUTCOME_WINDOW_STEPS = 64


class Run6Explorer(Run4Explorer):
    """Explorer fixes learned from the fifth and sixth playthroughs.

    Run six reached the Dark World, but exact v9 transition evidence was still
    overridden by the last requested movement key. One corridor doorway was
    consequently learned with contradictory actions and crossed repeatedly.
    Automatic dialogue boundaries also inflated story progress, and a choice
    was learned as successful without the agent ever sending its planned
    confirm. This layer repairs those semantics while preserving the map.
    """

    def __init__(self, seed: int = 0, memory_path: Path | None = None):
        super().__init__(seed, memory_path)
        self.transition_direction_overrides = 0
        self.entry_escape_moves = 0
        self.room_discoveries = 0
        self.automatic_sequence_events = 0
        self.automatic_sequence_active = False
        self.automatic_sequence_settle_steps = 0
        self.pending_choice_started_at: int | None = None
        self.pending_choice_confirmed = False
        self.room_link_bounces: Counter[frozenset[str]] = Counter()
        self.run6_room_trace: deque[str] = deque(maxlen=8)
        self.entry_escape: dict[
            str,
            tuple[str, tuple[int, int], int],
        ] = {}

        # Old A-B-A observations were stored permanently after only one return.
        # That can blacklist a required story corridor in the next run. Keep
        # suppression session-local; reliable portal outcome metadata still
        # persists and can rank a route as return/backtrack or loop-suppressed.
        self.cleared_persistent_room_suppressions = len(
            self.world.suppressed_room_links
        )
        self.world.suppressed_room_links.clear()
        self.suppressed_room_links = set()

        self.ambiguous_portal_links_repaired = 0
        self.ambiguous_portals_removed = 0
        self.portal_progress_counts_normalized = 0
        self.legacy_choice_successes_reset = 0
        self._repair_ambiguous_portal_links()
        self._normalize_legacy_portal_progress()
        self._reset_legacy_choice_successes()

    def _repair_ambiguous_portal_links(self) -> None:
        """Rebuild links whose persisted portals disagree on the action.

        Run3 already canonicalizes the raw warp counter from room geometry.
        Rich portal records were introduced later and retained their old action
        variants. Rebuild only a link for which the canonical counter now has a
        single action but persisted records still have several actions.
        """
        canonical_actions: dict[tuple[str, str], set[str]] = {}
        for warp in self.warps:
            canonical_actions.setdefault((warp[0], warp[4]), set()).add(warp[3])

        portal_actions: dict[tuple[str, str], set[str]] = {}
        for record in self.world.warp_portals.values():
            link = (
                str(record.get("from_room") or ""),
                str(record.get("to_room") or ""),
            )
            portal_actions.setdefault(link, set()).add(
                str(record.get("action") or "event")
            )

        repaired_links = {
            link
            for link, actions in portal_actions.items()
            if len(actions) > 1 and len(canonical_actions.get(link, set())) == 1
        }
        if not repaired_links:
            return

        for portal_id, record in list(self.world.warp_portals.items()):
            link = (
                str(record.get("from_room") or ""),
                str(record.get("to_room") or ""),
            )
            if link in repaired_links:
                self.world.warp_portals.pop(portal_id, None)
                self.ambiguous_portals_removed += 1
        self.world.reconcile_warp_portals()
        self.ambiguous_portal_links_repaired = len(repaired_links)

    def _normalize_legacy_portal_progress(self) -> None:
        """Collapse repeated dialogue boundaries into one sequence outcome."""
        for record in self.world.warp_portals.values():
            raw = record.get("progress_outcomes")
            if not isinstance(raw, dict):
                continue
            automatic = 0
            normalized: dict[str, int] = {}
            for name, value in raw.items():
                try:
                    count = max(0, int(value or 0))
                except (TypeError, ValueError):
                    continue
                if str(name).casefold().startswith("automatic"):
                    automatic += count
                elif count:
                    normalized[str(name)] = count
            if automatic:
                normalized["automatic sequence"] = 1
            total = sum(normalized.values())
            old_total = int(record.get("non_discovery_progress_outcomes", 0) or 0)
            if normalized != raw or total != old_total:
                record["progress_outcomes"] = normalized
                record["non_discovery_progress_outcomes"] = total
                refresh_portal_classification(record)
                self.portal_progress_counts_normalized += 1

    def _reset_legacy_choice_successes(self) -> None:
        """Forget successes learned before explicit confirmation was required."""
        for record in self.choice_trials:
            successes = record.get("successes")
            had_success = record.get("successful_pattern") is not None or (
                isinstance(successes, list)
                and any(int(value or 0) > 0 for value in successes)
            )
            if not had_success:
                continue
            record["successful_pattern"] = None
            record["successes"] = [0 for _ in range(len(successes or []))]
            self.legacy_choice_successes_reset += 1

    def _source_transition_cell(
        self,
        telemetry: TelemetrySample,
    ) -> tuple[int, int] | None:
        x = (
            telemetry.transition_from_foot_x
            if telemetry.transition_from_foot_x is not None
            else telemetry.transition_from_x
        )
        y = (
            telemetry.transition_from_foot_y
            if telemetry.transition_from_foot_y is not None
            else telemetry.transition_from_y
        )
        if x is not None and y is not None:
            return int(float(x) // CELL_SIZE), int(float(y) // CELL_SIZE)
        return self.observed_cell

    def _edge_direction_from_seen_cells(
        self,
        room: str,
        cell: tuple[int, int] | None,
    ) -> str | None:
        if cell is None:
            return None
        cells = [
            (x, y)
            for seen_room, x, y in self.seen_cells
            if seen_room == room
        ]
        if len(cells) < 8:
            return None
        xs = [point[0] for point in cells]
        ys = [point[1] for point in cells]
        distances = {
            "left": abs(cell[0] - min(xs)),
            "right": abs(max(xs) - cell[0]),
            "up": abs(cell[1] - min(ys)),
            "down": abs(max(ys) - cell[1]),
        }
        nearest = min(distances.values())
        choices = [name for name, distance in distances.items() if distance == nearest]
        if nearest > 2 or len(choices) != 1:
            return None
        return choices[0]

    def _transition_source_direction(
        self,
        telemetry: TelemetrySample,
        source_room: str,
    ) -> str | None:
        source_cell = self._source_transition_cell(telemetry)
        geometry = self._edge_direction_from_seen_cells(source_room, source_cell)
        if geometry is not None:
            return geometry
        if telemetry.transition_from_facing in DIRECTION_VECTORS:
            return telemetry.transition_from_facing
        for candidate in (self.last_movement, self.last_overworld_movement):
            if candidate in DIRECTION_VECTORS:
                return candidate
        return None

    def _arrival_return_direction(
        self,
        telemetry: TelemetrySample,
        source_direction: str | None,
    ) -> str | None:
        cell = self._cell(telemetry)
        if telemetry.room_width is not None and telemetry.room_height is not None:
            max_x = max(0, int((float(telemetry.room_width) - 1) // CELL_SIZE))
            max_y = max(0, int((float(telemetry.room_height) - 1) // CELL_SIZE))
            distances = {
                "left": cell[0],
                "right": max_x - cell[0],
                "up": cell[1],
                "down": max_y - cell[1],
            }
            nearest = min(distances.values())
            choices = [
                name for name, distance in distances.items() if distance == nearest
            ]
            expected = OPPOSITE.get(source_direction or "")
            if nearest <= ENTRY_EDGE_MARGIN_CELLS:
                return expected if expected in choices else choices[0]

        room = self._room_key(telemetry)
        geometry = self._edge_direction_from_seen_cells(room, cell)
        if geometry is not None:
            return geometry
        return None

    def _observe_room(self, telemetry: TelemetrySample) -> None:
        previous_room = self.observed_room
        room = self._room_key(telemetry)
        transition = previous_room is not None and room != previous_room
        original_direction = self.last_movement or self.last_overworld_movement
        source_direction = (
            self._transition_source_direction(telemetry, previous_room)
            if transition and previous_room is not None
            else None
        )
        if transition and source_direction in DIRECTION_VECTORS:
            if original_direction != source_direction:
                self.transition_direction_overrides += 1
            # StarterPolicy records last_movement before exact telemetry. Feed
            # it the corrected source direction so Run2's guard and the portal
            # record both use the same evidence.
            self.last_movement = source_direction
            self.last_overworld_movement = source_direction

        super()._observe_room(telemetry)
        if not transition or previous_room is None:
            return

        self.last_movement = None
        self.last_overworld_movement = None
        arrival = self._cell(telemetry)
        return_direction = self._arrival_return_direction(
            telemetry,
            source_direction,
        )
        if return_direction in DIRECTION_VECTORS:
            expires_at = self.navigation_tick + ENTRY_ESCAPE_STEPS
            self.entry_direction_guards[room] = (
                return_direction,
                arrival,
                expires_at,
            )
            self.entry_escape[room] = (
                OPPOSITE[return_direction],
                arrival,
                expires_at,
            )

        if not self.run6_room_trace:
            self.run6_room_trace.append(previous_room)
        if self.run6_room_trace[-1] != room:
            self.run6_room_trace.append(room)
        link = frozenset((previous_room, room))
        bounced = (
            len(self.run6_room_trace) >= 3
            and self.run6_room_trace[-3] == self.run6_room_trace[-1]
            and self.run6_room_trace[-2] != self.run6_room_trace[-1]
        )
        if bounced:
            self.room_link_bounces[link] += 1
        if self.room_link_bounces[link] < PERSISTENT_LINK_BOUNCES:
            self.suppressed_room_links.discard(link)
        else:
            self.suppressed_room_links.add(link)

    def _entry_escape_action(
        self,
        telemetry: TelemetrySample,
    ) -> Action | None:
        if telemetry.mode != "overworld" or telemetry.player_controlled is not True:
            return None
        room = self._room_key(telemetry)
        guard = self.entry_escape.get(room)
        if guard is None:
            return None
        direction, arrival, expires_at = guard
        cell = self._cell(telemetry)
        distance = max(abs(cell[0] - arrival[0]), abs(cell[1] - arrival[1]))
        if self.navigation_tick >= expires_at or distance > ENTRY_ESCAPE_RADIUS_CELLS:
            self.entry_escape.pop(room, None)
            return None
        if self._blocked_near(room, cell, direction):
            self.entry_escape.pop(room, None)
            return None
        self.entry_escape_moves += 1
        return self._select(
            direction,
            "clear arrival portal before replanning; move "
            f"{direction} into {room}",
            telemetry,
        )

    def choose(
        self,
        observation: Observation,
        perception: Perception,
        telemetry: TelemetrySample | None = None,
    ) -> Action:
        if (
            telemetry is not None
            and self.observed_room is not None
            and self._room_key(telemetry) != self.observed_room
        ):
            self._observe_room(telemetry)
        if telemetry is not None:
            escape = self._entry_escape_action(telemetry)
            if escape is not None:
                return escape
        return super().choose(observation, perception, telemetry)

    def _start_choice_trial(
        self,
        observation: Observation,
        telemetry: TelemetrySample | None,
    ) -> None:
        super()._start_choice_trial(observation, telemetry)
        self.pending_choice_started_at = self.navigation_tick
        self.pending_choice_confirmed = False

    def _choose_menu_action(
        self,
        observation: Observation,
        telemetry: TelemetrySample | None,
        menu_started: bool,
    ) -> Action:
        action = super()._choose_menu_action(
            observation,
            telemetry,
            menu_started,
        )
        if action.name == "confirm" and self.pending_choice_record is not None:
            self.pending_choice_confirmed = True
        return action

    def _choice_outcome_is_eligible(self) -> bool:
        if (
            self.pending_choice_record is None
            or self.pending_choice_pattern is None
            or not self.pending_choice_confirmed
            or self.pending_choice_started_at is None
        ):
            return False
        return (
            self.navigation_tick - self.pending_choice_started_at
            <= CHOICE_OUTCOME_WINDOW_STEPS
        )

    def _record_room_discovery(
        self,
        telemetry: TelemetrySample | None,
    ) -> None:
        self.room_discoveries += 1
        self.story_stall_steps = 0
        update: dict[str, object] = {
            "type": "room_discovery",
            "discovery": self.room_discoveries,
        }
        if telemetry is not None:
            update["room"] = self._room_key(telemetry)
            update["cell"] = list(self._cell(telemetry))
        self.map_updates.append(update)

    def _record_story_progress(
        self,
        event: str,
        telemetry: TelemetrySample | None,
    ) -> None:
        if event == "discovered a new room":
            eligible_choice = self._choice_outcome_is_eligible()
            if self.pending_choice_record is not None and not eligible_choice:
                self._mark_pending_choice_failed(
                    "room changed before the planned choice confirm",
                    prioritize_retry=True,
                )
            if eligible_choice:
                super()._record_story_progress(
                    "confirmed choice led to a new room",
                    telemetry,
                )
            self._record_room_discovery(telemetry)
            return

        if self.pending_choice_record is not None and not self._choice_outcome_is_eligible():
            self._mark_pending_choice_failed(
                "state changed before an explicit, recent choice confirm",
                prioritize_retry=True,
            )
        super()._record_story_progress(event, telemetry)

    def _observe_story_state(
        self,
        state: GameState,
        telemetry: TelemetrySample | None,
        interaction_was_pending: bool,
    ) -> None:
        """Record one event per automatic sequence, not per writer boundary."""
        if self.active_interaction_key is not None:
            self.automatic_sequence_active = False
            self.automatic_sequence_settle_steps = 0
            if state is GameState.DIALOGUE:
                self.active_interaction_dialogue_steps += 1
            elif state is GameState.CUTSCENE:
                self.active_interaction_cutscene_steps += 1
            elif state is GameState.BATTLE:
                self.active_interaction_saw_battle = True
            elif state is GameState.OVERWORLD:
                self._finish_active_interaction(telemetry)

        if (
            state is GameState.OVERWORLD
            and self.previous_state is GameState.MENU
            and self.active_interaction_key is None
            and self.pending_choice_record is not None
        ):
            self._mark_pending_choice_failed(
                "menu closed without observed story progress",
                prioritize_retry=False,
            )

        automatic_state = (
            state in {GameState.DIALOGUE, GameState.CUTSCENE}
            and not interaction_was_pending
            and self.active_interaction_key is None
        )
        if automatic_state:
            self.automatic_sequence_settle_steps = 0
            if not self.automatic_sequence_active:
                self.automatic_sequence_active = True
                self.automatic_sequence_events += 1
                self._record_story_progress(
                    "automatic scripted sequence",
                    telemetry,
                )
        elif (
            state is GameState.OVERWORLD
            and telemetry is not None
            and telemetry.mode == "overworld"
            and telemetry.player_controlled is True
            and self.automatic_sequence_active
        ):
            self.automatic_sequence_settle_steps += 1
            if (
                self.automatic_sequence_settle_steps
                >= AUTOMATIC_SEQUENCE_SETTLE_STEPS
            ):
                self.automatic_sequence_active = False
                self.automatic_sequence_settle_steps = 0
        elif state in {GameState.DIALOGUE, GameState.CUTSCENE, GameState.MENU}:
            self.automatic_sequence_settle_steps = 0

        self.previous_state = state

    def summary(self) -> dict:
        summary = super().summary()
        summary["room_discoveries"] = self.room_discoveries
        summary["automatic_sequence_events"] = self.automatic_sequence_events
        summary["transition_direction_overrides"] = (
            self.transition_direction_overrides
        )
        summary["entry_escape_moves"] = self.entry_escape_moves
        summary["session_room_link_bounces"] = sum(
            self.room_link_bounces.values()
        )
        summary["cleared_persistent_room_suppressions"] = (
            self.cleared_persistent_room_suppressions
        )
        summary["ambiguous_portal_links_repaired"] = (
            self.ambiguous_portal_links_repaired
        )
        summary["ambiguous_portals_removed"] = self.ambiguous_portals_removed
        summary["portal_progress_counts_normalized"] = (
            self.portal_progress_counts_normalized
        )
        summary["legacy_choice_successes_reset"] = (
            self.legacy_choice_successes_reset
        )
        return summary
