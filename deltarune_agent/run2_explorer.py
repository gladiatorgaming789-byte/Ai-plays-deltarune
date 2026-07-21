from __future__ import annotations

from collections import deque
from pathlib import Path

from .actions import Action
from .dialogue import DialogueReader
from .improved_explorer import ImprovedExplorer
from .observer import Observation
from .perception import GameState, Perception
from .policy import DIRECTION_VECTORS
from .telemetry import TelemetrySample
from .world_model import Warp


ROOM_LINK_COOLDOWN_STEPS = 600
RAPID_RETURN_WINDOW_STEPS = 240
MIN_WRITER_CHOICE_ROWS = 2


class Run2Explorer(ImprovedExplorer):
    """Explorer fixes learned from the second recorded playthrough.

    The run reached four rooms, but repeatedly crossed the kitchen/bathroom
    doorway while the game had disabled player control. It also interpreted
    ordinary obj_writer text rows as a response menu. This layer keeps the
    earlier warp reliability rules and adds transition settling, room-link
    cooldowns, and conservative writer-choice handling.
    """

    def __init__(self, seed: int = 0, memory_path: Path | None = None):
        super().__init__(seed, memory_path)
        self.dialogue_reader = DialogueReader(enable_ocr=False)
        self.room_link_cooldowns: dict[frozenset[str], int] = {}
        self.transition_trace: deque[tuple[str, int]] = deque(maxlen=8)
        self.control_lock_waits = 0
        self.rejected_writer_choices = 0
        self.rapid_room_returns = 0

    def choose(
        self,
        observation: Observation,
        perception: Perception,
        telemetry: TelemetrySample | None = None,
    ) -> Action:
        if (
            telemetry is not None
            and telemetry.mode == "overworld"
            and telemetry.player_controlled is False
        ):
            # Door transitions briefly report overworld while the control gate is
            # closed. Sending movement here can carry the previous direction into
            # the destination room and immediately walk back through the doorway.
            self._observe_room(telemetry)
            self._suspend_movement_learning()
            self.previous_state = GameState.OVERWORLD
            self.control_lock_waits += 1
            return self._select(
                "wait",
                "transition control locked; release movement until control returns",
                telemetry,
            )

        if (
            telemetry is not None
            and telemetry.object_name == "obj_writer"
            and perception.state is GameState.DIALOGUE
            and observation.visual_valid
        ):
            reading = self.dialogue_reader.analyze(observation.frame)
            if reading.option_count < MIN_WRITER_CHOICE_ROWS:
                # One active text row is normal dialogue, not a menu. Bypass the
                # broad legacy asterisk heuristic so arrow keys are not injected
                # into ordinary flavor text.
                interaction_was_pending = self.interaction_candidate is not None
                self._complete_pending_interaction()
                self._observe_story_state(
                    GameState.DIALOGUE,
                    telemetry,
                    interaction_was_pending,
                )
                self._suspend_movement_learning()
                self.active_choice_record = None
                self.menu_action_queue.clear()
                self.rejected_writer_choices += 1
                return self._select(
                    "confirm",
                    f"advance writer dialogue; {reading.option_count} visible text row is not a choice",
                    telemetry,
                )

        return super().choose(observation, perception, telemetry)

    def _observe_room(self, telemetry: TelemetrySample) -> None:
        previous_room = self.observed_room
        room = self._room_key(telemetry)
        super()._observe_room(telemetry)
        if previous_room is None or room == previous_room:
            return

        link = frozenset((previous_room, room))
        self.room_link_cooldowns[link] = (
            self.navigation_tick + ROOM_LINK_COOLDOWN_STEPS
        )
        self.transition_trace.append((room, self.navigation_tick))
        if len(self.transition_trace) >= 3:
            first_room, first_tick = self.transition_trace[-3]
            middle_room, _middle_tick = self.transition_trace[-2]
            last_room, last_tick = self.transition_trace[-1]
            if (
                first_room == last_room
                and middle_room != last_room
                and last_tick - first_tick <= RAPID_RETURN_WINDOW_STEPS
            ):
                self.rapid_room_returns += 1
                self.suppressed_room_links.add(
                    frozenset((first_room, middle_room))
                )
                self.steps_without_frontier = 0
                self.stalled_recovery_steps = 0

    def _link_is_cooling_down(self, room: str, target_room: str) -> bool:
        link = frozenset((room, target_room))
        expires_at = self.room_link_cooldowns.get(link)
        if expires_at is None:
            return False
        if self.navigation_tick >= expires_at:
            self.room_link_cooldowns.pop(link, None)
            return False
        return True

    def _route_to_learned_warp(
        self,
        room: str,
        start: tuple[int, int],
    ) -> tuple[str, Warp] | None:
        route = super()._route_to_learned_warp(room, start)
        if route is None:
            return None
        direction, warp = route
        target_room = warp[4]
        if self._link_is_cooling_down(room, target_room):
            return None
        return direction, warp

    def _is_entry_warp_direction(
        self,
        room: str,
        cell: tuple[int, int],
        direction: str,
    ) -> bool:
        if super()._is_entry_warp_direction(room, cell, direction):
            return True
        dx, dy = DIRECTION_VECTORS[direction]
        next_cell = (cell[0] + dx, cell[1] + dy)
        for warp, _crossings in self._reliable_warps():
            source_room, source_x, source_y, action, target_room, _tx, _ty = warp
            if source_room != room or not self._link_is_cooling_down(room, target_room):
                continue
            source = (source_x, source_y)
            if source == cell and action == direction:
                return True
            current_distance = max(abs(cell[0] - source_x), abs(cell[1] - source_y))
            next_distance = max(
                abs(next_cell[0] - source_x),
                abs(next_cell[1] - source_y),
            )
            if next_distance < current_distance and next_distance <= 2:
                return True
        return False

    def summary(self) -> dict:
        summary = super().summary()
        summary["control_lock_waits"] = self.control_lock_waits
        summary["rejected_writer_choices"] = self.rejected_writer_choices
        summary["rapid_room_returns"] = self.rapid_room_returns
        summary["active_room_link_cooldowns"] = len(self.room_link_cooldowns)
        return summary
