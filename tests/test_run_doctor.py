import hashlib
import json
from pathlib import Path

from deltarune_agent.run_doctor import (
    DoctorThresholds,
    analyze_run,
    load_run,
    write_report,
)


def _event(step, room="room_a", action="right", valid=True, elapsed=None):
    return {
        "step": step,
        "elapsed_seconds": float(step) if elapsed is None else elapsed,
        "telemetry": {"room_name": room} if room is not None else None,
        "action": action,
        "visual_valid": valid,
        "reason": "test",
    }


def _write_run(path: Path, events):
    path.mkdir(parents=True)
    (path / "run.json").write_text(
        json.dumps({"agent_revision": "fixture-rev", "schema_version": 2}),
        encoding="utf-8",
    )
    (path / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    return path


def test_loader_tolerates_partial_and_malformed_jsonl(tmp_path: Path):
    run_dir = tmp_path / "run"
    _write_run(run_dir, [_event(0)])
    with (run_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write("not-json\n")
        stream.write("[]\n")
    run = load_run(run_dir)
    assert len(run.events) == 1
    assert run.agent_revision == "fixture-rev"
    assert len(run.warnings) == 2


def test_room_stall_and_repeated_action_findings(tmp_path: Path):
    events = [_event(step, room="classroom", action="right") for step in range(35)]
    run = load_run(_write_run(tmp_path / "run", events))
    report = analyze_run(
        run,
        DoctorThresholds(
            room_stall_steps=30,
            room_stall_seconds=1000,
            repeated_action_streak=10,
            rapid_return_steps=5,
            invalid_visual_streak=10,
            low_visual_valid_ratio=0.5,
            low_visual_min_events=10,
        ),
    )
    kinds = {finding.finding_type for finding in report.findings}
    assert "room_stall" in kinds
    assert "repeated_action_streak" in kinds


def test_rapid_a_b_a_return_is_detected(tmp_path: Path):
    events = [
        _event(0, "a"),
        _event(1, "a"),
        _event(2, "b"),
        _event(3, "b"),
        _event(4, "a"),
    ]
    report = analyze_run(
        load_run(_write_run(tmp_path / "run", events)),
        DoctorThresholds(
            room_stall_steps=100,
            room_stall_seconds=1000,
            repeated_action_streak=100,
            rapid_return_steps=3,
            invalid_visual_streak=100,
            low_visual_valid_ratio=0.1,
            low_visual_min_events=100,
        ),
    )
    finding = next(f for f in report.findings if f.finding_type == "rapid_room_return")
    assert finding.measured["from_room"] == "a"
    assert finding.measured["via_room"] == "b"
    assert finding.measured["return_steps"] == 2


def test_capture_ratio_and_long_invalid_streak_are_detected(tmp_path: Path):
    events = [_event(step, valid=step < 5) for step in range(45)]
    report = analyze_run(
        load_run(_write_run(tmp_path / "run", events)),
        DoctorThresholds(
            room_stall_steps=1000,
            room_stall_seconds=1000,
            repeated_action_streak=1000,
            rapid_return_steps=3,
            invalid_visual_streak=20,
            low_visual_valid_ratio=0.5,
            low_visual_min_events=20,
        ),
    )
    kinds = {finding.finding_type for finding in report.findings}
    assert "capture_validity_degradation" in kinds
    assert "invalid_visual_streak" in kinds


def test_report_is_deterministic(tmp_path: Path):
    run_dir = _write_run(tmp_path / "run", [_event(step) for step in range(20)])
    thresholds = DoctorThresholds(
        room_stall_steps=10,
        room_stall_seconds=1000,
        repeated_action_streak=10,
    )
    first = analyze_run(load_run(run_dir), thresholds).as_dict()
    second = analyze_run(load_run(run_dir), thresholds).as_dict()
    assert first == second


def test_writing_doctor_report_never_changes_existing_artifacts(tmp_path: Path):
    run_dir = _write_run(tmp_path / "run", [_event(step) for step in range(20)])
    before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in run_dir.iterdir()
        if path.is_file()
    }
    report = analyze_run(load_run(run_dir), DoctorThresholds(room_stall_steps=10))
    json_path, md_path = write_report(report)
    assert json_path.is_file()
    assert md_path.is_file()
    after = {
        name: hashlib.sha256((run_dir / name).read_bytes()).hexdigest()
        for name in before
    }
    assert after == before


def test_no_findings_for_short_healthy_fixture(tmp_path: Path):
    events = [
        _event(0, "a", "right", True),
        _event(1, "a", "down", True),
        _event(2, "b", "right", True),
        _event(3, "b", "up", True),
        _event(4, "c", "left", True),
    ]
    report = analyze_run(load_run(_write_run(tmp_path / "run", events)))
    assert report.finding_count == 0
