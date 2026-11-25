"""Small policy/value neural network for the Deltarune agent.

The network is intentionally only a function of the Phase 1 numerical
observation vector. It contains no game-specific route logic and no vision
path. The model is suitable for supervised warm-starts, shadow inference, and
later policy-gradient reinforcement learning.
"""

from __future__ import annotations

from dataclasses import dataclass

from .actions import ACTIONS
from .neural_env import NeuralObservation


@dataclass(frozen=True)
class NeuralPolicyConfig:
    hidden_sizes: tuple[int, ...] = (128, 128)
    dropout: float = 0.0


def action_names() -> tuple[str, ...]:
    return tuple(ACTIONS.keys())


def observation_tensor(observation: NeuralObservation):
    """Convert an observation to a torch tensor without importing torch globally."""
    import torch
    return torch.as_tensor(observation.combined(), dtype=torch.float32)


def build_policy_value_network(input_width: int, config: NeuralPolicyConfig | None = None):
    """Build a policy + value MLP."""
    import torch.nn as nn

    if input_width <= 0:
        raise ValueError("input_width must be positive")
    config = config or NeuralPolicyConfig()
    if not config.hidden_sizes or any(size <= 0 for size in config.hidden_sizes):
        raise ValueError("hidden_sizes must contain positive widths")
    if not 0.0 <= config.dropout < 1.0:
        raise ValueError("dropout must be in [0, 1)")

    layers: list[nn.Module] = []
    width = input_width
    for hidden in config.hidden_sizes:
        layers.extend((nn.Linear(width, hidden), nn.Tanh()))
        if config.dropout:
            layers.append(nn.Dropout(config.dropout))
        width = hidden

    encoder = nn.Sequential(*layers)

    class PolicyValueNetwork(nn.Module):
        def __init__(self):
            super().__init__()
            self.encoder = encoder
            self.policy_head = nn.Linear(width, len(ACTIONS))
            self.value_head = nn.Linear(width, 1)

        def forward(self, x):
            features = self.encoder(x)
            return {
                "policy_logits": self.policy_head(features),
                "value": self.value_head(features).squeeze(-1),
            }

    return PolicyValueNetwork()


def build_policy_from_observation(observation: NeuralObservation, config: NeuralPolicyConfig | None = None):
    """Construct a network whose input width comes from the environment contract."""
    return build_policy_value_network(len(observation.combined()), config)
