from __future__ import annotations

from pathlib import Path

from .actions import Action
from .battle import BattleController
from .dialogue import DialogueReader
from .objectives import ObjectiveManager
from .observer import Observation
from .perception import GameState, Perception
from .policy import StarterPolicy
from .telemetry import TelemetrySample


class HierarchicalPolicy:
    """Specialized reflex controllers wrapped around the proven explorer.

    Overworld navigation and persistent learning remain delegated to
    ``StarterPolicy``. Battle, dialogue analysis, and high-level objective
    tracking are isolated so they can evolve without destabilizing mapping.
    """

    def __init__(self, seed: int = 0, memory_path: Path | None = None):
        self.explorer = StarterPolicy(seed, memory_path)
        self.objectives = ObjectiveManager()
        self.dialogue = DialogueReader()
        self.battle = BattleController()
        self.reason = "hierarchical policy starting"
        self.last_dialogue_signature: str | None = None
        self.last_dialogue_text: str | None = None

    def __getattr__(self, name: str):
        return getattr(self.explorer, name)

    def choose(
        self,
        observation: Observation,
        perception: Perception,
        telemetry: TelemetrySample | None = None,
    ) -> Action:
        if perception.state is GameState.BATTLE:
            soul = None
            if telemetry is not None:
                x = telemetry.player_x if telemetry.player_x is not None else telemetry.x
                y = telemetry.player_y if telemetry.player_y is not None else telemetry.y
                soul = (x, y)
            action = self.battle.choose(observation.frame, soul)
            self.reason = self.battle.reason
            room = self._room_name(telemetry)
            self.objectives.objective_for_state("battle", self.reason, room)
            return action

        action = self.explorer.choose(observation, perception, telemetry)
        self.reason = self.explorer.reason
        room = self._room_name(telemetry)

        if perception.state in {GameState.DIALOGUE, GameState.MENU}:
            reading = self.dialogue.analyze(observation.frame)
            self.last_dialogue_signature = reading.signature
            self.last_dialogue_text = reading.text
            detail = f"; visible option rows={reading.option_count}"
            if reading.text:
                detail += f"; OCR={reading.text[:100]!r}"
            self.reason += detail

        objective = self.objectives.objective_for_state(
            perception.state.value,
            self.reason,
            room,
        )
        self.reason = f"[{objective.kind.value}] {self.reason}"
        return action

    @staticmethod
    def _room_name(telemetry: TelemetrySample | None) -> str | None:
        if telemetry is None:
            return None
        return telemetry.room_name or str(telemetry.room_id)

    def summary(self) -> dict:
        summary = self.explorer.summary()
        summary["current_objective"] = (
            self.objectives.current.kind.value if self.objectives.current else None
        )
        summary["objective_changes"] = len(self.objectives.history)
        summary["last_dialogue_signature"] = self.last_dialogue_signature
        summary["last_dialogue_text"] = self.last_dialogue_text
        return summary
