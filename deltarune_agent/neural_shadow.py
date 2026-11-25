"""Non-authoritative neural shadow runner.

The shadow agent observes telemetry, runs the neural policy, and records what
it would have done. It never sends input and therefore cannot take control
away from the existing gameplay policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

from .actions import ACTIONS
from .experience import NeuralTransition, write_transitions
from .neural_env import NeuralObservation, NeuralObservationEncoder
from .neural_policy import NeuralPolicyConfig, build_policy_value_network, observation_tensor


@dataclass
class NeuralShadowAgent:
    encoder: NeuralObservationEncoder
    model: object
    device: object
    previous_action: str = "wait"
    previous_duration: float = 0.0

    @classmethod
    def create(cls, *, checkpoint: Path | None = None):
        import torch

        encoder = NeuralObservationEncoder()
        model = build_policy_value_network(
            encoder.combined_width,
            NeuralPolicyConfig(),
        )
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model.to(device)
        if checkpoint is not None:
            state = torch.load(checkpoint, map_location=device, weights_only=True)
            model.load_state_dict(state)
        model.eval()
        return cls(encoder, model, device)

    def observe(self, telemetry) -> NeuralObservation:
        return self.encoder.encode(
            telemetry,
            previous_action=self.previous_action,
            previous_action_duration=self.previous_duration,
        )

    def predict(self, observation: NeuralObservation) -> dict[str, object]:
        import torch

        with torch.inference_mode():
            output = self.model(observation_tensor(observation).unsqueeze(0).to(self.device))
            probabilities = torch.softmax(output["policy_logits"], dim=-1)[0]
            index = int(torch.argmax(probabilities).item())
            actions = tuple(ACTIONS.keys())
            return {
                "action": actions[index],
                "confidence": float(probabilities[index].item()),
                "value": float(output["value"][0].item()),
                "probabilities": {
                    name: float(probabilities[i].item())
                    for i, name in enumerate(actions)
                },
            }

    def make_transition(
        self,
        observation: NeuralObservation,
        action: str,
        reward: float,
        next_observation: NeuralObservation,
        *,
        done: bool = False,
    ) -> NeuralTransition:
        self.previous_action = action
        return NeuralTransition.from_observations(
            observation,
            action,
            reward,
            next_observation,
            done=done,
        )


class NeuralShadowWriter:
    """Append-only JSONL writer for neural shadow predictions/transitions."""

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.predictions_path = directory / "neural_shadow_predictions.jsonl"
        self.transitions_path = directory / "neural_experiences.jsonl"

    def write_prediction(self, *, step: int, prediction: dict[str, object]) -> None:
        self._append(self.predictions_path, {"step": step, **prediction})

    def write_transition(self, transition: NeuralTransition) -> None:
        self._append(self.transitions_path, transition.as_dict())

    @staticmethod
    def _append(path: Path, payload: dict[str, object]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, separators=(",", ":")) + "\n")
