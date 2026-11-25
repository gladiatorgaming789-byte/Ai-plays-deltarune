import json

import pytest

torch = pytest.importorskip("torch")

from deltarune_agent.experience import (
    NeuralTransition,
    policy_samples_from_transitions,
    write_transitions,
)
from deltarune_agent.neural_env import DEFAULT_ENCODER
from deltarune_agent.neural_training import NeuralTrainingConfig, train_policy


def make_transition(action: str, value: float, sequence: int) -> NeuralTransition:
    width = DEFAULT_ENCODER.width
    observation = tuple(value for _ in range(width))
    available = tuple(1.0 for _ in range(width))
    next_observation = tuple(value + 0.01 for _ in range(width))
    return NeuralTransition(
        observation=observation,
        available=available,
        action=action,
        reward=0.0,
        next_observation=next_observation,
        next_available=available,
        done=False,
        source_sequence=sequence,
        next_source_sequence=sequence + 1,
    )


def test_policy_samples_ignore_reward():
    transition = make_transition("left", 0.25, 1)
    samples = policy_samples_from_transitions([transition])
    assert len(samples) == 1
    assert samples[0].action == "left"
    assert not hasattr(samples[0], "reward")


def test_training_creates_checkpoint_and_report(tmp_path):
    actions = ("left", "right", "up", "down")
    transitions = [
        make_transition(actions[index % len(actions)], float(index) / 100, index)
        for index in range(40)
    ]
    dataset = tmp_path / "dataset.jsonl"
    checkpoint = tmp_path / "policy.pt"
    write_transitions(dataset, transitions)

    report = train_policy(
        dataset,
        checkpoint,
        config=NeuralTrainingConfig(
            epochs=2,
            batch_size=8,
            learning_rate=1e-2,
            validation_fraction=0.2,
            device="cpu",
        ),
    )

    assert checkpoint.is_file()
    assert report["dataset_count"] == 40
    assert report["train_count"] == 32
    assert report["validation_count"] == 8
    assert report["validation_split"] == "chronological"

    payload = torch.load(checkpoint, map_location="cpu", weights_only=True)
    assert payload["format"] == "deltarune-neural-policy-v1"
    assert payload["encoder_width"] == DEFAULT_ENCODER.width
    assert payload["action_names"] == [
        "wait", "confirm", "cancel", "menu", "up", "down", "left", "right"
    ]


def test_training_rejects_empty_dataset(tmp_path):
    dataset = tmp_path / "empty.jsonl"
    dataset.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="no transitions"):
        train_policy(
            dataset,
            tmp_path / "policy.pt",
            config=NeuralTrainingConfig(device="cpu"),
        )
