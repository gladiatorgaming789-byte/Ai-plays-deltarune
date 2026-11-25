"""Optional live controller for the temporal numerical neural policy.

This controller is opt-in and vision-free. It consumes only the numerical
telemetry observation contract and a fixed-length history. It never consults
the hierarchical policy, route tables, story text, or developer benchmarks.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque

from .actions import ACTIONS
from .neural_env import NeuralObservation, NeuralObservationEncoder, DEFAULT_ENCODER
from .neural_temporal import NeuralTemporalConfig, build_temporal_policy_network


@dataclass(frozen=True)
class NeuralDecision:
    action: str
    confidence: float
    value: float
    probabilities: dict[str, float]


class NeuralTemporalAgent:
    def __init__(self, model, device, *, sequence_length: int = 8):
        self.model = model
        self.device = device
        self.sequence_length = sequence_length
        self.encoder = NeuralObservationEncoder()
        self.history: Deque[NeuralObservation] = deque(maxlen=sequence_length)
        self.previous_action = "wait"
        self.previous_duration = 0.0

    @classmethod
    def load(cls, checkpoint: Path):
        import torch

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        payload = torch.load(checkpoint, map_location=device, weights_only=True)
        if not isinstance(payload, dict) or payload.get("format") != "deltarune-neural-temporal-policy-v1":
            raise ValueError("checkpoint is not a temporal neural policy checkpoint")
        raw = payload.get("temporal_config", {})
        config = NeuralTemporalConfig(
            sequence_length=int(raw.get("sequence_length", 8)),
            hidden_size=int(raw.get("hidden_size", 128)),
            layers=int(raw.get("layers", 1)),
            dropout=float(raw.get("dropout", 0.0)),
            epochs=1,
            batch_size=1,
        )
        input_width = int(payload["combined_input_width"])
        model = build_temporal_policy_network(input_width, config).to(device)
        model.load_state_dict(payload["model_state_dict"])
        model.eval()
        return cls(model, device, sequence_length=config.sequence_length)

    def observe(self, telemetry) -> NeuralObservation:
        return self.encoder.encode(
            telemetry,
            previous_action=self.previous_action,
            previous_action_duration=self.previous_duration,
        )

    def reset(self) -> None:
        self.history.clear()
        self.previous_action = "wait"
        self.previous_duration = 0.0

    def predict(self, observation: NeuralObservation) -> NeuralDecision:
        import torch

        self.history.append(observation)
        if len(self.history) < self.sequence_length:
            # Do not fabricate a sequence. Wait until enough real telemetry
            # observations exist for the recurrent model.
            return NeuralDecision("wait", 0.0, 0.0, {name: (1.0 if name == "wait" else 0.0) for name in ACTIONS})

        sequence = torch.tensor(
            [[
                value
                for item in self.history
                for value in (*item.values, *item.available)
            ]],
            dtype=torch.float32,
            device=self.device,
        ).reshape(1, self.sequence_length, DEFAULT_ENCODER.combined_width)

        with torch.inference_mode():
            output = self.model(sequence)
            probabilities = torch.softmax(output["policy_logits"], dim=-1)[0]
            index = int(probabilities.argmax().item())

        names = tuple(ACTIONS)
        decision = NeuralDecision(
            action=names[index],
            confidence=float(probabilities[index].item()),
            value=float(output["value"][0].item()),
            probabilities={name: float(probabilities[i].item()) for i, name in enumerate(names)},
        )
        self.previous_action = decision.action
        self.previous_duration = ACTIONS[decision.action].duration
        return decision
