import pytest

torch = pytest.importorskip("torch")

from deltarune_agent.neural_env import NeuralObservationEncoder, NeuralObservation
from deltarune_agent.neural_shadow import NeuralShadowAgent


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
