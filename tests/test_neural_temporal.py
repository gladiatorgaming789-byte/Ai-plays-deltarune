import pytest

torch = pytest.importorskip("torch")

from deltarune_agent.experience import NeuralTransition, write_transitions
from deltarune_agent.neural_env import DEFAULT_ENCODER
from deltarune_agent.neural_temporal import (
    NeuralTemporalConfig,
    build_temporal_policy_network,
    sequence_samples_from_transitions,
)


def make_transition(action: str, sequence: int) -> NeuralTransition:
    width = DEFAULT_ENCODER.width
    values = tuple(float(sequence) / 10 for _ in range(width))
    available = tuple(1.0 for _ in range(width))
    return NeuralTransition(values, available, action, 0.0, values, available, False, sequence, sequence + 1)


def test_sequence_windows_are_contiguous():
    transitions = [make_transition("left", i) for i in range(10)]
    samples = sequence_samples_from_transitions(transitions, 4)
    assert len(samples) == 7
    assert len(samples[0][0]) == 4 * DEFAULT_ENCODER.combined_width


def test_sequence_gaps_are_not_bridged():
    transitions = [make_transition("left", i) for i in range(4)] + [make_transition("right", 10)]
    assert len(sequence_samples_from_transitions(transitions, 4)) == 1


def test_temporal_network_shapes():
    model = build_temporal_policy_network(DEFAULT_ENCODER.combined_width, NeuralTemporalConfig(sequence_length=4))
    x = torch.zeros(2, 4, DEFAULT_ENCODER.combined_width)
    output = model(x)
    assert output["policy_logits"].shape == (2, 8)
    assert output["value"].shape == (2,)


def test_temporal_config_validation():
    with pytest.raises(ValueError):
        NeuralTemporalConfig(sequence_length=1)
