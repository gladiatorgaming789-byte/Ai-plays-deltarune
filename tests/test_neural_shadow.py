import pytest

torch = pytest.importorskip("torch")

from deltarune_agent.neural_env import NeuralObservationEncoder, NeuralObservation
from deltarune_agent.neural_shadow import NeuralShadowAgent
from deltarune_agent.actions import ACTIONS


def test_shadow_agent_predicts_only_known_actions():
    agent = NeuralShadowAgent.create()
    encoder = NeuralObservationEncoder()
    observation = NeuralObservation(
        values=(0.0,) * encoder.width,
        available=(1.0,) * encoder.width,
        feature_names=encoder.feature_names,
        source_sequence=None,
        room_id=0,
        room_name="",
    )
    prediction = agent.predict(observation)
    assert prediction["action"] in {
        "wait", "confirm", "cancel", "menu", "up", "down", "left", "right",
    }
    assert 0.0 <= prediction["confidence"] <= 1.0
    assert pytest.approx(sum(prediction["probabilities"].values()), abs=1e-6) == 1.0


def test_shadow_model_is_not_training_mode():
    agent = NeuralShadowAgent.create()
    assert agent.model.training is False


def test_shadow_loads_supervised_checkpoint(tmp_path):
    from deltarune_agent.neural_policy import build_policy_value_network

    encoder = NeuralObservationEncoder()
    model = build_policy_value_network(encoder.combined_width)
    checkpoint = tmp_path / "policy.pt"
    torch.save(
        {
            "format": "deltarune-neural-policy-v1",
            "model_state_dict": model.state_dict(),
            "encoder_width": encoder.width,
            "combined_input_width": encoder.combined_width,
            "action_names": list(ACTIONS),
            "policy_config": {
                "hidden_sizes": [128, 128],
                "dropout": 0.0,
            },
        },
        checkpoint,
    )
    agent = NeuralShadowAgent.create(checkpoint=checkpoint)
    assert agent.model.training is False
