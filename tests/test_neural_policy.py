import pytest

torch = pytest.importorskip("torch")

from deltarune_agent.neural_env import NeuralObservationEncoder
from deltarune_agent.neural_policy import (
    NeuralPolicyConfig,
    action_names,
    build_policy_from_observation,
    build_policy_value_network,
)


def test_action_order_matches_game_actions():
    assert action_names() == ("wait", "confirm", "cancel", "menu", "up", "down", "left", "right")


def test_policy_has_policy_and_value_heads():
    encoder = NeuralObservationEncoder()
    model = build_policy_value_network(encoder.combined_width)
    output = model(torch.zeros(4, encoder.combined_width))
    assert output["policy_logits"].shape == (4, len(action_names()))
    assert output["value"].shape == (4,)
    assert torch.isfinite(output["policy_logits"]).all()
    assert torch.isfinite(output["value"]).all()


def test_policy_from_observation_uses_environment_width():
    encoder = NeuralObservationEncoder()
    from deltarune_agent.neural_env import NeuralObservation
    observation = NeuralObservation(
        values=(0.0,) * encoder.width,
        available=(1.0,) * encoder.width,
        feature_names=encoder.feature_names,
        source_sequence=None,
        room_id=0,
        room_name="",
    )
    model = build_policy_from_observation(observation)
    assert model(torch.zeros(1, encoder.combined_width))["policy_logits"].shape == (1, len(action_names()))


def test_policy_config_rejects_invalid_values():
    with pytest.raises(ValueError):
        build_policy_value_network(10, NeuralPolicyConfig(hidden_sizes=()))
    with pytest.raises(ValueError):
        build_policy_value_network(10, NeuralPolicyConfig(hidden_sizes=(0,)))
    with pytest.raises(ValueError):
        build_policy_value_network(10, NeuralPolicyConfig(dropout=1.0))
