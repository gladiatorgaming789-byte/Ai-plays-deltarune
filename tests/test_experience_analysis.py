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


def test_analysis_reports_missing_and_underrepresented_actions(tmp_path: Path):
    transitions = [
        NeuralTransition(
            observation=(0.1, 0.2),
            available=(1.0, 1.0),
            action="right",
            reward=0.0,
            next_observation=(0.2, 0.2),
            next_available=(1.0, 1.0),
            done=False,
        )
        for _ in range(120)
    ]
    transitions.append(NeuralTransition(
        observation=(0.2, 0.2),
        available=(1.0, 0.0),
        action="confirm",
        reward=0.0,
        next_observation=(0.3, 0.2),
        next_available=(1.0, 0.0),
        done=False,
    ))
    path = tmp_path / "coverage.jsonl"
    write_transitions(path, transitions)

    report = analyze_dataset(path)

    assert report["action_counts"]["right"] == 120
    assert report["action_counts"]["confirm"] == 1
    assert "left" in report["missing_actions"]
    assert "confirm" in report["underrepresented_actions"]
    assert report["training_ready"] is False


def test_analysis_reports_named_feature_availability(tmp_path: Path):
    from deltarune_agent.neural_env import DEFAULT_ENCODER

    width = DEFAULT_ENCODER.width
    available = [1.0] * width
    available[0] = 0.0
    transition = NeuralTransition(
        observation=tuple(0.0 for _ in range(width)),
        available=tuple(available),
        action="wait",
        reward=0.0,
        next_observation=tuple(0.0 for _ in range(width)),
        next_available=tuple(available),
        done=False,
    )
    path = tmp_path / "features.jsonl"
    write_transitions(path, [transition])

    report = analyze_dataset(path)

    assert report["feature_availability_by_name"]["position.x"] == 0.0
    assert "position.x" in report["never_available_features"]
    assert report["feature_availability_fraction"] < 1.0


def test_analysis_handles_empty_dataset(tmp_path: Path):
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")

    report = analyze_dataset(path)

    assert report["transition_count"] == 0
    assert report["action_fractions"]["wait"] == 0.0
    assert report["training_ready"] is False


def test_analysis_blocks_current_next_width_mismatch(tmp_path: Path):
    transition = NeuralTransition(
        observation=(0.0, 0.5),
        available=(1.0, 1.0),
        action="wait",
        reward=0.0,
        next_observation=(0.1, 0.5, 0.2),
        next_available=(1.0, 1.0, 1.0),
        done=False,
    )
    path = tmp_path / "width-mismatch.jsonl"
    write_transitions(path, [transition])

    report = analyze_dataset(path)

    assert report["transition_width_mismatches"] == 1
    assert any("different current/next" in item for item in report["training_blockers"])
