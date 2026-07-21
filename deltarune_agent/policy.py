from collections import deque
from math import hypot
from pathlib import Path

from .actions import ACTIONS, Action
from .observer import Observation
from .perception import GameState, Perception, looks_like_dialogue_choice
from .room_view import RoomViewMemory
from .screen_regions import analyze_screen_regions, visible_region_coordinates
from .telemetry import TelemetrySample
from .world_model import CELL_SIZE, EXPLORATION_REGION_CELLS, Edge, Warp, WorldModel


DIRECTION_VECTORS = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}
OPPOSITE = {"up": "down", "down": "up", "left": "right", "right": "left"}
MOVEMENT_COMMIT_STEPS = 3
COLLISION_CONFIRM_SAMPLES = 3
WARP_SEEK_STEPS = 12
EXIT_PROBE_COMMIT_STEPS = 4
MAX_EXIT_PROBES = 1
BACKTRACK_WARP_RADIUS = 2
INTERACTION_MEMORY_RADIUS = 6
SCREEN_ANALYSIS_INTERVAL = 5
LOW_AREA_LOOP_SAMPLES = 10
LOOP_DIRECTION_COOLDOWN = 24
VISUAL_GOAL_STALL_LIMIT = 8
VISUAL_GOAL_AGE_LIMIT = 24
MAX_ROOM_VISUAL_HYPOTHESES = 12
STORY_SEARCH_STEPS = 48
CHARACTER_APPROACH_DIRECTIONS = 2
CHARACTER_SINGLE_APPROACH_MAX_TARGETS = 2
CHARACTER_SINGLE_APPROACH_MIN_VIEWS = 3
CHARACTER_SINGLE_APPROACH_MIN_INTEREST = 0.18
CHARACTER_PROBE_VERSION = 1
LEGACY_CHARACTER_PROBE_FAILURES = 4
CHOICE_CONFIRM_SETTLE_STEPS = 2
LEGACY_CHOICE_DIALOGUE_STEPS = 30
STORY_USEFULNESS_RANK = {
    "progress": 0,
    "choice_pending": 1,
    "unknown": 2,
    "flavor": 3,
}
CHOICE_PATTERNS: tuple[tuple[str, ...], ...] = (
    (),
    ("down",),
    ("right",),
    ("down", "down"),
    ("right", "right"),
    ("down", "right"),
    ("right", "down"),
    ("left",),
    ("up",),
)
CHOICE_RESET: tuple[str, ...] = ("up", "up", "up", "left", "left", "left")
# Give each response pattern one fair attempt before retiring an unresolved
# choice.  Keeping this tied to the pattern table prevents newly added menu
# strategies from becoming unreachable.
CHOICE_REENGAGEMENT_LIMIT = len(CHOICE_PATTERNS)


