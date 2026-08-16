from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .actions import Action
from .aligned_navigation_maps import install_aligned_navigation_exporter
from .battle import BattleController
from .dialogue import DialogueReader
from .objectives import ObjectiveManager
from .observer import Observation
from .perception import GameState, Perception
from .run15_screen_regions import install_run15_screen_region_analyzer
from .run16_semantics import install_run16_semantics
from .run20_reporting import install_run20_reporting
from .telemetry import TelemetrySample
from .visual_freshness import VisualFreshnessGuard
from .warp_classification_v2 import install_warp_classification_v2


# Install the older persistence/semantic layers before Guessing v3 captures the
# methods it wraps. The order-safe bootstrap also protects developer/test code
# that happened to import guessing_v3 earlier in the process.
install_run16_semantics()
install_run20_reporting()
install_warp_classification_v2()
from .guessing_v3_bootstrap import install_guessing_v3  # noqa: E402

install_guessing_v3()


# runner.py imports this module before progress.py. Install the exact-room-bounds
# exporter before EpisodeTracker captures its export helper. Install the newest
# screen analyzer before the explorer observes its first screenshot, then wrap
# that final analyzer so Guessing v3 can sample raw per-view anchors before the
# legacy memory stabilizer keeps only the clearest target geometry.
install_aligned_navigation_exporter()
install_run15_screen_region_analyzer()
from .guessing_v3_screen import install_guessing_v3_screen_observer  # noqa: E402

install_guessing_v3_screen_observer()

# Exit Detection v2 interprets the final Run15 analyzer output and uses Guessing
# v3 multi-view measurements. Entity Detection v2 must follow it so its belief
# wrapper preserves Exit v2's evidence calibration while weakening only
# one-sided entity semantics.
from .exit_detection_v2 import install_exit_detection_v2  # noqa: E402
from .exit_detection_v2_confirmation import (  # noqa: E402
    install_exit_detection_v2_confirmation,
)
from .exit_detection_v2_transition_guard import (  # noqa: E402
    install_exit_detection_v2_transition_guard,
)
from .entity_detection_v2 import install_entity_detection_v2  # noqa: E402

install_exit_detection_v2()
install_exit_detection_v2_confirmation()
install_exit_detection_v2_transition_guard()
install_entity_detection_v2()

# The adaptive observer wrapper rotates to an independent capture route after a
# usable bitmap repeats for an implausibly long streak. It is safe to install
# here because runner.py imports HierarchicalPolicy before ScreenObserver.
from .adaptive_capture import install_adaptive_capture_recovery  # noqa: E402

install_adaptive_capture_recovery()

# Navigation Coherence v1 sits above the completed Autonomy runtime. It keeps
# evidence-backed goals stable between decisions while preserving the lower
# collision, interaction, visual-evidence, and persistence contracts.
from .navigation_coherence import NavigationCoherenceExplorer  # noqa: E402
from .population_training import PopulationCoordinator  # noqa: E402


