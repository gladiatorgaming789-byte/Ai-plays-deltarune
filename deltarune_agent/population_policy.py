from __future__ import annotations

from pathlib import Path

from .autonomy_v1 import AutonomyOption
from .navigation_coherence import NavigationCoherenceExplorer
from .population_training import PopulationCoordinator


class PopulationTrainingExplorer(NavigationCoherenceExplorer):
    """Navigation explorer whose selected scorer is owned by one candidate."""

    def __init__(
        self,
        seed: int,
        memory_path: Path,
        coordinator: PopulationCoordinator,
    ) -> None:
        self.training = coordinator
        self._population_legal_options: dict[str, AutonomyOption] = {}
        super().__init__(seed, memory_path)
        coordinator.bind_explorer(self)

    def _population_reinforcement_key(self, option: AutonomyOption) -> str:
        if option.kind == "retry_interaction" and option.budget_key:
            return option.budget_key
        if option.kind in {
            "learned_warp",
            "controlled_backtrack",
            "long_horizon_route",
        }:
            warp = option.metadata.get("warp")
            if isinstance(warp, tuple) and len(warp) == 7:
                portal_id = self.world.portal_id_for_warp(warp, create=False)
                if portal_id:
                    return self._portal_key(portal_id)
        room = str(getattr(self, "observed_room", "") or getattr(self, "last_room", "") or "unknown")
        mode = {
            "frontier_cluster": "frontier_exploration",
            "semantic_entity": "interaction_search",
            "weak_entity_test": "interaction_search",
            "semantic_exit": "exit_search",
            "geometry_exit_test": "exit_search",
            "information_probe": "local_search",
            "broad_reset": "local_search",
        }.get(option.kind)
        if mode:
            return f"mode:{room}:{mode}"
        return f"option:{option.kind}:{option.option_id}"

    def _score_option(self, option: AutonomyOption) -> float:
        option._population_reinforcement_key = (  # type: ignore[attr-defined]
            self._population_reinforcement_key(option)
        )
        budget_fraction = 0.0
        if option.budget_key is not None and option.budget_limit > 0:
            state = self._ensure_budget(
                option.budget_key,
                option.fingerprint,
                option.budget_limit,
            )
            option.budget_spent = state.spent
            option.budget_remaining = state.remaining
            if state.remaining <= 0:
                return float("-inf")
            budget_fraction = state.spent / max(1, state.limit)
        option.score = self.training.score_option(
            option,
            budget_fraction=budget_fraction,
        )
        self._population_legal_options[option.option_id] = option
        return option.score

    def choose(self, observation, perception, telemetry=None):
        self._population_legal_options = {}
        action = super().choose(observation, perception, telemetry)
        if self._population_legal_options:
            self.training.record_legal_options(
                tuple(self._population_legal_options.values())
            )
        else:
            # Specialized battle/dialogue and ordinary exploration are shared
            # controllers. All heads therefore make the same recommendation.
            self.training.record_shared_action(action.name, self.reason)
        return action

    def prediction_snapshot(self) -> dict[str, object]:
        snapshot = super().prediction_snapshot()
        snapshot["training"] = self.training.snapshot()
        return snapshot

    def save_memory(self) -> None:
        super().save_memory()
        self.training.flush_candidates()

    def summary(self) -> dict:
        summary = super().summary()
        summary["population_training"] = self.training.snapshot()
        return summary


__all__ = ["PopulationTrainingExplorer"]
