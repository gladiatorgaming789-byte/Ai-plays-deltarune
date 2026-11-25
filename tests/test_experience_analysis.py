from pathlib import Path

from deltarune_agent.experience import NeuralTransition, write_transitions
from deltarune_agent.experience_analysis import analyze_dataset


def test_dataset_analysis_reports_quality_and_unassigned_rewards(tmp_path: Path):
    first = NeuralTransition(
        observation=(0.0, 0.5),
        available=(1.0, 0.0),
        action="right",
        reward=0.0,
        next_observation=(0.1, 0.5),
        next_available=(1.0, 1.0),
        done=False,
        source_sequence=10,
        next_source_sequence=12,
    )
    second = NeuralTransition(
        observation=(0.0, 0.5),
        available=(1.0, 0.0),
        action="right",
        reward=0.0,
        next_observation=(0.1, 0.5),
        next_available=(1.0, 1.0),
        done=False,
        source_sequence=12,
        next_source_sequence=13,
    )
    path = tmp_path / "dataset.jsonl"
    write_transitions(path, [first, second])

    report = analyze_dataset(path)

    assert report["transition_count"] == 2
    assert report["action_counts"] == {"right": 2}
    assert report["duplicate_observation_count"] == 1
    assert report["sequence_gap_pairs"] == 1
    assert report["reward_status"] == "unassigned"
    assert report["training_ready"] is False
