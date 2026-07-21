from __future__ import annotations

from pathlib import Path

from .actions import Action
from .battle import BattleController
from .dialogue import DialogueReader
from .objectives import ObjectiveManager
from .observer import Observation
from .perception import GameState, Perception
from .run2_explorer import Run2Explorer
from .telemetry import TelemetrySample
from .visual_freshness import VisualFreshnessGuard


class HierarchicalPolicy:
    """Specialized reflex controllers wrapped around the proven explorer."""

    def __init__(self, seed: int = 0, memory_path: Path | None = None):
        self.explorer = Run2Explorer(seed, memory_path)
        self.objectives = ObjectiveManager()
        self.dialogue = DialogueReader()
        self.battle = BattleController()
        self.visual_freshness = VisualFreshnessGuard()
        self.reason = "hierarchical policy starting"
        self.last_dialogue_signature: str | None = None
        self.last_dialogue_text: str | None = None
        self.last_visual_valid = True

    def __getattr__(self, name: str):
        return getattr(self.explorer, name)

    def choose(
        self,
        observation: Observation,
        perception: Perception,
        telemetry: TelemetrySample | None = None,
    ) -> Action:
        observation = self.visual_freshness.validate(
            observation,
            telemetry,
        )
        self.last_visual_valid = observation.visual_valid

        if perception.state is GameState.BATTLE:
            soul = None
            if telemetry is not None:
                x = (
                    telemetry.player_x
                    if telemetry.player_x is not None
                    else telemetry.x
                )
                y = (
                    telemetry.player_y
                    if telemetry.player_y is not None
                    else telemetry.y
                )
                soul = (x, y)
            action = self.battle.choose(
                observation.frame,
                soul,
                visual_valid=observation.visual_valid,
            )
            self.reason = self.battle.reason
            room = self._room_name(telemetry)
            self.objectives.objective_for_state(
                "battle",
                self.reason,
                room,
            )
            return action

        action = self.explorer.choose(
            observation,
            perception,
            telemetry,
        )
        self.reason = self.explorer.reason
        room = self._room_name(telemetry)

        if (
            observation.visual_valid
            and perception.state
            in {GameState.DIALOGUE, GameState.MENU}
        ):
            reading = self.dialogue.analyze(observation.frame)
            self.last_dialogue_signature = reading.signature
            self.last_dialogue_text = reading.text
            detail = f"; visible option rows={reading.option_count}"
            if reading.text:
                detail += f"; OCR={reading.text[:100]!r}"
            self.reason += detail
        elif perception.state in {
            GameState.DIALOGUE,
            GameState.MENU,
        }:
            self.reason += "; visual capture stale, skip dialogue analysis"

        objective = self.objectives.objective_for_state(
            perception.state.value,
            self.reason,
            room,
        )
        self.reason = f"[{objective.kind.value}] {self.reason}"
        return action

    @staticmethod
    def _room_name(
        telemetry: TelemetrySample | None,
    ) -> str | None:
        if telemetry is None:
            return None
        return telemetry.room_name or str(telemetry.room_id)

    def summary(self) -> dict:
        summary = self.explorer.summary()
        summary["current_objective"] = (
            self.objectives.current.kind.value
            if self.objectives.current
            else None
        )
        summary["objective_changes"] = len(
            self.objectives.history
        )
        summary["last_dialogue_signature"] = (
            self.last_dialogue_signature
        )
        summary["last_dialogue_text"] = self.last_dialogue_text
        summary["frozen_visual_frames"] = (
            self.visual_freshness.frozen_frames
        )
        return summary
