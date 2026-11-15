import json

import pytest

from deltarune_agent.tested_exit_dataset import (
    ExitAdjudication,
    build_adjudication_template,
    build_tested_exit_dataset,
    load_adjudications,
    write_dataset_artifacts,
)
from deltarune_agent.visual_exit_benchmark import VisualExitCandidate


def _candidate(candidate_id="door-1"):
    return VisualExitCandidate(
        candidate_id=candidate_id,
        room="room_a",
        region=(1, 2),
        step=5,
        state="visual_candidate",
        source="doorway_facade",
        score=0.8,
        anchor_x=100.0,
        anchor_y=120.0,
        state_history=((5, "visual_candidate"),),
    )


def test_template_defaults_every_candidate_to_unknown():
    template = build_adjudication_template([_candidate()])
    assert template == [{
        "candidate_id": "door-1",
        "status": "unknown",
        "evidence": "",
        "tested_step": None,
        "reviewer": "",
    }]


def test_dataset_only_uses_explicit_supervised_labels():
    candidates = [_candidate("exit"), _candidate("not-exit"), _candidate("untested"), _candidate("unreachable")]
    labels = [
        ExitAdjudication("exit", "confirmed_exit", "Crossed and room_b loaded.", 20, "reviewer"),
        ExitAdjudication("not-exit", "tested_non_exit", "Approached and interacted; no transition.", 30, "reviewer"),
        ExitAdjudication("unreachable", "unreachable", "Blocked by geometry.", 40, "reviewer"),
    ]

    rows, summary = build_tested_exit_dataset(candidates, labels)
    by_id = {row["candidate_id"]: row for row in rows}

    assert by_id["exit"]["supervised_example"] is True
    assert by_id["not-exit"]["supervised_example"] is True
    assert by_id["untested"]["label"] == "unknown"
    assert by_id["untested"]["supervised_example"] is False
    assert by_id["unreachable"]["supervised_example"] is False
    assert summary["supervised_example_count"] == 2
    assert summary["negative_labels_inferred_from_absence"] is False


def test_supervised_labels_require_evidence_and_valid_status(tmp_path):
    path = tmp_path / "labels.jsonl"
    path.write_text(json.dumps({
        "candidate_id": "door-1",
        "status": "tested_non_exit",
        "evidence": "",
    }) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="requires explicit evidence"):
        load_adjudications(path)

    path.write_text(json.dumps({
        "candidate_id": "door-1",
        "status": "guess",
        "evidence": "maybe",
    }) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported status"):
        load_adjudications(path)


def test_unknown_missing_file_means_no_adjudications(tmp_path):
    assert load_adjudications(tmp_path / "missing.jsonl") == []


def test_dataset_rejects_unknown_and_duplicate_candidate_ids():
    with pytest.raises(ValueError, match="unknown candidates"):
        build_tested_exit_dataset(
            [_candidate()],
            [ExitAdjudication("not-in-run", "confirmed_exit", "Evidence")],
        )

    with pytest.raises(ValueError, match="Duplicate adjudications"):
        build_tested_exit_dataset(
            [_candidate()],
            [
                ExitAdjudication("door-1"),
                ExitAdjudication("door-1", "unknown"),
            ],
        )


def test_writer_preserves_existing_adjudication_template(tmp_path):
    candidate = _candidate()
    first = write_dataset_artifacts(tmp_path, [candidate], [], {"candidate_count": 1})
    original = first["template"].read_text(encoding="utf-8")
    first["template"].write_text('{"candidate_id":"door-1","status":"tested_non_exit"}\n', encoding="utf-8")
    write_dataset_artifacts(tmp_path, [candidate], [], {"candidate_count": 1})
    assert first["template"].read_text(encoding="utf-8") != original
    dataset = first["dataset"].read_text(encoding="utf-8")
    assert dataset == ""
