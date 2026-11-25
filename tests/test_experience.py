from pathlib import Path
import random

from deltarune_agent.experience import (
    NeuralReplayBuffer,
    NeuralTransition,
    read_transitions,
    write_transitions,
)
from deltarune_agent.neural_env import NeuralObservation


def observation(sequence: int) -> NeuralObservation:
    return NeuralObservation(
        values=(0.1, 0.2),
        available=(1.0, 1.0),
        feature_names=("a", "b"),
        source_sequence=sequence,
        room_id=1,
        room_name="room",
    )


def transition(action="right", reward=1.0, done=False):
    return NeuralTransition.from_observations(
        observation(1),
        action,
        reward,
        observation(2),
        done=done,
    )


def test_transition_validates_action_and_width():
    item = transition()
    assert item.action == "right"
    assert item.reward == 1.0
    assert item.source_sequence == 1
    assert item.next_source_sequence == 2


def test_transition_rejects_unknown_action():
    try:
        transition(action="teleport")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown action was accepted")


def test_replay_buffer_is_bounded():
    buffer = NeuralReplayBuffer(capacity=2)
    buffer.add(transition(action="left"))
    buffer.add(transition(action="right"))
    buffer.add(transition(action="up"))

    assert len(buffer) == 2
    assert [item.action for item in buffer.snapshot()] == ["right", "up"]


def test_replay_sampling_is_reproducible():
    buffer = NeuralReplayBuffer(capacity=10)
    for action in ("up", "down", "left", "right"):
        buffer.add(transition(action=action))

    first = buffer.sample(3, rng=random.Random(123))
    second = buffer.sample(3, rng=random.Random(123))
    assert first == second


def test_transition_round_trip(tmp_path: Path):
    path = tmp_path / "transitions.jsonl"
    original = [transition(), transition(action="left", reward=-0.5, done=True)]

    write_transitions(path, original)
    loaded = read_transitions(path)

    assert loaded == original


def test_streaming_writer_appends_and_flushes(tmp_path: Path):
    from deltarune_agent.experience import NeuralExperienceWriter

    path = tmp_path / "stream.jsonl"
    writer = NeuralExperienceWriter(path)
    writer.write(transition())
    writer.write(transition(action="left"))
    assert writer.count == 2
    writer.close()
    loaded = read_transitions(path)
    assert [item.action for item in loaded] == ["right", "left"]


def test_transition_rejects_non_finite_values():
    from dataclasses import replace

    try:
        replace(transition(), reward=float("nan"))
    except ValueError:
        pass
    else:
        raise AssertionError("NaN reward was accepted")
