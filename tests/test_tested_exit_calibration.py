import json

import pytest

from deltarune_agent.tested_exit_calibration import (
    calibrate_tested_exit_dataset,
    load_tested_exit_dataset,
    write_tested_exit_calibration,
)


def _row(candidate_id, room, score, label, source="doorway_facade"):
    return {
        "candidate_id": candidate_id,
        "room": room,
        "visual_score": score,
        "label": label,
        "source": source,
    }


def test_calibration_uses_only_explicit_positive_and_negative_labels():
    rows = [
        _row("a", "room_a", 0.9, "confirmed_exit"),
        _row("b", "room_a", 0.2, "tested_non_exit"),
        _row("c", "room_b", 0.8, "unknown"),
        _row("d", "room_b", 0.1, "unreachable"),
    ]
    report = calibrate_tested_exit_dataset(rows)

    assert report["calibration_status"] == "complete"
    assert report["supervised_example_count"] == 2
    assert report["excluded_unlabeled_count"] == 2
    assert report["overall_metrics"]["true_positives"] == 1
    assert report["overall_metrics"]["true_negatives"] == 1
    assert report["model_trained"] is False
    assert report["gameplay_thresholds_modified"] is False


def test_calibration_reports_insufficient_labels_without_inventing_negatives():
    report = calibrate_tested_exit_dataset([
        _row("a", "room_a", 0.9, "confirmed_exit"),
        _row("b", "room_b", 0.2, "unknown"),
    ])
    assert report["calibration_status"] == "insufficient_supervised_data"
    assert report["selected_threshold"] is None
    assert report["negative_example_count"] == 0


def test_leave_one_room_out_selects_threshold_without_held_out_room():
    rows = [
        _row("a1", "room_a", 0.9, "confirmed_exit"),
        _row("a2", "room_a", 0.1, "tested_non_exit"),
        _row("b1", "room_b", 0.8, "confirmed_exit"),
        _row("b2", "room_b", 0.2, "tested_non_exit"),
        _row("c1", "room_c", 0.7, "confirmed_exit"),
        _row("c2", "room_c", 0.3, "tested_non_exit"),
    ]
    report = calibrate_tested_exit_dataset(rows)
    validation = report["leave_one_room_out"]

    assert validation["status"] == "complete"
    assert validation["fold_count"] == 3
    # The held-out room_c positive scores below the threshold learned
    # from room_a and room_b. This is a genuine cross-room false negative,
    # not a reason to leak held-out labels into threshold selection.
    assert validation["aggregate_metrics"]["true_positives"] == 2
    assert validation["aggregate_metrics"]["true_negatives"] == 3
    assert validation["aggregate_metrics"]["false_negatives"] == 1
    assert validation["aggregate_metrics"]["recall"] == pytest.approx(2 / 3)
    assert all(fold["testing_example_count"] == 2 for fold in validation["folds"])


def test_leave_one_room_out_skips_fold_when_training_rooms_lack_both_classes():
    rows = [
        _row("a1", "room_a", 0.9, "confirmed_exit"),
        _row("b1", "room_b", 0.1, "tested_non_exit"),
    ]
    report = calibrate_tested_exit_dataset(rows)
    validation = report["leave_one_room_out"]
    assert validation["status"] == "insufficient_training_classes"
    assert validation["fold_count"] == 0
    assert len(validation["skipped_folds"]) == 2


def test_invalid_supervised_scores_and_duplicate_ids_are_rejected():
    with pytest.raises(ValueError, match="visual_score"):
        calibrate_tested_exit_dataset([
            _row("a", "room_a", None, "confirmed_exit"),
            _row("b", "room_b", 0.2, "tested_non_exit"),
        ])
    with pytest.raises(ValueError, match="Duplicate candidate_id"):
        calibrate_tested_exit_dataset([
            _row("a", "room_a", 0.9, "confirmed_exit"),
            _row("a", "room_b", 0.2, "tested_non_exit"),
        ])


def test_dataset_loader_and_report_writer(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        json.dumps(_row("a", "room_a", 0.9, "confirmed_exit")) + "\n"
        + json.dumps(_row("b", "room_a", 0.2, "tested_non_exit")) + "\n",
        encoding="utf-8",
    )
    rows = load_tested_exit_dataset(dataset)
    report = calibrate_tested_exit_dataset(rows)
    output = write_tested_exit_calibration(tmp_path / "calibration.json", report)
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["calibration_version"] == 1
    assert saved["gameplay_thresholds_modified"] is False