class StarterPolicy:
    """Deterministic frontier explorer with state-aware collision learning."""

    def __init__(self, seed: int = 0, memory_path: Path | None = None):
        self.world = WorldModel.load(memory_path)
        self.memory_warning = self.world.load_warning
        self.fallback_offset = seed % len(DIRECTION_VECTORS)
        self.direction = "down"
        self.direction_steps = 0
        self.last_movement: str | None = None
        self.last_overworld_movement: str | None = None
        self.last_position: tuple[float, float] | None = None
        self.last_room: str | None = None
        self.last_cell: tuple[int, int] | None = None
        self.observed_room: str | None = None
        self.observed_cell: tuple[int, int] | None = None
        self.failed_movement = False
        self.collision_pending = False
        self.stationary_streak = 0
        self.stationary_key: tuple[str, int, int, str] | None = None
        self.awaiting_fresh_telemetry = False
        self.last_movement_sample_at: float | None = None
        self.input_not_registered = False
        self.unregistered_input_streak = 0
        self.unexpected_displacement = False
        self.interaction_tried = False
        self.pending_blocked_direction: str | None = None
        self.visits = self.world.visits
        self.blocked = self.world.blocked
        self.blocked_zones = set(self.blocked)
        self.tried = self.world.tried
        self.tried_regions = {
            (room, x // EXPLORATION_REGION_CELLS, y // EXPLORATION_REGION_CELLS, direction)
            for room, x, y, direction in self.tried
        }
        self.open_edges = self.world.open_edges
        self.tried_regions.update(
            (
                room,
                source_x // EXPLORATION_REGION_CELLS,
                source_y // EXPLORATION_REGION_CELLS,
                direction,
            )
            for (
                room,
                source_x,
                source_y,
                direction,
                _target_x,
                _target_y,
            ) in self.open_edges
        )
        self.seen_cells = self.world.seen_cells
        self.seen_regions = {
            (room, x // EXPLORATION_REGION_CELLS, y // EXPLORATION_REGION_CELLS)
            for room, x, y in self.seen_cells
        }
        self.interacted_zones: set[tuple[str, int, int]] = set()
        self.interacted_targets: set[tuple[str, int, int]] = set()
        self.interacted_instances: set[tuple[str, int]] = set()
        self.interactables = self.world.interactables
        for (known_room, target_x, target_y), record in self.interactables.items():
            self.interacted_targets.add((known_room, target_x, target_y))
            for approach in record.get("approaches", []):
                self.interacted_zones.add(
                    (known_room, int(approach["x"]), int(approach["y"]))
                )
            instance_id = record.get("instance_id")
            if isinstance(instance_id, int) and instance_id >= 0:
                self.interacted_instances.add((known_room, instance_id))
        self.interaction_candidate: (
            tuple[str, int, int, str, int | None, str | None, int, int] | None
        ) = None
        self.completed_interaction: tuple[str, int, int, str] | None = None
        self.transitions = self.world.transitions
        self.warps = self.world.warps
        self.exit_probes = self.world.exit_probes
        self.character_probes = self.world.character_probes
        self.exit_search_goal: tuple[str, int, int, str] | None = None
        self.screen_regions = self.world.screen_regions
        for (screen_room, region_x, region_y), record in self.screen_regions.items():
            if record.get("hypothesis") == "possible_interactable":
                # Older builds treated any distinctive interior texture as an
                # interactable. That evidence was too weak and caused scenery
                # to be revisited as an NPC.
                record["hypothesis"] = None
            if (
                record.get("hypothesis") == "possible_character"
                and not self._region_has_character_topology(
                    screen_room,
                    (region_x, region_y),
                )
            ):
                # Older builds treated autonomous animation as NPC evidence.
                # Most Deltarune NPCs are stationary, while fireplaces and
                # other scenery animate, so discard that evidence on load.
                record["hypothesis"] = None
            elif record.get("hypothesis") == "possible_character":
                if int(record.get("character_probe_version", 0)) < CHARACTER_PROBE_VERSION:
                    # Earlier "inspections" ended as soon as Kris entered the
                    # same broad region. They did not prove that Kris reached
                    # an interaction side, so give those learned leads a real
                    # probe under the exact-approach planner.
                    record["inspections"] = 0
                record["character_probe_version"] = CHARACTER_PROBE_VERSION
        self.choice_trials = self.world.choice_trials
        self.room_view = (
            RoomViewMemory(memory_path.parent / "room_views")
            if memory_path is not None
            else None
        )
        if self.room_view is not None and self.room_view.load_warning:
            self.memory_warning = " ".join(
                warning
                for warning in (self.memory_warning, self.room_view.load_warning)
                if warning
            )
        self.current_visible_regions: set[tuple[str, int, int]] = set()
        self.visual_goal: tuple[str, int, int] | None = None
        self.visual_goal_age = 0
        self.visual_goal_stalls = 0
        self.visual_goal_best_distance: int | None = None
        self.room_entry_from = self.world.room_entry_from
        self.recent_rooms: deque[str] = deque(maxlen=8)
        self.suppressed_room_links = self.world.suppressed_room_links
        self.recent_cells: deque[tuple[str, int, int]] = deque(maxlen=24)
        self.decision_history: deque[tuple[str, int, int, str]] = deque(maxlen=8)
        self.oscillation_breaks = 0
        self.loop_reason = "detected repeated movement loop"
        self.navigation_tick = 0
        self.loop_direction_cooldowns: dict[
            tuple[str, int, int], tuple[frozenset[str], int]
        ] = {}
        self.steps_without_frontier = 0
        self.reason = "starting"
        self.map_updates: list[dict[str, object]] = []
        self.stalled_recovery_steps = 0
        self.previous_state: GameState | None = None
        self.story_epoch = max(
            (
                int(record.get("last_story_epoch", 0))
                for record in self.interactables.values()
            ),
            default=0,
        )
        self.story_progress_events = 0
        self.story_stall_steps = 0
        self.active_interaction_key: tuple[str, int, int] | None = None
        self.active_interaction_dialogue_steps = 0
        self.active_interaction_cutscene_steps = 0
        self.active_interaction_saw_battle = False
        self.menu_action_queue: deque[str] = deque()
        self.interaction_cooldowns: dict[tuple[str, int, int, str], int] = {}
        self.active_choice_record: dict[str, object] | None = None
        self.pending_choice_record: dict[str, object] | None = None
        self.pending_choice_pattern: int | None = None
        self.pending_choice_epoch = self.story_epoch
        self.choice_settle_steps = 0
        self.choice_session_trials = 0
        if any(self._story_interaction_retryable(key) for key in self.interactables):
            self.story_stall_steps = STORY_SEARCH_STEPS

    def choose(
        self,
        observation: Observation,
        perception: Perception,
        telemetry: TelemetrySample | None = None,
    ) -> Action:
        state = perception.state
        if (
            state is GameState.DIALOGUE
            and (
                (
                    observation.visual_valid
                    and looks_like_dialogue_choice(observation.frame)
                )
                or (
                    self.active_choice_record is not None
                    and (
                        self.menu_action_queue
                        or not observation.visual_valid
                    )
                )
            )
        ):
            # Some Deltarune prompts are rendered by obj_writer rather than a
            # choicer object. Preserve telemetry dialogue authority while
            # treating the visibly repeated response markers as a menu.
            state = GameState.MENU
        menu_started = state is GameState.MENU and self.previous_state is not GameState.MENU
        interaction_was_pending = self.interaction_candidate is not None
        if state in {
            GameState.DIALOGUE,
            GameState.CUTSCENE,
            GameState.MENU,
            GameState.BATTLE,
        }:
            self._complete_pending_interaction()
        self._observe_story_state(state, telemetry, interaction_was_pending)
        if state is GameState.OVERWORLD and telemetry and telemetry.mode == "overworld":
            self._observe_room(telemetry)
            self._learn_movement_result(telemetry)
            self._observe_screen(observation, telemetry)
        else:
            self._suspend_movement_learning()
        if state is GameState.DIALOGUE:
            return self._select("confirm", "advance dialogue", telemetry)
        if state is GameState.CUTSCENE:
            return self._select("confirm", "advance detected cutscene", telemetry)
        if state is GameState.BATTLE:
            # A deterministic dodge cycle is easier to inspect than random key mashing.
            names = ["left", "up", "right", "down", "confirm"]
            return self._select(names[observation.step % len(names)], "battle cycle", telemetry)
        if state is GameState.MENU:
            if (
                not observation.visual_valid
                and self.active_choice_record is not None
            ):
                return self._select(
                    "wait",
                    "choice capture stale; wait for a fresh menu frame",
                    telemetry,
                )
            return self._choose_menu_action(observation, telemetry, menu_started)
        if state is GameState.UNKNOWN:
            name = "confirm" if observation.step % 8 == 0 else "wait"
            return self._select(name, "wait through unknown state", telemetry)

        if telemetry and telemetry.mode == "overworld":
            return self._explore(telemetry)

        # Vision-only fallback when the telemetry patch is unavailable.
        if self.direction_steps <= 0:
            directions = ["down", "right", "up", "left"]
            index = (observation.step // 12 + self.fallback_offset) % len(directions)
            self.direction = directions[index]
            self.direction_steps = 12
        self.direction_steps -= 1
        return self._select(self.direction, "vision-only exploration", telemetry)

    @staticmethod
    def _menu_signature(observation: Observation) -> str:
        image = observation.frame.convert("L").resize((16, 12))
        return "".join(format(int(value) // 64, "x") for value in image.getdata())

    def _menu_context(
        self,
        telemetry: TelemetrySample | None,
    ) -> tuple[str, int, int]:
        if self.active_interaction_key is not None:
            return self.active_interaction_key
        if telemetry is None:
            return "unknown", -1, -1
        x = telemetry.player_x if telemetry.player_x is not None else telemetry.x
        y = telemetry.player_y if telemetry.player_y is not None else telemetry.y
        return self._room_key(telemetry), int(x // CELL_SIZE), int(y // CELL_SIZE)

    @staticmethod
    def _signature_distance(first: str, second: str) -> int:
        if len(first) != len(second):
            return max(len(first), len(second))
        return sum(left != right for left, right in zip(first, second))

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
            and self._signature_distance(str(record.get("signature") or ""), signature)
            <= 24
        ]
        if matches:
            return min(
                matches,
                key=lambda record: self._signature_distance(
                    str(record.get("signature") or ""), signature
                ),
            )
        record: dict[str, object] = {
            "room": room,
            "context_x": context_x,
            "context_y": context_y,
            "signature": signature,
            "attempts": [0] * len(CHOICE_PATTERNS),
            "failures": [0] * len(CHOICE_PATTERNS),
            "successes": [0] * len(CHOICE_PATTERNS),
            "successful_pattern": None,
        }
        self.choice_trials.append(record)
        return record

    @staticmethod
    def _choice_counts(record: dict[str, object], name: str) -> list[int]:
        raw = record.get(name)
        values = [int(value) for value in raw] if isinstance(raw, list) else []
        values.extend([0] * (len(CHOICE_PATTERNS) - len(values)))
        record[name] = values[: len(CHOICE_PATTERNS)]
        return record[name]  # type: ignore[return-value]

    def _start_choice_trial(
        self,
        observation: Observation,
        telemetry: TelemetrySample | None,
    ) -> None:
        record = self._find_choice_record(observation, telemetry)
        if self.active_interaction_key is not None:
            interaction = self.interactables.get(self.active_interaction_key)
            if interaction is not None:
                previous_map_state = (
                    int(interaction.get("choice_menus", 0)),
                    interaction.get("classification"),
                    interaction.get("usefulness"),
                )
                interaction["choice_menus"] = max(
                    1,
                    int(interaction.get("choice_menus", 0)),
                )
                interaction["classification"] = "confirmed_npc"
                interaction["usefulness"] = "choice_pending"
                current_map_state = (
                    int(interaction.get("choice_menus", 0)),
                    interaction.get("classification"),
                    interaction.get("usefulness"),
                )
                if current_map_state != previous_map_state:
                    key = self.active_interaction_key
                    self.map_updates.append(
                        {
                            "type": "interaction_outcome",
                            "room": key[0],
                            "cell": [key[1], key[2]],
                            "choice_menus": int(
                                interaction.get("choice_menus", 0)
                            ),
                            "classification": interaction.get(
                                "classification"
                            ),
                            "usefulness": interaction.get("usefulness"),
                            "last_outcome": interaction.get("last_outcome"),
                            "outcome_counts": interaction.get(
                                "outcome_counts", {}
                            ),
                        }
                    )
        if (
            self.pending_choice_record is record
            and self.pending_choice_pattern is not None
            and self.pending_choice_epoch == self.story_epoch
        ):
            failures = self._choice_counts(record, "failures")
            failures[self.pending_choice_pattern] += 1
        successful = record.get("successful_pattern")
        attempts = self._choice_counts(record, "attempts")
        failures = self._choice_counts(record, "failures")
        if isinstance(successful, int) and 0 <= successful < len(CHOICE_PATTERNS):
            pattern_index = successful
        else:
            pattern_index = min(
                range(len(CHOICE_PATTERNS)),
                key=lambda index: (attempts[index] + failures[index], attempts[index], index),
            )
        attempts[pattern_index] += 1
        self.choice_session_trials += 1
        self.active_choice_record = record
        self.pending_choice_record = record
        self.pending_choice_pattern = pattern_index
        self.pending_choice_epoch = self.story_epoch
        self.menu_action_queue = deque(
            (*CHOICE_RESET, *CHOICE_PATTERNS[pattern_index], "confirm")
        )

    def _choose_menu_action(
        self,
        observation: Observation,
        telemetry: TelemetrySample | None,
        menu_started: bool,
    ) -> Action:
        if menu_started:
            self.choice_settle_steps = 0
            self.choice_session_trials = 0
            self._start_choice_trial(observation, telemetry)
        elif not self.menu_action_queue:
            # A confirm can take a few frames to close the menu. Starting a
            # second trial immediately would count the same response as a
            # failure and may send another confirm to the NPC. Let the game
            # settle first; a real re-entry will be announced by menu_started.
            if (
                self.active_choice_record is not None
                and self.choice_settle_steps < CHOICE_CONFIRM_SETTLE_STEPS
            ):
                self.choice_settle_steps += 1
                return self._select(
                    "wait",
                    "wait for choice result to settle",
                    telemetry,
                )
            self.choice_settle_steps = 0
            successful = (
                self.active_choice_record.get("successful_pattern")
                if self.active_choice_record is not None
                else None
            )
            session_limit = (
                1
                if isinstance(successful, int)
                else CHOICE_REENGAGEMENT_LIMIT
            )
            if self.choice_session_trials >= session_limit:
                return self._select(
                    "wait",
                    "choice patterns exhausted; wait for menu state to change",
                    telemetry,
                )
            self._start_choice_trial(observation, telemetry)
        action = self.menu_action_queue.popleft()
        pattern = self.pending_choice_pattern or 0
        phase = "confirm selection" if action == "confirm" else "move selection"
        return self._select(
            action,
            f"choice trial {pattern + 1}: {phase} {action}",
            telemetry,
        )

    def _observe_story_state(
        self,
        state: GameState,
        telemetry: TelemetrySample | None,
        interaction_was_pending: bool,
    ) -> None:
        """Learn which visible actions have consequences beyond flavor text."""
        if self.active_interaction_key is not None:
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
            # Save/configuration menus use the same telemetry mode as story
            # choices.  Once such a menu simply returns to play, retire that
            # trial so a later unrelated room change cannot be credited to it.
            self._mark_pending_choice_failed(
                "menu closed without observed story progress",
                prioritize_retry=False,
            )

        sequence_started = self.previous_state not in {
            GameState.DIALOGUE,
            GameState.CUTSCENE,
        }
        if (
            state is GameState.CUTSCENE
            and sequence_started
            and self.active_interaction_key is None
        ):
            self._record_story_progress("automatic scripted sequence", telemetry)
        elif (
            state is GameState.DIALOGUE
            and sequence_started
            and not interaction_was_pending
            and self.active_interaction_key is None
        ):
            self._record_story_progress("automatic dialogue", telemetry)
        self.previous_state = state

    def _record_story_progress(
        self,
        event: str,
        telemetry: TelemetrySample | None,
    ) -> None:
        if self.pending_choice_record is not None and self.pending_choice_pattern is not None:
            completed_record = self.pending_choice_record
            successes = self._choice_counts(completed_record, "successes")
            successes[self.pending_choice_pattern] += 1
            completed_record["successful_pattern"] = self.pending_choice_pattern
            self.map_updates.append(
                {
                    "type": "choice_outcome",
                    "room": completed_record.get("room"),
                    "pattern": self.pending_choice_pattern + 1,
                    "successful": True,
                    "event": event,
                }
            )
            self.pending_choice_record = None
            self.pending_choice_pattern = None
            if self.active_choice_record is completed_record:
                self.active_choice_record = None
                self.menu_action_queue.clear()
                self.choice_settle_steps = 0
                self.choice_session_trials = 0
        self.story_epoch += 1
        self.story_progress_events += 1
        self.story_stall_steps = 0
        update: dict[str, object] = {
            "type": "story_progress",
            "event": event,
            "epoch": self.story_epoch,
        }
        if telemetry is not None:
            update["room"] = self._room_key(telemetry)
            update["cell"] = list(self._cell(telemetry))
        self.map_updates.append(update)

    def _mark_pending_choice_failed(
        self,
        event: str,
        *,
        prioritize_retry: bool = True,
    ) -> None:
        if self.pending_choice_record is None or self.pending_choice_pattern is None:
            return
        failed_record = self.pending_choice_record
        failures = self._choice_counts(failed_record, "failures")
        failures[self.pending_choice_pattern] += 1
        self.map_updates.append(
            {
                "type": "choice_outcome",
                "room": failed_record.get("room"),
                "pattern": self.pending_choice_pattern + 1,
                "successful": False,
                "event": event,
            }
        )
        self.pending_choice_record = None
        self.pending_choice_pattern = None
        if self.active_choice_record is failed_record:
            self.active_choice_record = None
            self.menu_action_queue.clear()
            self.choice_settle_steps = 0
            self.choice_session_trials = 0
        if prioritize_retry:
            # Keep the NPC as the immediate objective instead of resuming
            # general exploration after an option had no observed consequence.
            self.story_stall_steps = max(
                self.story_stall_steps,
                STORY_SEARCH_STEPS,
            )

    @staticmethod
    def _remember_interaction_outcome(
        record: dict[str, object],
        outcome: str,
        usefulness: str,
    ) -> None:
        counts = record.get("outcome_counts")
        if not isinstance(counts, dict):
            counts = {}
        counts[outcome] = int(counts.get(outcome, 0)) + 1
        record["outcome_counts"] = counts
        record["last_outcome"] = outcome
        record["usefulness"] = usefulness

    def _finish_active_interaction(
        self,
        telemetry: TelemetrySample | None,
    ) -> None:
        key = self.active_interaction_key
        if key is None:
            return
        record = self.interactables.get(key)
        if record is not None:
            record["dialogue_steps"] = int(record.get("dialogue_steps", 0)) + int(
                self.active_interaction_dialogue_steps
            )
            record["cutscene_steps"] = int(record.get("cutscene_steps", 0)) + int(
                self.active_interaction_cutscene_steps
            )
            changed_room = bool(
                telemetry is not None and self._room_key(telemetry) != key[0]
            )
            meaningful = (
                self.active_interaction_cutscene_steps > 0
                or self.active_interaction_saw_battle
                or changed_room
            )
            if meaningful:
                record["progressions"] = int(record.get("progressions", 0)) + 1
                if self.active_interaction_saw_battle:
                    outcome = "battle_started"
                    progress_event = "interaction started a battle"
                elif self.active_interaction_cutscene_steps:
                    outcome = "scripted_sequence"
                    progress_event = "interaction caused a scripted sequence"
                else:
                    outcome = "room_change"
                    progress_event = "interaction changed rooms"
                self._remember_interaction_outcome(
                    record,
                    outcome,
                    "progress",
                )
                self._record_story_progress(progress_event, telemetry)
            elif int(record.get("choice_menus", 0)) > 0:
                self._remember_interaction_outcome(
                    record,
                    "choice_without_progress",
                    "choice_pending",
                )
                self._mark_pending_choice_failed(
                    "returned to overworld without observed story progress"
                )
            else:
                self._remember_interaction_outcome(
                    record,
                    "ordinary_dialogue",
                    "flavor",
                )
            if int(record.get("choice_menus", 0)) > 0:
                record["classification"] = "confirmed_npc"
            elif meaningful:
                record["classification"] = "story_interaction"
            else:
                record["classification"] = "tested_nonchoice"
            self.map_updates.append(
                {
                    "type": "interaction_outcome",
                    "room": key[0],
                    "cell": [key[1], key[2]],
                    "dialogue_steps": int(record.get("dialogue_steps", 0)),
                    "cutscene_steps": int(record.get("cutscene_steps", 0)),
                    "progressions": int(record.get("progressions", 0)),
                    "meaningful": meaningful,
                    "choice_menus": int(record.get("choice_menus", 0)),
                    "classification": record.get("classification"),
                    "usefulness": record.get("usefulness"),
                    "last_outcome": record.get("last_outcome"),
                    "outcome_counts": record.get("outcome_counts", {}),
                }
            )
        self.active_interaction_key = None
        self.active_choice_record = None
        self.menu_action_queue.clear()
        self.active_interaction_dialogue_steps = 0
        self.active_interaction_cutscene_steps = 0
        self.active_interaction_saw_battle = False
        self.choice_session_trials = 0

    @staticmethod
    def _room_key(telemetry: TelemetrySample) -> str:
        return telemetry.room_name or str(telemetry.room_id)

    @staticmethod
    def _cell(telemetry: TelemetrySample) -> tuple[int, int]:
        return int(telemetry.x // CELL_SIZE), int(telemetry.y // CELL_SIZE)

    @staticmethod
    def _region(cell: tuple[int, int]) -> tuple[int, int]:
        return (
            cell[0] // EXPLORATION_REGION_CELLS,
            cell[1] // EXPLORATION_REGION_CELLS,
        )

    def _observe_room(self, telemetry: TelemetrySample) -> None:
        room = self._room_key(telemetry)
        cell = self._cell(telemetry)
        if not self.recent_rooms:
            self.recent_rooms.append(self.observed_room or room)
        if self.observed_room is not None and room != self.observed_room:
            source_room = self.observed_room
            first_visit = not any(
                seen_room == room for seen_room, _x, _y in self.seen_cells
            )
            self.transitions[(source_room, room)] += 1
            self.room_entry_from[room] = source_room
            if self.recent_rooms and self.recent_rooms[-1] == room:
                self.suppressed_room_links.add(frozenset((source_room, room)))
            self.exit_search_goal = None
            self.visual_goal = None
            self.visual_goal_age = 0
            self.visual_goal_stalls = 0
            self.visual_goal_best_distance = None
            if not self.recent_rooms or self.recent_rooms[-1] != room:
                self.recent_rooms.append(room)
            if (
                len(self.recent_rooms) >= 3
                and self.recent_rooms[-3] == self.recent_rooms[-1]
            ):
                self.suppressed_room_links.add(frozenset((source_room, room)))
            if self.recent_rooms and len(self.recent_rooms) >= 2:
                previous_room = self.recent_rooms[-2]
                if previous_room == room:
                    self.suppressed_room_links.add(frozenset((source_room, room)))
            if self.observed_cell is not None:
                action = (
                    self.last_movement
                    or self.last_overworld_movement
                    or "event"
                )
                warp = (
                    source_room,
                    *self.observed_cell,
                    action,
                    room,
                    *cell,
                )
                self.warps[warp] += 1
                self.map_updates.append(
                    {
                        "type": "warp",
                        "from_room": source_room,
                        "from_cell": list(self.observed_cell),
                        "action": action,
                        "to_room": room,
                        "to_cell": list(cell),
                        "count": self.warps[warp],
                    }
                )
            self.last_overworld_movement = None
            if first_visit:
                self._record_story_progress("discovered a new room", telemetry)
        self.observed_room = room
        self.observed_cell = cell

    def observe_room_trace(self, samples: list[TelemetrySample]) -> None:
        """Observe every ordered room packet, including multiple warps per step."""
        for sample in samples:
            self._observe_room(sample)

    def _observe_screen(
        self,
        observation: Observation,
        telemetry: TelemetrySample,
    ) -> None:
        room = self._room_key(telemetry)
        coordinates = visible_region_coordinates(
            telemetry.camera_x,
            telemetry.camera_y,
            telemetry.camera_width,
            telemetry.camera_height,
            telemetry.room_width,
            telemetry.room_height,
        )
        self.current_visible_regions = {
            (room, region_x, region_y) for region_x, region_y in coordinates
        }
        if (
            not observation.visual_valid
            or not coordinates
            or observation.step % SCREEN_ANALYSIS_INTERVAL
        ):
            return
        if self.room_view is not None:
            self.map_updates.extend(
                tile.as_map_update()
                for tile in self.room_view.capture(
                    observation.frame,
                    telemetry,
                    observation.step,
                )
            )
        for visual in analyze_screen_regions(observation.frame, telemetry):
            key = (room, visual.region_x, visual.region_y)
            existing = self.screen_regions.get(key)
            previous_interest = (
                float(existing.get("interest", 0.0)) if existing else 0.0
            )
            previous_hypothesis = existing.get("hypothesis") if existing else None
            record = dict(existing or {})
            record["views"] = int(record.get("views", 0)) + 1
            entity_evidence, obstruction_targets = self._region_obstruction_evidence(
                room,
                (visual.region_x, visual.region_y),
            )
            record["entity_approach_directions"] = entity_evidence
            record["obstruction_target_cells"] = obstruction_targets
            record["walkable_evidence"] = self._region_has_walkable_evidence(
                room,
                (visual.region_x, visual.region_y),
            )
            record["last_signature"] = visual.appearance_signature
            record["last_interest"] = visual.interest
            record["interest"] = max(previous_interest, visual.interest)
            record.setdefault("inspections", 0)
            if (
                previous_hypothesis == "possible_exit"
                and visual.hypothesis is None
                and record.get("walkable_evidence", False)
            ):
                record["guess_misses"] = int(record.get("guess_misses", 0)) + 1
            elif visual.hypothesis is not None:
                record["guess_misses"] = 0
            else:
                record.setdefault("guess_misses", 0)
            retire_exit_guess = (
                previous_hypothesis == "possible_exit"
                and int(record.get("guess_misses", 0)) >= 3
                and record.get("walkable_evidence", False)
            )
            accept_hypothesis = bool(visual.hypothesis) and (
                previous_hypothesis is None or visual.interest > previous_interest
            )
            if accept_hypothesis and previous_hypothesis is None:
                active = [
                    (active_key, active_record)
                    for active_key, active_record in self.screen_regions.items()
                    if active_key[0] == room
                    and active_record.get("hypothesis")
                    and int(active_record.get("inspections", 0)) < 2
                ]
                if len(active) >= MAX_ROOM_VISUAL_HYPOTHESES:
                    weakest_key, weakest = min(
                        active,
                        key=lambda item: float(item[1].get("interest", 0.0)),
                    )
                    if visual.interest > float(weakest.get("interest", 0.0)) + 0.05:
                        weakest["inspections"] = 2
                        self.map_updates.append(
                            {
                                "type": "screen_region",
                                "room": weakest_key[0],
                                "region": [weakest_key[1], weakest_key[2]],
                                "views": int(weakest.get("views", 1)),
                                "interest": round(
                                    float(weakest.get("interest", 0.0)), 3
                                ),
                                "hypothesis": weakest.get("hypothesis"),
                                "inspections": 2,
                            }
                        )
                    else:
                        accept_hypothesis = False
            if retire_exit_guess:
                # Repeatedly seeing ordinary walkable pixels is evidence
                # against an exit guess. Retire it so the planner can test a
                # different lead instead of revisiting the same scenery.
                record["hypothesis"] = None
                record["inspections"] = max(2, int(record.get("inspections", 0)))
            elif accept_hypothesis:
                record["hypothesis"] = visual.hypothesis
            else:
                record.setdefault("hypothesis", previous_hypothesis)
            strong_character_shape = (
                entity_evidence >= CHARACTER_APPROACH_DIRECTIONS
                and obstruction_targets <= 4
                and visual.interest >= 0.06
            )
            compact_single_approach = (
                entity_evidence >= 1
                and obstruction_targets <= CHARACTER_SINGLE_APPROACH_MAX_TARGETS
                and int(record.get("views", 0)) >= CHARACTER_SINGLE_APPROACH_MIN_VIEWS
                and visual.interest >= CHARACTER_SINGLE_APPROACH_MIN_INTEREST
                and record.get("hypothesis") != "possible_exit"
            )
            if strong_character_shape or compact_single_approach:
                record["hypothesis"] = "possible_character"
                record["character_probe_version"] = CHARACTER_PROBE_VERSION
            elif (
                record.get("hypothesis") == "possible_character"
                and not strong_character_shape
                and not compact_single_approach
            ):
                record["hypothesis"] = None
            self.screen_regions[key] = record
            if (
                existing is None
                or record.get("hypothesis") != previous_hypothesis
                or float(record["interest"]) >= previous_interest + 0.05
            ):
                self.map_updates.append(
                    {
                        "type": "screen_region",
                        "room": room,
                        "region": [visual.region_x, visual.region_y],
                        "views": int(record["views"]),
                        "interest": round(float(record["interest"]), 3),
                        "hypothesis": record.get("hypothesis"),
                        "inspections": int(record.get("inspections", 0)),
                        "entity_approach_directions": entity_evidence,
                        "obstruction_target_cells": obstruction_targets,
                        "guess_misses": int(record.get("guess_misses", 0)),
                    }
                )

    def _region_has_walkable_evidence(
        self,
        room: str,
        region: tuple[int, int],
    ) -> bool:
        return any(
            seen_room == room and self._region((seen_x, seen_y)) == region
            for seen_room, seen_x, seen_y in self.seen_cells
        )

    def _region_obstruction_evidence(
        self,
        room: str,
        region: tuple[int, int],
    ) -> tuple[int, int]:
        if not self._region_has_walkable_evidence(room, region):
            return 0, 0
        approaches: list[tuple[tuple[int, int], str]] = []
        for (edge_room, source_x, source_y, direction), failures in self.blocked.items():
            if edge_room != room or failures <= 0 or direction not in DIRECTION_VECTORS:
                continue
            dx, dy = DIRECTION_VECTORS[direction]
            target = (source_x + dx, source_y + dy)
            if self._region(target) == region:
                approaches.append((target, direction))
        best_directions = 0
        best_targets = 0
        for target, _direction in approaches:
            nearby = [
                (nearby_target, direction)
                for nearby_target, direction in approaches
                if max(
                    abs(nearby_target[0] - target[0]),
                    abs(nearby_target[1] - target[1]),
                )
                <= 2
            ]
            directions = {direction for _nearby_target, direction in nearby}
            target_count = len({nearby_target for nearby_target, _direction in nearby})
            if (len(directions), -target_count) > (best_directions, -best_targets):
                best_directions = len(directions)
                best_targets = target_count
        return best_directions, best_targets

    def _region_character_approach_count(
        self,
        room: str,
        region: tuple[int, int],
    ) -> int:
        return self._region_obstruction_evidence(room, region)[0]

    def _region_has_character_topology(
        self,
        room: str,
        region: tuple[int, int],
    ) -> bool:
        directions, targets = self._region_obstruction_evidence(room, region)
        if directions >= CHARACTER_APPROACH_DIRECTIONS and targets <= 4:
            return True
        record = self.screen_regions.get((room, *region), {})
        return (
            directions >= 1
            and targets <= CHARACTER_SINGLE_APPROACH_MAX_TARGETS
            and int(record.get("views", 0)) >= CHARACTER_SINGLE_APPROACH_MIN_VIEWS
            and float(record.get("interest", 0.0))
            >= CHARACTER_SINGLE_APPROACH_MIN_INTEREST
            and record.get("hypothesis") != "possible_exit"
        )

    def _learn_movement_result(self, telemetry: TelemetrySample | None) -> None:
        if not telemetry or not self.last_movement or self.last_position is None:
            return
        room = self._room_key(telemetry)
        if room != self.last_room:
            self.failed_movement = False
            self._reset_collision_evidence()
            self.interaction_tried = False
            self.pending_blocked_direction = None
            self.direction_steps = 0
            return
        if (
            self.last_movement_sample_at is not None
            and telemetry.received_at <= self.last_movement_sample_at
        ):
            self.awaiting_fresh_telemetry = True
            self.failed_movement = False
            self.collision_pending = False
            return
        self.awaiting_fresh_telemetry = False
        delta_x = telemetry.x - self.last_position[0]
        delta_y = telemetry.y - self.last_position[1]
        distance = hypot(delta_x, delta_y)
        stationary = distance < 0.75
        self.input_not_registered = (
            stationary
            and telemetry.facing_direction is not None
            and telemetry.facing_direction != self.last_movement
        )
        if self.input_not_registered:
            self.failed_movement = False
            self.collision_pending = False
            self.stationary_streak = 0
            self.stationary_key = None
            self.unregistered_input_streak += 1
        elif stationary:
            key = (
                room,
                round(telemetry.x),
                round(telemetry.y),
                self.last_movement,
            )
            if key == self.stationary_key:
                self.stationary_streak += 1
            else:
                self.stationary_key = key
                self.stationary_streak = 1
            self.failed_movement = self.stationary_streak >= COLLISION_CONFIRM_SAMPLES
            self.collision_pending = not self.failed_movement
            self.unregistered_input_streak = 0
        else:
            self.failed_movement = False
            self.collision_pending = False
            self.stationary_streak = 0
            self.stationary_key = None
        if not self.failed_movement:
            current_cell = self._cell(telemetry)
            vector_x, vector_y = DIRECTION_VECTORS[self.last_movement]
            forward = delta_x * vector_x + delta_y * vector_y
            lateral = abs(delta_x * vector_y - delta_y * vector_x)
            aligned_with_action = forward > 0.75 and lateral <= max(2.0, forward * 0.35)
            self.unexpected_displacement = distance >= 0.75 and not aligned_with_action
            if (
                aligned_with_action
                and self.last_cell is not None
                and current_cell != self.last_cell
            ):
                self._remember_open_path(
                    room,
                    self.last_cell,
                    self.last_movement,
                    current_cell,
                )
            if aligned_with_action:
                self._forget_contradicted_block(room, self.last_cell, self.last_movement)
                self.unregistered_input_streak = 0
                self.interaction_tried = False
                self.pending_blocked_direction = None

    def _remember_open_path(
        self,
        room: str,
        source: tuple[int, int],
        direction: str,
        target: tuple[int, int],
    ) -> None:
        """Record packet-gap movement as exact adjacent cardinal edges."""
        vector_x, vector_y = DIRECTION_VECTORS[direction]
        delta_x = target[0] - source[0]
        delta_y = target[1] - source[1]
        forward = delta_x * vector_x + delta_y * vector_y
        lateral = abs(delta_x * vector_y - delta_y * vector_x)
        if forward <= 0 or lateral != 0:
            return
        current = source
        for _ in range(forward):
            following = (current[0] + vector_x, current[1] + vector_y)
            edge = (room, *current, direction, *following)
            reverse = (room, *following, OPPOSITE[direction], *current)
            if edge not in self.open_edges:
                self.map_updates.append(
                    {
                        "type": "open_edge",
                        "room": room,
                        "from_cell": list(current),
                        "to_cell": list(following),
                    }
                )
            self.open_edges.add(edge)
            self.open_edges.add(reverse)
            current_region = self._region(current)
            following_region = self._region(following)
            self.tried_regions.add((room, *current_region, direction))
            self.tried_regions.add(
                (room, *following_region, OPPOSITE[direction])
            )
            current = following

    def _reset_collision_evidence(self) -> None:
        self.collision_pending = False
        self.stationary_streak = 0
        self.stationary_key = None
        self.awaiting_fresh_telemetry = False
        self.last_movement_sample_at = None

    def _forget_contradicted_block(
        self,
        room: str,
        cell: tuple[int, int] | None,
        direction: str,
    ) -> None:
        if cell is None:
            return
        edge = (room, *cell, direction)
        if self.blocked.pop(edge, None) is not None:
            self.blocked_zones.discard(edge)
            self.map_updates.append(
                {"type": "unblocked", "room": room, "cell": list(cell), "direction": direction}
            )

    def _suspend_movement_learning(self) -> None:
        # Dialogue, menus, cutscenes, and battles can freeze player coordinates.
        # Never interpret those freezes as collisions.
        self.last_movement = None
        self.last_position = None
        self.failed_movement = False
        self._reset_collision_evidence()
        self.input_not_registered = False
        self.unregistered_input_streak = 0
        self.unexpected_displacement = False
        self.interaction_tried = False
        self.pending_blocked_direction = None

    def _complete_pending_interaction(self) -> None:
        if self.interaction_candidate is None:
            return
        (
            room,
            cell_x,
            cell_y,
            direction,
            instance_id,
            name,
            target_x,
            target_y,
        ) = self.interaction_candidate
        self.interacted_zones.add((room, cell_x, cell_y))
        if instance_id is not None:
            self.interacted_instances.add((room, instance_id))
        key = self._matching_interactable(room, (target_x, target_y), instance_id)
        if key is None:
            key = (room, target_x, target_y)
        self.interacted_targets.add(key)
        record = self.interactables.get(key, {})
        approaches = list(record.get("approaches", []))
        approach = {"x": cell_x, "y": cell_y, "direction": direction}
        if approach not in approaches:
            approaches.append(approach)
        record.update(
            {
                "name": name or record.get("name") or "interaction",
                "instance_id": instance_id if instance_id is not None else record.get("instance_id"),
                "confirmations": int(record.get("confirmations", 0)) + 1,
                "attempts": int(record.get("attempts", 0)) + 1,
                "dialogue_steps": int(record.get("dialogue_steps", 0)),
                "cutscene_steps": int(record.get("cutscene_steps", 0)),
                "progressions": int(record.get("progressions", 0)),
                "last_story_epoch": self.story_epoch,
                "choice_menus": int(record.get("choice_menus", 0)),
                "classification": str(record.get("classification") or "unknown"),
                "usefulness": str(record.get("usefulness") or "unknown"),
                "last_outcome": str(record.get("last_outcome") or "unknown"),
                "outcome_counts": dict(record.get("outcome_counts", {}))
                if isinstance(record.get("outcome_counts"), dict)
                else {},
                "approaches": approaches,
            }
        )
        self.interactables[key] = record
        self.map_updates.append(
            {
                "type": "interactable",
                "room": room,
                "cell": [key[1], key[2]],
                "name": record["name"],
                "instance_id": record["instance_id"],
                "confirmations": record["confirmations"],
                "choice_menus": int(record.get("choice_menus", 0)),
                "classification": record.get("classification"),
                "usefulness": record.get("usefulness"),
                "last_outcome": record.get("last_outcome"),
                "outcome_counts": record.get("outcome_counts", {}),
                "approaches": approaches,
                "status": "confirmed",
            }
        )
        self.completed_interaction = (room, cell_x, cell_y, direction)
        self.active_interaction_key = key
        self.active_interaction_dialogue_steps = 0
        self.active_interaction_cutscene_steps = 0
        self.active_interaction_saw_battle = False
        self.interaction_candidate = None

    def _matching_interactable(
        self,
        room: str,
        target: tuple[int, int],
        instance_id: int | None,
    ) -> tuple[str, int, int] | None:
        if instance_id is not None:
            for key, record in self.interactables.items():
                if key[0] == room and record.get("instance_id") == instance_id:
                    return key
            # When telemetry exposes stable instance IDs, nearby objects remain
            # distinct even if their collision boxes are close together.
            return None
        nearby = [
            key
            for key in self.interactables
            if key[0] == room
            and max(abs(key[1] - target[0]), abs(key[2] - target[1]))
            <= INTERACTION_MEMORY_RADIUS
        ]
        if not nearby:
            return None
        return min(
            nearby,
            key=lambda key: abs(key[1] - target[0]) + abs(key[2] - target[1]),
        )

    @staticmethod
    def _interaction_target(
        cell: tuple[int, int],
        direction: str,
    ) -> tuple[int, int]:
        dx, dy = DIRECTION_VECTORS[direction]
        return cell[0] + dx, cell[1] + dy

    def _blocked_near(self, room: str, cell: tuple[int, int], direction: str) -> bool:
        # A nearby wall can contain a one-cell doorway. Only the exact attempted
        # edge is treated as blocked; successful movement can still erase it.
        return self.blocked[(room, *cell, direction)] > 0

    def _interacted_near(
        self, room: str, cell: tuple[int, int], direction: str
    ) -> bool:
        dx, dy = DIRECTION_VECTORS[direction]
        target = (cell[0] + dx, cell[1] + dy)
        nearby = [
            (target_room, target_x, target_y)
            for target_room, target_x, target_y in self.interacted_targets
            if target_room == room
            and max(abs(target_x - target[0]), abs(target_y - target[1]))
            <= INTERACTION_MEMORY_RADIUS
        ]
        if not nearby:
            return False
        return not any(self._story_interaction_retryable(key) for key in nearby)

    def _story_interaction_retryable(self, key: tuple[str, int, int]) -> bool:
        interaction = self.interactables.get(key)
        if interaction is None:
            return False
        if int(interaction.get("progressions", 0)) > 0:
            return False
        usefulness = str(interaction.get("usefulness") or "unknown")
        if usefulness == "flavor" and int(interaction.get("choice_menus", 0)) <= 0:
            return False
        if int(interaction.get("choice_menus", 0)) <= 0:
            # Older runs labeled obj_writer response prompts as ordinary
            # dialogue. Give only long, non-progressing conversations one
            # migration retry so the visible option detector can inspect them.
            return (
                int(interaction.get("dialogue_steps", 0))
                >= LEGACY_CHOICE_DIALOGUE_STEPS
                and int(interaction.get("attempts", 0)) < 2
            )
        matches = [
            record
            for record in self.choice_trials
            if record.get("room") == key[0]
            and max(
                abs(int(record.get("context_x", -99)) - key[1]),
                abs(int(record.get("context_y", -99)) - key[2]),
            )
            <= 2
            and record.get("successful_pattern") is None
        ]
        return any(
            sum(self._choice_counts(record, "attempts"))
            < CHOICE_REENGAGEMENT_LIMIT
            for record in matches
        )

    def _route_to_retryable_story_interaction(
        self,
        room: str,
        start: tuple[int, int],
    ) -> tuple[str, tuple[str, int, int]] | None:
        adjacency = self._adjacency(room)
        routes: list[
            tuple[
                tuple[int, int, int, int, int],
                str,
                tuple[str, int, int],
            ]
        ] = []
        for key, interaction in self.interactables.items():
            if key[0] != room or not self._story_interaction_retryable(key):
                continue
            for approach in interaction.get("approaches", []):
                if not isinstance(approach, dict):
                    continue
                approach_direction = str(approach.get("direction") or "")
                if approach_direction not in DIRECTION_VECTORS:
                    continue
                source = (int(approach.get("x", -1)), int(approach.get("y", -1)))
                if start == source:
                    first_direction = approach_direction
                    distance = 0
                else:
                    route = self._route_to_target(adjacency, start, source)
                    if route is None:
                        continue
                    first_direction, distance = route
                routes.append(
                    (
                        (
                            STORY_USEFULNESS_RANK.get(
                                str(interaction.get("usefulness") or "unknown"),
                                STORY_USEFULNESS_RANK["unknown"],
                            ),
                            int(interaction.get("progressions", 0)),
                            int(interaction.get("attempts", 0)),
                            distance,
                            self._recent_cell_cost(room, source),
                        ),
                        first_direction,
                        key,
                    )
                )
        if not routes:
            return None
        _score, direction, key = min(routes, key=lambda item: item[0])
        goal = (room, *self._region((key[1], key[2])))
        record = self.screen_regions.setdefault(
            goal,
            {"views": 1, "interest": 0.0, "inspections": 0},
        )
        record["hypothesis"] = "possible_character"
        record["choice_retry"] = True
        self.visual_goal = goal
        return direction, key

    def _interaction_probe_is_justified(
        self,
        room: str,
        cell: tuple[int, int],
        direction: str,
    ) -> bool:
        target_region = self._region(self._interaction_target(cell, direction))
        if self.visual_goal is not None and self.visual_goal[0] == room:
            record = self.screen_regions.get(self.visual_goal, {})
            if (
                target_region == (self.visual_goal[1], self.visual_goal[2])
                and record.get("hypothesis") in {
                    "possible_character",
                    "possible_exit",
                }
            ):
                return True
        return self.exit_search_goal == (room, *cell, direction)

    def _remember_blocked(
        self, room: str, cell: tuple[int, int], direction: str
    ) -> None:
        self.blocked[(room, *cell, direction)] += 1
        self.blocked_zones.add((room, *cell, direction))
        self.map_updates.append(
            {
                "type": "blocked",
                "room": room,
                "cell": list(cell),
                "direction": direction,
                "failures": self.blocked[(room, *cell, direction)],
            }
        )

    def _explore(self, telemetry: TelemetrySample) -> Action:
        self.navigation_tick += 1
        self.story_stall_steps += 1
        room = self._room_key(telemetry)
        cell = self._cell(telemetry)
        self.visits[(room, *cell)] += 1
        self.seen_cells.add((room, *cell))
        region_key = (room, *self._region(cell))
        if region_key in self.seen_regions:
            self.steps_without_frontier += 1
        else:
            self.seen_regions.add(region_key)
            self.steps_without_frontier = 0
        recent = (room, *cell)
        if not self.recent_cells or self.recent_cells[-1] != recent:
            self.recent_cells.append(recent)

        if room != self.last_room:
            self.direction_steps = 0
            self.failed_movement = False
            self._reset_collision_evidence()
            self.input_not_registered = False
            self.unregistered_input_streak = 0
            self.unexpected_displacement = False
            self.interaction_tried = False
            self.pending_blocked_direction = None

        if self.completed_interaction is not None:
            interaction_room, cell_x, cell_y, old_direction = self.completed_interaction
            self.completed_interaction = None
            if interaction_room == room:
                # Some doors become passable after dialogue. Test forward once;
                # if it is still blocked, interacted_zones prevents a second Z.
                self.direction = old_direction
                self.direction_steps = 1
                return self._select(
                    self.direction,
                    f"interaction completed; test {self.direction} once",
                    telemetry,
                )

        if self.awaiting_fresh_telemetry and self.last_movement:
            direction = self.last_movement
            self.awaiting_fresh_telemetry = False
            return self._select(
                direction,
                f"await fresh telemetry before judging {direction}",
                telemetry,
            )

        if self.collision_pending and self.last_movement:
            direction = self.last_movement
            self.collision_pending = False
            return self._select(
                direction,
                f"checking blockage {direction} ({self.stationary_streak}/{COLLISION_CONFIRM_SAMPLES})",
                telemetry,
            )

        if self.input_not_registered and self.last_movement:
            old_direction = self.last_movement
            self.input_not_registered = False
            if self.unregistered_input_streak <= 1:
                self.direction = old_direction
                self.direction_steps = 1
                return self._select(
                    self.direction,
                    f"input not reflected; retry {self.direction} once",
                    telemetry,
                )
            self.unregistered_input_streak = 0
            self.direction = self._least_visited_direction(
                room, cell, old_direction, avoid={old_direction}
            )
            self.direction_steps = 1
            return self._select(
                self.direction,
                f"input remained frozen; switch from {old_direction} to {self.direction}",
                telemetry,
            )

        if self.unexpected_displacement:
            # Replan at the observed location without attributing manual movement,
            # knockback, or another external displacement to the attempted key.
            self.unexpected_displacement = False
            self.direction_steps = 0

        if self.interaction_tried and self.pending_blocked_direction:
            blocked_cell = self.last_cell or cell
            old_direction = self.pending_blocked_direction
            self._remember_failed_character_probe()
            self._remember_blocked(room, blocked_cell, old_direction)
            self.interaction_candidate = None
            self.direction = self._least_visited_direction(
                room, cell, old_direction, avoid={old_direction}
            )
            self.direction_steps = 1
            self.failed_movement = False
            self.interaction_tried = False
            self.pending_blocked_direction = None
            return self._select(
                self.direction,
                f"learned obstacle {old_direction}; turn {self.direction}",
                telemetry,
            )

        if self.failed_movement and self.last_movement:
            if not self.interaction_tried:
                known_block = self._blocked_near(room, cell, self.last_movement)
                known_interaction = self._interacted_near(
                    room, cell, self.last_movement
                )
                justified_probe = self._interaction_probe_is_justified(
                    room,
                    cell,
                    self.last_movement,
                )
                if known_interaction or (known_block and not justified_probe):
                    old_direction = self.last_movement
                    self._remember_blocked(room, cell, old_direction)
                    self.direction = self._least_visited_direction(
                        room, cell, old_direction, avoid={old_direction}
                    )
                    self.direction_steps = 1
                    self.failed_movement = False
                    reason = (
                        f"completed interaction blocks {old_direction}"
                        if known_interaction
                        else f"known blocked {old_direction}"
                    )
                    return self._select(
                        self.direction,
                        f"{reason}; turn {self.direction}",
                        telemetry,
                    )
                if not justified_probe:
                    old_direction = self.last_movement
                    self._remember_blocked(room, cell, old_direction)
                    self._mark_interaction_cooldown(room, cell, old_direction)
                    self.direction = self._least_visited_direction(
                        room,
                        cell,
                        old_direction,
                        avoid={old_direction},
                    )
                    self.direction_steps = 1
                    self.failed_movement = False
                    return self._select(
                        self.direction,
                        f"unidentified obstacle {old_direction}; skip interaction and turn {self.direction}",
                        telemetry,
                    )
                target = self._interaction_target(cell, self.last_movement)
                if (
                    telemetry.facing_direction is not None
                    and telemetry.facing_direction != self.last_movement
                ):
                    return self._select(
                        self.last_movement,
                        f"align facing {self.last_movement} before interaction",
                        telemetry,
                    )
                self.interaction_tried = True
                self.pending_blocked_direction = self.last_movement
                self.interaction_candidate = (
                    room,
                    *cell,
                    self.last_movement,
                    None,
                    None,
                    *target,
                )
                return self._select(
                    "confirm",
                    f"blocked {self.last_movement}; try interaction",
                    telemetry,
                )

        if self.direction_steps <= 0:
            fallback_reason, fallback_direction = self._stalled_recovery(
                room, cell, self.direction
            )
            if fallback_direction is None:
                self.direction, self.direction_steps, reason = self._plan_exploration(
                    room, cell
                )
                stabilized, broke_loop = self._break_oscillation(
                    room, cell, self.direction
                )
                if broke_loop:
                    self.direction = stabilized
                    self.direction_steps = 1
                    reason = f"{self.loop_reason}; escape {self.direction}"
            else:
                self.direction = fallback_direction
                self.direction_steps = 1
                reason = fallback_reason
            self.decision_history.append((room, *cell, self.direction))
        else:
            reason = f"continue clear path {self.direction}"
        self.direction_steps -= 1
        return self._select(self.direction, reason, telemetry)

    def _stalled_recovery(
        self,
        room: str,
        cell: tuple[int, int],
        proposed: str,
    ) -> tuple[str, str | None]:
        if self.steps_without_frontier < WARP_SEEK_STEPS:
            return "", None

        self.stalled_recovery_steps += 1
        recent_directions = {
            direction for decision_room, _x, _y, direction in self.decision_history if decision_room == room
        }
        if proposed in recent_directions and self.stalled_recovery_steps >= 1:
            for direction in ("left", "right", "up", "down"):
                if direction == proposed or direction in recent_directions:
                    continue
                if self._blocked_near(room, cell, direction) or self._is_entry_warp_direction(room, cell, direction):
                    continue
                return f"stalled recovery; try {direction} to escape repetitive behavior", direction

        if len(recent_directions) <= 1 and self.stalled_recovery_steps >= 1:
            for direction in ("left", "right", "up", "down"):
                if direction == proposed or direction in recent_directions:
                    continue
                if self._blocked_near(room, cell, direction) or self._is_entry_warp_direction(room, cell, direction):
                    continue
                return f"stalled recovery; try {direction} to escape repetitive behavior", direction

        candidate_directions = [
            direction
            for direction in DIRECTION_VECTORS
            if direction != proposed
            and direction not in recent_directions
            and not self._blocked_near(room, cell, direction)
            and not self._is_entry_warp_direction(room, cell, direction)
        ]
        if candidate_directions:
            def score_direction(direction: str) -> tuple[int, int, str]:
                dx, dy = DIRECTION_VECTORS[direction]
                neighbor = self._known_open_neighbor(room, cell, direction)
                target = neighbor or (cell[0] + dx, cell[1] + dy)
                return (
                    self.visits[(room, *target)],
                    self._recent_cell_cost(room, target),
                    direction,
                )

            direction = min(candidate_directions, key=score_direction)
            return f"stalled recovery; diversify from {proposed} toward {direction}", direction

        return "", None

    def _progress_pressure(self, room: str, cell: tuple[int, int]) -> bool:
        if self.story_stall_steps >= STORY_SEARCH_STEPS:
            return True
        return self.steps_without_frontier >= WARP_SEEK_STEPS and not any(
            self._direction_is_unexplored(room, cell, direction)
            for direction in DIRECTION_VECTORS
        )

    def _plan_exploration(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> tuple[str, int, str]:
        loop_avoid = self._loop_avoid_directions(room, cell)
        frontier_route = self._route_to_nearest_frontier(
            room,
            cell,
            allowed_first=set(DIRECTION_VECTORS) - loop_avoid,
        )
        current_frontier = any(
            self._direction_is_unexplored(room, cell, direction)
            for direction in DIRECTION_VECTORS
        )
        story_focus = self._progress_pressure(room, cell)
        retry_route = (
            self._route_to_retryable_story_interaction(room, cell)
            if story_focus
            else None
        )
        if retry_route is not None:
            direction, target = retry_route
            return (
                direction,
                1,
                "story search: retry another response at learned interaction "
                f"({target[1]},{target[2]}) via {direction}",
            )
        character_plan = (
            self._direction_to_visual_hypothesis(
                room,
                cell,
                story_focus=True,
                allowed_hypotheses={"possible_character"},
            )
            if story_focus
            else None
        )
        if character_plan is not None:
            direction, _hypothesis, target_region = character_plan
            return (
                direction,
                1,
                "story search: approach remembered possible character "
                f"interaction side via {direction} near region {target_region}",
            )

        warp_route = self._route_to_learned_warp(room, cell)
        should_seek_warp = warp_route is not None and (
            self.steps_without_frontier >= WARP_SEEK_STEPS
            or (not current_frontier and frontier_route is None)
        )
        if should_seek_warp:
            direction, warp = warp_route
            return (
                direction,
                1,
                f"follow learned warp to {warp[4]} via {direction} "
                f"from ({warp[1]},{warp[2]})",
            )
        visual_plan = (
            self._direction_to_visual_hypothesis(room, cell)
            if not story_focus
            else None
        )
        if visual_plan is not None:
            direction, hypothesis, target_region = visual_plan
            readable_hypothesis = hypothesis.replace("_", " ")
            return (
                direction,
                2,
                f"investigate {readable_hypothesis} seen on screen: move "
                f"{direction} toward region {target_region}",
            )
        should_search_for_exit = (
            self.steps_without_frontier >= WARP_SEEK_STEPS
            or (not current_frontier and frontier_route is None)
        )
        exit_route = (
            self._route_to_possible_exit(room, cell)
            if should_search_for_exit
            else None
        )
        if exit_route is not None and exit_route[0] in loop_avoid:
            self.exit_search_goal = None
            exit_route = None
        if exit_route is not None:
            direction, probe = exit_route
            _probe_room, probe_x, probe_y, probe_direction = probe
            if cell == (probe_x, probe_y):
                self.exit_probes[probe] += 1
                return (
                    probe_direction,
                    EXIT_PROBE_COMMIT_STEPS,
                    (
                        "story search: " if story_focus else ""
                    )
                    + f"probe possible room exit {probe_direction} at learned "
                    f"map edge ({probe_x},{probe_y})",
                )
            return (
                direction,
                1,
                ("story search: " if story_focus else "")
                + f"search room edge: move {direction} toward possible "
                f"{probe_direction} exit at ({probe_x},{probe_y})",
            )
        if current_frontier:
            direction = self._least_visited_direction(room, cell, self.direction)
            return direction, MOVEMENT_COMMIT_STEPS, f"explore new edge {direction}"
        if frontier_route is not None:
            # Replan after one sample while following the graph. A longer held
            # commitment can pass straight through the intermediate frontier.
            return frontier_route, 1, f"route to mapped frontier {frontier_route}"
        if story_focus:
            visual_exit = self._direction_to_visual_hypothesis(
                room,
                cell,
                story_focus=True,
                allowed_hypotheses={"possible_exit"},
            )
            if visual_exit is not None:
                direction, _hypothesis, target_region = visual_exit
                return (
                    direction,
                    1,
                    "story search: inspect remembered visual passage "
                    f"via {direction} toward region {target_region}",
                )
        direction = self._least_visited_direction(room, cell, self.direction)
        return direction, 1, f"no reachable frontier; probe {direction}"

    def _direction_to_visual_hypothesis(
        self,
        room: str,
        cell: tuple[int, int],
        *,
        story_focus: bool = False,
        allowed_hypotheses: set[str] | None = None,
    ) -> tuple[str, str, tuple[int, int]] | None:
        allowed = allowed_hypotheses or {
            "possible_exit",
            "possible_character",
        }
        candidates = {
            key: record
            for key, record in self.screen_regions.items()
            if key[0] == room
            and record.get("hypothesis") in allowed
            and (
                key in self.current_visible_regions
                # A stationary character lead that was genuinely on screen can
                # be remembered after the camera moves, just as a player would
                # remember where someone was standing.
                or record.get("hypothesis") == "possible_character"
            )
            and int(record.get("inspections", 0))
            < (
                3
                if story_focus
                else 2
            )
            and not self._visual_hypothesis_is_confirmed(
                key,
                allow_story_retry=story_focus,
            )
        }
        if self.visual_goal not in candidates:
            self.visual_goal = None
        current_region = self._region(cell)
        if self.visual_goal is None and candidates:
            self.visual_goal = min(
                candidates,
                key=lambda key: (
                    self._visual_hypothesis_priority(
                        candidates[key],
                        key,
                        current_region,
                        story_focus,
                    ),
                ),
            )
            self.visual_goal_age = 0
            self.visual_goal_stalls = 0
            self.visual_goal_best_distance = None
        if self.visual_goal is None:
            return None

        goal = self.visual_goal
        record = candidates[goal]
        target_region = (goal[1], goal[2])
        distance = abs(target_region[0] - current_region[0]) + abs(
            target_region[1] - current_region[1]
        )
        self.visual_goal_age += 1
        if self.visual_goal_best_distance is None or distance < self.visual_goal_best_distance:
            self.visual_goal_best_distance = distance
            self.visual_goal_stalls = 0
        else:
            self.visual_goal_stalls += 1
        if (
            self.visual_goal_age > VISUAL_GOAL_AGE_LIMIT
            or self.visual_goal_stalls > VISUAL_GOAL_STALL_LIMIT
        ):
            self._finish_visual_goal()
            return None

        if record.get("hypothesis") == "possible_character":
            character_probe = self._route_to_character_probe(
                room,
                cell,
                target_region,
            )
            if character_probe is not None:
                return character_probe, "possible_character", target_region

        mapped_route = self._route_toward_visible_region(room, cell, target_region)
        readable_hypothesis = (
            "possible_character"
            if record.get("hypothesis") == "possible_character"
            else str(record["hypothesis"])
        )
        if mapped_route is not None:
            return mapped_route, readable_hypothesis, target_region

        if distance == 0:
            self._finish_visual_goal()
            return None

        delta_x = target_region[0] - current_region[0]
        delta_y = target_region[1] - current_region[1]
        horizontal = "right" if delta_x > 0 else "left"
        vertical = "down" if delta_y > 0 else "up"
        axes: list[str] = []
        if delta_x and delta_y:
            if abs(delta_x) > abs(delta_y):
                axes = [horizontal, vertical]
            elif abs(delta_y) > abs(delta_x):
                axes = [vertical, horizontal]
            else:
                axes = (
                    [self.direction, vertical if self.direction == horizontal else horizontal]
                    if self.direction in {horizontal, vertical}
                    else [horizontal, vertical]
                )
        elif delta_x:
            axes = [horizontal]
        elif delta_y:
            axes = [vertical]
        loop_avoid = self._loop_avoid_directions(room, cell)
        directions = [
            direction
            for direction in axes
            if direction not in loop_avoid
            and not self._blocked_near(room, cell, direction)
            and not self._is_entry_warp_direction(room, cell, direction)
        ]
        if not directions:
            self._finish_visual_goal()
            return None
        return (
            directions[0],
            readable_hypothesis,
            target_region,
        )

    def _visual_hypothesis_priority(
        self,
        record: dict[str, object],
        key: tuple[str, int, int],
        current_region: tuple[int, int],
        story_focus: bool,
    ) -> tuple[int, int, int, int, int, int, int, tuple[int, int]]:
        hypothesis = str(record.get("hypothesis") or "")
        interest = float(record.get("interest", 0.0))
        inspections = int(record.get("inspections", 0))
        views = int(record.get("views", 1))
        distance = abs(key[1] - current_region[0]) + abs(key[2] - current_region[1])
        type_rank = 0 if hypothesis == "possible_character" else 1 if hypothesis == "possible_exit" else 2
        if story_focus and hypothesis == "possible_character":
            type_rank -= 1
        if hypothesis == "possible_character":
            type_rank -= 1 if views >= 2 else 0
        if hypothesis == "possible_exit":
            type_rank += 1
        confidence_bias = 0 if interest >= 0.5 else 1 if interest >= 0.2 else 2
        if inspections >= 2:
            confidence_bias -= 1
        return (
            type_rank,
            confidence_bias,
            inspections,
            distance,
            -int(interest * 1000),
            -views,
            int(record.get("guess_misses", 0)),
            (key[1], key[2]),
        )

    def _route_to_character_probe(
        self,
        room: str,
        start: tuple[int, int],
        target_region: tuple[int, int],
    ) -> str | None:
        """Route to an observed collision side, then face the static candidate."""
        adjacency = self._adjacency(room)
        probes: list[tuple[tuple[int, int, int, int, int, str], str]] = []
        for (edge_room, source_x, source_y, direction), failures in self.blocked.items():
            if edge_room != room or failures <= 0 or direction not in DIRECTION_VECTORS:
                continue
            source = (source_x, source_y)
            probe = (room, source_x, source_y, direction)
            if self.character_probes[probe] > 0:
                continue
            target = self._interaction_target(source, direction)
            if self._region(target) != target_region:
                continue
            if self._interacted_near(room, source, direction):
                continue
            if start == source:
                first_direction = direction
                distance = 0
            else:
                route = self._route_to_target(adjacency, start, source)
                if route is None:
                    continue
                first_direction, distance = route
            probes.append(
                (
                    (
                        1 if failures >= LEGACY_CHARACTER_PROBE_FAILURES else 0,
                        distance,
                        failures,
                        self._recent_cell_cost(room, source),
                        source_y,
                        direction,
                    ),
                    first_direction,
                )
            )
        if not probes:
            return None
        return min(probes, key=lambda item: item[0])[1]

    def _mark_interaction_cooldown(
        self,
        room: str,
        cell: tuple[int, int],
        direction: str,
        cooldown_steps: int = 6,
    ) -> None:
        self.interaction_cooldowns[(room, *cell, direction)] = self.navigation_tick + cooldown_steps

    def _interaction_is_cooldown(
        self,
        room: str,
        cell: tuple[int, int],
        direction: str,
    ) -> bool:
        key = (room, *cell, direction)
        expires_at = self.interaction_cooldowns.get(key)
        if expires_at is None:
            return False
        if self.navigation_tick >= expires_at:
            self.interaction_cooldowns.pop(key, None)
            return False
        return True

    def _direction_target(
        self,
        room: str,
        cell: tuple[int, int],
        direction: str,
    ) -> tuple[int, int]:
        dx, dy = DIRECTION_VECTORS[direction]
        neighbor = self._known_open_neighbor(room, cell, direction)
        return neighbor or (cell[0] + dx, cell[1] + dy)

    def _remember_failed_character_probe(self) -> None:
        """Remember a facing direction whose Z press produced no game state."""
        if self.interaction_candidate is None or self.visual_goal is None:
            return
        room, cell_x, cell_y, direction, _instance_id, _name, target_x, target_y = (
            self.interaction_candidate
        )
        record = self.screen_regions.get(self.visual_goal, {})
        if (
            self.visual_goal[0] != room
            or record.get("hypothesis") != "possible_character"
            or self._region((target_x, target_y))
            != (self.visual_goal[1], self.visual_goal[2])
        ):
            return
        probe = (room, cell_x, cell_y, direction)
        self.character_probes[probe] += 1
        self.map_updates.append(
            {
                "type": "character_probe",
                "room": room,
                "cell": [cell_x, cell_y],
                "direction": direction,
                "attempts": self.character_probes[probe],
                "result": "no response",
            }
        )

    def _route_toward_visible_region(
        self,
        room: str,
        start: tuple[int, int],
        target_region: tuple[int, int],
    ) -> str | None:
        """Use only learned open paths to get closer to an on-screen landmark."""
        adjacency = self._adjacency(room)
        left = target_region[0] * EXPLORATION_REGION_CELLS
        top = target_region[1] * EXPLORATION_REGION_CELLS
        right = left + EXPLORATION_REGION_CELLS - 1
        bottom = top + EXPLORATION_REGION_CELLS - 1

        def distance_to_region(cell: tuple[int, int]) -> int:
            dx = left - cell[0] if cell[0] < left else cell[0] - right if cell[0] > right else 0
            dy = top - cell[1] if cell[1] < top else cell[1] - bottom if cell[1] > bottom else 0
            return dx + dy

        start_distance = distance_to_region(start)
        queue = deque([(start, None, 0)])
        visited = {start}
        routes: list[tuple[tuple[int, int, int], str]] = []
        while queue:
            current, first_direction, route_distance = queue.popleft()
            if first_direction is not None:
                routes.append(
                    (
                        (
                            distance_to_region(current),
                            route_distance,
                            self._recent_cell_cost(room, current),
                        ),
                        first_direction,
                    )
                )
            for direction, target in sorted(adjacency.get(current, [])):
                if target in visited:
                    continue
                visited.add(target)
                queue.append((target, first_direction or direction, route_distance + 1))
        if not routes:
            return None
        score, direction = min(routes, key=lambda item: item[0])
        return direction if score[0] < start_distance else None

    def _visual_hypothesis_is_confirmed(
        self,
        key: tuple[str, int, int],
        *,
        allow_story_retry: bool = False,
    ) -> bool:
        room, region_x, region_y = key
        matching_interactions = [
            interaction_key
            for interaction_key in self.interactables
            if interaction_key[0] == room
            and self._region((interaction_key[1], interaction_key[2]))
            == (region_x, region_y)
        ]
        if matching_interactions and not (
            allow_story_retry
            and any(
                self._story_interaction_retryable(interaction_key)
                for interaction_key in matching_interactions
            )
        ):
            return True
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

    def _finish_visual_goal(self) -> None:
        if self.visual_goal is not None and self.visual_goal in self.screen_regions:
            record = self.screen_regions[self.visual_goal]
            record["inspections"] = int(record.get("inspections", 0)) + 1
            self.map_updates.append(
                {
                    "type": "screen_region",
                    "room": self.visual_goal[0],
                    "region": [self.visual_goal[1], self.visual_goal[2]],
                    "views": int(record.get("views", 1)),
                    "interest": round(float(record.get("interest", 0.0)), 3),
                    "hypothesis": record.get("hypothesis"),
                    "inspections": int(record["inspections"]),
                }
            )
        self.visual_goal = None
        self.visual_goal_age = 0
        self.visual_goal_stalls = 0
        self.visual_goal_best_distance = None

    def _possible_exit_probes(self, room: str) -> list[Edge]:
        """Return plausible exits inferred only from the learned floor outline."""
        cells = {
            (seen_x, seen_y)
            for seen_room, seen_x, seen_y in self.seen_cells
            if seen_room == room
        }
        if len(cells) < 2:
            return []

        column_bounds: dict[int, tuple[int, int]] = {}
        row_bounds: dict[int, tuple[int, int]] = {}
        for x, y in cells:
            low_y, high_y = column_bounds.get(x, (y, y))
            column_bounds[x] = min(low_y, y), max(high_y, y)
            low_x, high_x = row_bounds.get(y, (x, x))
            row_bounds[y] = min(low_x, x), max(high_x, x)

        candidates: list[Edge] = []
        for x, y in sorted(cells):
            outline_directions = []
            if y == column_bounds[x][0]:
                outline_directions.append("up")
            if y == column_bounds[x][1]:
                outline_directions.append("down")
            if x == row_bounds[y][0]:
                outline_directions.append("left")
            if x == row_bounds[y][1]:
                outline_directions.append("right")
            for direction in outline_directions:
                probe = (room, x, y, direction)
                if (
                    self.exit_probes[probe] >= MAX_EXIT_PROBES
                    or self._blocked_near(room, (x, y), direction)
                    or self._known_open_neighbor(room, (x, y), direction) is not None
                    or self._known_open_neighbor(
                        room, (x, y), OPPOSITE[direction]
                    )
                    is None
                    or self._known_warp_endpoint(room, (x, y))
                    or self._is_entry_warp_direction(room, (x, y), direction)
                ):
                    continue
                candidates.append(probe)
        return candidates

    def _route_to_possible_exit(
        self,
        room: str,
        start: tuple[int, int],
    ) -> tuple[str, Edge] | None:
        """Keep one learned-outline goal long enough to reach and test it."""
        adjacency = self._adjacency(room)
        candidates = self._possible_exit_probes(room)
        candidate_set = set(candidates)

        if self.exit_search_goal not in candidate_set:
            self.exit_search_goal = None
        if self.exit_search_goal is not None:
            goal = self.exit_search_goal
            goal_cell = (goal[1], goal[2])
            if start == goal_cell:
                return goal[3], goal
            route = self._route_to_target(adjacency, start, goal_cell)
            if route is not None:
                return route[0], goal
            self.exit_search_goal = None

        scored: list[
            tuple[tuple[int, int, int, int, int, int, str], str, Edge]
        ] = []
        for probe in candidates:
            _probe_room, probe_x, probe_y, probe_direction = probe
            goal_cell = (probe_x, probe_y)
            if start == goal_cell:
                first_direction = probe_direction
                distance = 0
            else:
                route = self._route_to_target(adjacency, start, goal_cell)
                if route is None:
                    continue
                first_direction, distance = route
            path_degree = len(adjacency.get(goal_cell, []))
            endpoint_rank = 0 if path_degree <= 1 else 1 if path_degree == 2 else 2
            straight_approach = self._straight_approach_length(
                room,
                goal_cell,
                probe_direction,
            )
            region_attempts = sum(
                attempts
                for (attempt_room, attempt_x, attempt_y, attempt_direction), attempts
                in self.exit_probes.items()
                if attempt_room == room
                and attempt_direction == probe_direction
                and self._region((attempt_x, attempt_y)) == self._region(goal_cell)
            )
            score = (
                endpoint_rank,
                -straight_approach,
                region_attempts,
                distance,
                self.visits[(room, probe_x, probe_y)],
                self._recent_cell_cost(room, goal_cell),
                probe_direction,
            )
            scored.append((score, first_direction, probe))
        if not scored:
            return None
        _score, direction, probe = min(scored, key=lambda item: item[0])
        self.exit_search_goal = probe
        self._remember_path_continuation(probe)
        return direction, probe

    def _straight_approach_length(
        self,
        room: str,
        cell: tuple[int, int],
        outward_direction: str,
    ) -> int:
        """Count the observed straight path leading into an untested boundary."""
        backward = OPPOSITE[outward_direction]
        current = cell
        length = 0
        for _ in range(4):
            neighbor = self._known_open_neighbor(room, current, backward)
            if neighbor is None:
                break
            length += 1
            current = neighbor
        return length

    def _remember_path_continuation(self, probe: Edge) -> None:
        """Expose a geometry-learned passage even when it has no door artwork."""
        room, cell_x, cell_y, _direction = probe
        key = (room, *self._region((cell_x, cell_y)))
        record = self.screen_regions.get(key)
        if record is None or record.get("hypothesis") == "possible_character":
            return
        changed = record.get("hypothesis") != "possible_exit" or not record.get(
            "path_continuation"
        )
        record["hypothesis"] = "possible_exit"
        record["path_continuation"] = True
        if changed:
            self.map_updates.append(
                {
                    "type": "screen_region",
                    "room": room,
                    "region": [key[1], key[2]],
                    "views": int(record.get("views", 1)),
                    "interest": round(float(record.get("interest", 0.0)), 3),
                    "hypothesis": "possible_exit",
                    "inspections": int(record.get("inspections", 0)),
                    "path_continuation": True,
                }
            )

    def _break_oscillation(
        self,
        room: str,
        cell: tuple[int, int],
        proposed: str,
    ) -> tuple[str, bool]:
        """Escape exact endpoint bounces and low-area one-axis loops."""
        if len(self.recent_cells) >= LOW_AREA_LOOP_SAMPLES:
            recent_cells = list(self.recent_cells)[-LOW_AREA_LOOP_SAMPLES:]
            if all(recent_room == room for recent_room, _x, _y in recent_cells):
                xs = [x for _recent_room, x, _y in recent_cells]
                ys = [y for _recent_room, _x, y in recent_cells]
                span_x = max(xs) - min(xs)
                span_y = max(ys) - min(ys)
                unique_cells = len(set(recent_cells))
                avoided: set[str] = set()
                loop_name = ""
                if span_x <= 1 and 1 <= span_y <= 4 and unique_cells <= 5:
                    avoided = {"up", "down"}
                    loop_name = "detected up/down loop in a small area"
                elif span_y <= 1 and 1 <= span_x <= 4 and unique_cells <= 5:
                    avoided = {"left", "right"}
                    loop_name = "detected left/right loop in a small area"
                recent_directions = {
                    direction
                    for decision_room, _x, _y, direction in self.decision_history
                    if decision_room == room
                }
                if (
                    avoided
                    and proposed in avoided
                    and avoided.issubset(recent_directions)
                ):
                    escape = self._loop_escape_direction(room, cell, avoided)
                    if escape is not None:
                        self.loop_direction_cooldowns[(room, *self._region(cell))] = (
                            frozenset(avoided),
                            self.navigation_tick + LOOP_DIRECTION_COOLDOWN,
                        )
                        self.exit_search_goal = None
                        self.visual_goal = None
                        self.loop_reason = loop_name
                        self.oscillation_breaks += 1
                        return escape, True
        if len(self.decision_history) < 4:
            return proposed, False
        first, second, third, fourth = list(self.decision_history)[-4:]
        here = (room, *cell)
        if not (
            first[:3] == here
            and third[:3] == here
            and second[:3] == fourth[:3]
            and second[:3] != here
            and first[3] == proposed
            and third[3] == proposed
            and second[3] == OPPOSITE[proposed]
            and fourth[3] == OPPOSITE[proposed]
        ):
            return proposed, False

        avoided = {proposed, OPPOSITE[proposed]}
        escape = self._loop_escape_direction(room, cell, avoided)
        if escape is None:
            return proposed, False
        self.loop_direction_cooldowns[(room, *self._region(cell))] = (
            frozenset(avoided),
            self.navigation_tick + LOOP_DIRECTION_COOLDOWN,
        )
        self.loop_reason = "detected repeated corridor loop"
        self.oscillation_breaks += 1
        return escape, True

    def _loop_escape_direction(
        self,
        room: str,
        cell: tuple[int, int],
        avoided: set[str],
    ) -> str | None:
        alternatives = [
            direction
            for direction in DIRECTION_VECTORS
            if direction not in avoided
            and not self._blocked_near(room, cell, direction)
            and not self._is_entry_warp_direction(room, cell, direction)
        ]
        if not alternatives:
            return None

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

        return min(alternatives, key=score)

    def _loop_avoid_directions(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> set[str]:
        key = (room, *self._region(cell))
        cooldown = self.loop_direction_cooldowns.get(key)
        if cooldown is None:
            return set()
        directions, expires_at = cooldown
        if self.navigation_tick >= expires_at:
            self.loop_direction_cooldowns.pop(key, None)
            return set()
        return set(directions)

    def _exploration_direction_score(
        self,
        room: str,
        cell: tuple[int, int],
        direction: str,
    ) -> tuple[int, int, int, int, str]:
        target = self._direction_target(room, cell, direction)
        blocked_penalty = 10_000 if self._blocked_near(room, cell, direction) else 0
        cooldown_penalty = 2_000 if self._interaction_is_cooldown(room, cell, direction) else 0
        return (
            blocked_penalty + cooldown_penalty,
            self.visits[(room, *target)],
            self._recent_cell_cost(room, target),
            self._region_distance_cost(room, cell, target),
            direction,
        )

    def _region_distance_cost(
        self,
        room: str,
        cell: tuple[int, int],
        target: tuple[int, int],
    ) -> int:
        current_region = self._region(cell)
        target_region = self._region(target)
        return abs(current_region[0] - target_region[0]) + abs(current_region[1] - target_region[1])

    def _least_visited_direction(
        self,
        room: str,
        cell: tuple[int, int],
        previous: str,
        avoid: set[str] | None = None,
    ) -> str:
        avoid = set(avoid or ()) | self._loop_avoid_directions(room, cell)
        right = {"up": "right", "right": "down", "down": "left", "left": "up"}
        left = {value: key for key, value in right.items()}
        preference = [previous, right[previous], left[previous], OPPOSITE[previous]]
        available = [
            direction
            for direction in preference
            if direction not in avoid
            and not self._is_entry_warp_direction(room, cell, direction)
        ]
        safe = [
            direction
            for direction in available
            if not self._blocked_near(room, cell, direction)
        ]
        for direction in safe:
            if self._direction_is_unexplored(room, cell, direction):
                return direction

        route_direction = self._route_to_nearest_frontier(
            room, cell, allowed_first=set(safe)
        )
        if route_direction is not None:
            return route_direction

        # If old map history claims every exit is blocked, keep recovery
        # bounded by trying a different direction instead of repeating the one
        # that just failed forever. Fresh movement evidence can then repair the map.
        known_paths = [
            direction
            for direction in safe
            if self._known_open_neighbor(room, cell, direction) is not None
        ]
        candidates = known_paths or safe or available
        if not candidates:
            candidates = [
                direction
                for direction in preference
                if not self._is_entry_warp_direction(room, cell, direction)
            ] or preference
        scored: list[tuple[tuple[int, int, int, int, str], str]] = []
        for rank, direction in enumerate(candidates):
            target = self._direction_target(room, cell, direction)
            obstacle_cost = (
                self.blocked[(room, *cell, direction)] * 10_000
                + (10_000 if self._blocked_near(room, cell, direction) else 0)
            )
            tried_cost = 200 if (room, *cell, direction) in self.tried else 0
            visit_cost = self.visits[(room, *target)] * 10
            recent_cost = self._recent_cell_cost(room, target)
            reverse_cost = 120 if direction == OPPOSITE[previous] else 0
            scored.append(
                (
                    (
                        obstacle_cost
                        + tried_cost
                        + visit_cost
                        + recent_cost
                        + reverse_cost
                        + rank,
                        self.visits[(room, *target)],
                        self._recent_cell_cost(room, target),
                        self._region_distance_cost(room, cell, target),
                        direction,
                    ),
                    direction,
                )
            )
        return min(scored, key=lambda item: item[0])[1]

    def _direction_is_unexplored(
        self,
        room: str,
        cell: tuple[int, int],
        direction: str,
    ) -> bool:
        return (
            not self._region_direction_tried(room, cell, direction)
            and not self._blocked_near(room, cell, direction)
            and self._known_open_neighbor(room, cell, direction) is None
            and not self._known_warp_direction(room, cell, direction)
        )

    def _region_direction_tried(
        self,
        room: str,
        cell: tuple[int, int],
        direction: str,
    ) -> bool:
        region_x, region_y = self._region(cell)
        key = (room, region_x, region_y, direction)
        if key in self.tried_regions:
            return True
        for tried_room, tried_x, tried_y, tried_direction in self.tried:
            if (
                tried_room == room
                and tried_direction == direction
                and self._region((tried_x, tried_y)) == (region_x, region_y)
            ):
                self.tried_regions.add(key)
                return True
        return False

    def _known_warp_direction(
        self,
        room: str,
        cell: tuple[int, int],
        direction: str,
    ) -> bool:
        return any(
            source_room == room
            and (source_x, source_y) == cell
            and action == direction
            for (
                source_room,
                source_x,
                source_y,
                action,
                _target_room,
                _target_x,
                _target_y,
            ) in self.warps
        )

    def _known_warp_endpoint(self, room: str, cell: tuple[int, int]) -> bool:
        return any(
            source_room == room and (source_x, source_y) == cell
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

    def _is_entry_warp_direction(
        self,
        room: str,
        cell: tuple[int, int],
        direction: str,
    ) -> bool:
        entry_room = self.room_entry_from.get(room)
        vector_x, vector_y = DIRECTION_VECTORS[direction]
        next_cell = (cell[0] + vector_x, cell[1] + vector_y)
        for (
            source_room,
            source_x,
            source_y,
            action,
            target_room,
            _target_x,
            _target_y,
        ) in self.warps:
            if source_room != room or not (
                target_room == entry_room
                or frozenset((room, target_room)) in self.suppressed_room_links
            ):
                continue
            source = (source_x, source_y)
            if source == cell and action == direction:
                return True
            current_distance = max(
                abs(cell[0] - source_x), abs(cell[1] - source_y)
            )
            next_distance = max(
                abs(next_cell[0] - source_x), abs(next_cell[1] - source_y)
            )
            if (
                next_distance < current_distance
                and next_distance <= BACKTRACK_WARP_RADIUS
            ):
                return True
        return False

    def _known_open_neighbor(
        self,
        room: str,
        cell: tuple[int, int],
        direction: str,
    ) -> tuple[int, int] | None:
        for (
            edge_room,
            source_x,
            source_y,
            edge_direction,
            target_x,
            target_y,
        ) in self.open_edges:
            if (
                edge_room == room
                and (source_x, source_y) == cell
                and edge_direction == direction
                and self._edge_matches_direction(cell, direction, (target_x, target_y))
            ):
                return target_x, target_y
        return None

    def _recent_cell_cost(self, room: str, cell: tuple[int, int]) -> int:
        cost = 0
        for age, recent in enumerate(reversed(self.recent_cells)):
            if recent == (room, *cell):
                cost += max(20, 360 - age * 20)
        return cost

    def _adjacency(
        self,
        room: str,
        *,
        avoid_backtrack: bool = True,
    ) -> dict[tuple[int, int], list[tuple[str, tuple[int, int]]]]:
        adjacency: dict[tuple[int, int], list[tuple[str, tuple[int, int]]]] = {}
        for (
            edge_room,
            source_x,
            source_y,
            direction,
            target_x,
            target_y,
        ) in self.open_edges:
            source = (source_x, source_y)
            target = (target_x, target_y)
            if (
                edge_room == room
                and self._edge_matches_direction(source, direction, target)
                and not self._blocked_near(room, source, direction)
                and (
                    not avoid_backtrack
                    or not self._is_entry_warp_direction(room, source, direction)
                )
            ):
                adjacency.setdefault(source, []).append((direction, target))
        return adjacency

    def _route_to_nearest_frontier(
        self,
        room: str,
        start: tuple[int, int],
        allowed_first: set[str] | None = None,
    ) -> str | None:
        adjacency = self._adjacency(room)

        queue = deque([(start, None)])
        visited = {start}
        while queue:
            cell, first_direction = queue.popleft()
            if cell != start and any(
                self._direction_is_unexplored(room, cell, direction)
                for direction in DIRECTION_VECTORS
            ):
                return first_direction
            for direction, target in sorted(adjacency.get(cell, [])):
                if cell == start and allowed_first is not None and direction not in allowed_first:
                    continue
                if target in visited:
                    continue
                visited.add(target)
                queue.append((target, first_direction or direction))
        return None

    def _route_to_learned_warp(
        self,
        room: str,
        start: tuple[int, int],
    ) -> tuple[str, Warp] | None:
        """Route only to warp endpoints previously observed during a room change."""
        forward_adjacency = self._adjacency(room)
        all_adjacency = self._adjacency(room, avoid_backtrack=False)
        candidates: list[
            tuple[tuple[int, int, int, int, int], str, Warp]
        ] = []
        for warp, crossings in self.warps.items():
            (
                source_room,
                source_x,
                source_y,
                action,
                target_room,
                _target_x,
                _target_y,
            ) = warp
            if source_room != room or action not in DIRECTION_VECTORS:
                continue
            link = frozenset((room, target_room))
            is_entry = self.room_entry_from.get(room) == target_room
            link_penalty = 2 if link in self.suppressed_room_links else int(is_entry)
            adjacency = all_adjacency if link_penalty else forward_adjacency
            source = (source_x, source_y)
            if source == start:
                if self._blocked_near(room, start, action):
                    continue
                first_direction = action
                distance = 0
                route_quality = 0
            else:
                route = self._route_to_target(adjacency, start, source)
                if route is None:
                    route = self._route_to_region_target(
                        room,
                        start,
                        source,
                        allow_backtrack=bool(link_penalty),
                    )
                    if route is None:
                        continue
                    route_quality = 1
                else:
                    route_quality = 0
                first_direction, distance = route
            target_regions = len(
                {
                    (region_x, region_y)
                    for seen_room, region_x, region_y in self.seen_regions
                    if seen_room == target_room
                }
                | {
                    self._region((seen_x, seen_y))
                    for seen_room, seen_x, seen_y in self.seen_cells
                    if seen_room == target_room
                }
            )
            score = (
                link_penalty,
                target_regions,
                crossings,
                route_quality,
                distance,
            )
            candidates.append((score, first_direction, warp))
        if not candidates:
            return None
        _score, direction, warp = min(candidates, key=lambda candidate: candidate[0])
        return direction, warp

    def _route_to_region_target(
        self,
        room: str,
        start: tuple[int, int],
        goal: tuple[int, int],
        *,
        allow_backtrack: bool,
    ) -> tuple[str, int] | None:
        """Use coarse visited regions when sampled cell paths contain small gaps."""
        start_region = self._region(start)
        goal_region = self._region(goal)
        if start_region == goal_region:
            direction = self._direction_toward_cell(
                room,
                start,
                goal,
                allow_backtrack=allow_backtrack,
            )
            return (direction, 0) if direction is not None else None

        regions = {
            (region_x, region_y)
            for seen_room, region_x, region_y in self.seen_regions
            if seen_room == room
        }
        regions.update(
            self._region((cell_x, cell_y))
            for seen_room, cell_x, cell_y in self.seen_cells
            if seen_room == room
        )
        regions.update((start_region, goal_region))
        queue = deque([(start_region, None, 0)])
        visited = {start_region}
        offsets = [
            (dx, dy)
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            if dx or dy
        ]
        while queue:
            region, first_region, distance = queue.popleft()
            if region == goal_region and first_region is not None:
                target = (
                    first_region[0] * EXPLORATION_REGION_CELLS
                    + EXPLORATION_REGION_CELLS // 2,
                    first_region[1] * EXPLORATION_REGION_CELLS
                    + EXPLORATION_REGION_CELLS // 2,
                )
                direction = self._direction_toward_cell(
                    room,
                    start,
                    target,
                    allow_backtrack=allow_backtrack,
                )
                return (direction, distance) if direction is not None else None
            neighbors = sorted(
                (
                    (region[0] + dx, region[1] + dy)
                    for dx, dy in offsets
                ),
                key=lambda candidate: (
                    max(
                        abs(candidate[0] - goal_region[0]),
                        abs(candidate[1] - goal_region[1]),
                    ),
                    abs(candidate[0] - goal_region[0])
                    + abs(candidate[1] - goal_region[1]),
                    candidate,
                ),
            )
            for neighbor in neighbors:
                if neighbor not in regions or neighbor in visited:
                    continue
                visited.add(neighbor)
                queue.append(
                    (neighbor, first_region or neighbor, distance + 1)
                )
        return None

    def _direction_toward_cell(
        self,
        room: str,
        source: tuple[int, int],
        target: tuple[int, int],
        *,
        allow_backtrack: bool,
    ) -> str | None:
        delta_x = target[0] - source[0]
        delta_y = target[1] - source[1]
        horizontal = "right" if delta_x > 0 else "left"
        vertical = "down" if delta_y > 0 else "up"
        axes: list[str] = []
        if delta_x:
            axes.append(horizontal)
        if delta_y:
            axes.append(vertical)
        axes.sort(
            key=lambda direction: (
                -(
                    abs(delta_x)
                    if direction in {"left", "right"}
                    else abs(delta_y)
                ),
                direction != self.direction,
                direction,
            )
        )
        return next(
            (
                direction
                for direction in axes
                if not self._blocked_near(room, source, direction)
                and (
                    allow_backtrack
                    or not self._is_entry_warp_direction(room, source, direction)
                )
            ),
            None,
        )

    @staticmethod
    def _route_to_target(
        adjacency: dict[tuple[int, int], list[tuple[str, tuple[int, int]]]],
        start: tuple[int, int],
        goal: tuple[int, int],
    ) -> tuple[str, int] | None:
        queue = deque([(start, None, 0)])
        visited = {start}
        while queue:
            cell, first_direction, distance = queue.popleft()
            if cell == goal and first_direction is not None:
                return first_direction, distance
            for direction, target in sorted(adjacency.get(cell, [])):
                if target in visited:
                    continue
                visited.add(target)
                queue.append((target, first_direction or direction, distance + 1))
        return None

    @staticmethod
    def _edge_matches_direction(
        source: tuple[int, int], direction: str, target: tuple[int, int]
    ) -> bool:
        delta_x = target[0] - source[0]
        delta_y = target[1] - source[1]
        vector_x, vector_y = DIRECTION_VECTORS[direction]
        forward = delta_x * vector_x + delta_y * vector_y
        lateral = abs(delta_x * vector_y - delta_y * vector_x)
        return forward == 1 and lateral == 0

    def _select(
        self, name: str, reason: str, telemetry: TelemetrySample | None
    ) -> Action:
        self.reason = reason
        if name in DIRECTION_VECTORS and telemetry and telemetry.mode == "overworld":
            self.last_movement = name
            self.last_overworld_movement = name
            self.last_position = (telemetry.x, telemetry.y)
            self.last_room = self._room_key(telemetry)
            self.last_cell = self._cell(telemetry)
            self.last_movement_sample_at = telemetry.received_at
            self.tried.add((self.last_room, *self.last_cell, name))
            region_x, region_y = self._region(self.last_cell)
            self.tried_regions.add((self.last_room, region_x, region_y, name))
        else:
            self.last_movement = None
        return ACTIONS[name]

    def summary(self) -> dict:
        return {
            "rooms_seen": sorted({room for room, _x, _y in self.seen_cells}),
            "mapped_cells": len(self.seen_cells),
            "explored_regions": len(self.seen_regions),
            "blocked_edges": sum(self.blocked.values()),
            "blocked_zones": len(self.blocked_zones),
            "learned_open_edges": len(self.open_edges),
            "completed_interactions": len(self.interacted_zones),
            "remembered_interaction_targets": len(self.interacted_targets),
            "identified_interactables": len(self.interactables),
            "story_progress_events": self.story_progress_events,
            "story_epoch": self.story_epoch,
            "story_stall_steps": self.story_stall_steps,
            "interactions_with_story_consequences": sum(
                int(record.get("progressions", 0)) > 0
                for record in self.interactables.values()
            ),
            "promising_story_interactions": sum(
                record.get("usefulness") == "choice_pending"
                for record in self.interactables.values()
            ),
            "flavor_interactions": sum(
                record.get("usefulness") == "flavor"
                for record in self.interactables.values()
            ),
            "confirmed_npcs": sum(
                record.get("classification") == "confirmed_npc"
                for record in self.interactables.values()
            ),
            "tested_nonchoice_interactions": sum(
                record.get("classification") == "tested_nonchoice"
                for record in self.interactables.values()
            ),
            "choice_menus_learned": len(self.choice_trials),
            "failed_character_directions": sum(self.character_probes.values()),
            "successful_choice_patterns": sum(
                record.get("successful_pattern") is not None
                for record in self.choice_trials
            ),
            "regions_seen_on_screen": len(self.screen_regions),
            "visual_hypotheses": sum(
                bool(record.get("hypothesis"))
                for record in self.screen_regions.values()
            ),
            "visual_hypotheses_inspected": sum(
                int(record.get("inspections", 0))
                for record in self.screen_regions.values()
            ),
            "exit_probe_attempts": sum(self.exit_probes.values()),
            "oscillation_breaks": self.oscillation_breaks,
            "suppressed_room_links": [
                sorted(link) for link in sorted(
                    self.suppressed_room_links,
                    key=lambda rooms: tuple(sorted(rooms)),
                )
            ],
            "discovered_warps": [
                {
                    "from": source,
                    "at": [source_x, source_y],
                    "action": action,
                    "to": target,
                    "entry": [target_x, target_y],
                    "count": count,
                }
                for (
                    source,
                    source_x,
                    source_y,
                    action,
                    target,
                    target_x,
                    target_y,
                ), count in sorted(self.warps.items())
            ],
            "transitions": [
                {"from": source, "to": target, "count": count}
                for (source, target), count in sorted(self.transitions.items())
            ],
        }

    def save_memory(self) -> None:
        self.world.save()

    def drain_map_updates(self) -> list[dict[str, object]]:
        updates = self.map_updates
        self.map_updates = []
        return updates