class HierarchicalPolicy:
    """Specialized reflex controllers wrapped around the learned explorer."""

    def __init__(
        self,
        seed: int = 0,
        memory_path: Path | None = None,
        *,
        training: PopulationCoordinator | None = None,
    ):
        if training is None:
            self.explorer = NavigationCoherenceExplorer(seed, memory_path)
        else:
            if memory_path is None:
                raise ValueError("Population training requires a staged memory path.")
            from .population_policy import PopulationTrainingExplorer

            self.explorer = PopulationTrainingExplorer(
                seed,
                memory_path,
                training,
            )
        self.training = training
        self.objectives = ObjectiveManager()
        self.dialogue = DialogueReader()
        self.battle = BattleController()
        self.visual_freshness = VisualFreshnessGuard()
        self.reason = "hierarchical policy starting"
        self.last_dialogue_signature: str | None = None
        self.last_dialogue_text: str | None = None
        self.last_visual_valid = True
        self._validated_step: int | None = None
        self._training_last_safe = False

    def __getattr__(self, name: str):
        return getattr(self.explorer, name)

    def validate_observation(
        self,
        observation: Observation,
        telemetry: TelemetrySample | None = None,
    ) -> Observation:
        """Apply capture freshness once before any visual subsystem consumes it."""
        if self._validated_step == observation.step:
            return replace(
                observation,
                visual_valid=self.last_visual_valid,
            )
        observation = self.visual_freshness.validate(observation, telemetry)
        self._validated_step = observation.step
        self.last_visual_valid = observation.visual_valid
        return observation

    def choose(
        self,
        observation: Observation,
        perception: Perception,
        telemetry: TelemetrySample | None = None,
    ) -> Action:
        observation = self.validate_observation(observation, telemetry)

        if perception.state is GameState.BATTLE:
            # The specialized controller owns the action, but the explorer
            # still owns interaction/story memory. Let it observe the battle
            # transition so an NPC-started encounter is not mislabeled flavor.
            self.explorer.choose(observation, perception, telemetry)
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
            if self.training is not None:
                self.training.record_shared_action(
                    action.name,
                    self.reason,
                    force=True,
                )
            room = self._room_name(telemetry)
            self.objectives.objective_for_state("battle", self.reason, room)
            return action

        action = self.explorer.choose(observation, perception, telemetry)
        self.reason = self.explorer.reason
        room = self._room_name(telemetry)

        if observation.visual_valid and perception.state in {
            GameState.DIALOGUE,
            GameState.MENU,
        }:
            reading = self.dialogue.analyze(observation.frame)
            self.last_dialogue_signature = reading.signature
            self.last_dialogue_text = reading.text
            detail = f"; visible option rows={reading.option_count}"
            if reading.text:
                detail += f"; OCR={reading.text[:100]!r}"
            self.reason += detail
        elif perception.state in {GameState.DIALOGUE, GameState.MENU}:
            self.reason += "; visual capture stale, skip dialogue analysis"

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
            self.objectives.current.kind.value
            if self.objectives.current
            else None
        )
        # History intentionally keeps only the newest 100 entries for compact
        # diagnostics. Report the independent counter so long runs do not look
        # artificially capped at exactly 100 objective changes.
        summary["objective_changes"] = self.objectives.change_count
        summary["objective_history_retained"] = len(self.objectives.history)
        summary["last_dialogue_signature"] = self.last_dialogue_signature
        summary["last_dialogue_text"] = self.last_dialogue_text
        summary["frozen_visual_frames"] = self.visual_freshness.frozen_frames
        return summary

    def observe_training_step(
        self,
        *,
        step: int,
        perception: Perception,
        telemetry: TelemetrySample | None,
        map_updates: list[dict[str, object]],
    ) -> None:
        if self.training is None:
            return
        room = self._room_name(telemetry)
        player_controlled = (
            telemetry.player_controlled if telemetry is not None else None
        )
        safe_overworld = (
            perception.state is GameState.OVERWORLD
            and telemetry is not None
            and telemetry.mode == "overworld"
            and player_controlled is not False
            and getattr(self.explorer, "active_goal_contract", None) is None
            and getattr(self.explorer, "active_interaction_key", None) is None
            and getattr(self.explorer, "interaction_candidate", None) is None
            and getattr(self.explorer, "pending_choice_record", None) is None
            and not getattr(self.explorer, "menu_action_queue", ())
        )
        self._training_last_safe = safe_overworld
        self.training.observe_step(
            step=step,
            state=perception.state.value,
            telemetry_present=telemetry is not None,
            room=room,
            player_controlled=player_controlled,
            reason=self.reason,
            map_updates=map_updates,
            safe_overworld=safe_overworld,
        )

    def commit_training_handoff(self) -> None:
        if self.training is not None:
            self.training.commit_handoff()

    def training_safe_to_stop(self) -> bool:
        return self.training is None or self._training_last_safe
