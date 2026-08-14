import json
from pathlib import Path

from deltarune_agent import run_doctor_calibration, run_doctor_release
from deltarune_agent.run_doctor import EvidenceRange, RunDoctorFinding


def _write_run(path: Path, events, *, revision="calibration-fixture", summary=None):
    path.mkdir(parents=True)
    (path / "run.json").write_text(
        json.dumps({"agent_revision": revision, "schema_version": 2}),
        encoding="utf-8",
    )
    (path / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    if summary is not None:
        (path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return path


def _event(step, *, action, x=0, y=0, state="overworld", reason="fixture", room="room_a"):
    return {
        "step": step,
        "elapsed_seconds": step / 10,
        "state": state,
        "telemetry": {"room_name": room, "player_x": x, "player_y": y},
        "action": action,
        "visual_valid": True,
        "reason": reason,
    }


def _kinds(report):
    return {finding.finding_type for finding in report.base.findings}


def test_productive_sustained_movement_is_not_reported_as_repetition(tmp_path: Path):
    events = [
        _event(step, action="right", x=step * 4, reason="continue clear path right")
        for step in range(25)
    ]
    report, _ = run_doctor_release.analyze_directory(_write_run(tmp_path / "run", events))
    assert "repeated_action_streak" not in _kinds(report)
    assert "unproductive_repeated_action_streak" not in _kinds(report)


def test_control_lock_wait_and_dialogue_confirm_are_not_reported_as_loops(tmp_path: Path):
    events = [
        _event(
            step,
            action="wait",
            state="cutscene",
            reason="transition control locked; release movement until control returns",
        )
        for step in range(20)
    ]
    events += [
        _event(
            step,
            action="confirm",
            state="dialogue",
            reason="advance dialogue; visible option rows=2",
        )
        for step in range(20, 45)
    ]
    report, _ = run_doctor_release.analyze_directory(_write_run(tmp_path / "run", events))
    assert "repeated_action_streak" not in _kinds(report)
    assert "unproductive_repeated_action_streak" not in _kinds(report)


def test_blocked_repeated_movement_remains_a_finding(tmp_path: Path):
    events = [
        _event(step, action="right", x=100, y=100, reason="route toward target")
        for step in range(20)
    ]
    report, _ = run_doctor_release.analyze_directory(_write_run(tmp_path / "run", events))
    finding = next(
        finding
        for finding in report.base.findings
        if finding.finding_type == "unproductive_repeated_action_streak"
    )
    assert finding.measured["path_distance"] == 0.0
    assert finding.measured["net_displacement"] == 0.0


def test_completed_long_room_residence_is_downgraded_from_stall(tmp_path: Path):
    events = []
    for step in range(650):
        events.append(
            _event(
                step,
                action="right" if step % 2 == 0 else "down",
                x=step % 100,
                y=(step // 100) % 10,
                reason="mapped exploration",
                room="room_a",
            )
        )
    events.append(_event(650, action="right", x=0, y=0, room="room_b"))
    report, _ = run_doctor_release.analyze_directory(_write_run(tmp_path / "run", events))
    finding = next(
        finding
        for finding in report.base.findings
        if finding.finding_type == "room_stall" and finding.room == "room_a"
    )
    assert finding.severity == "medium"
    assert finding.measured["eventually_exited_room"] is True


def test_historical_objective_cap_is_marked_uncertain(tmp_path: Path):
    events = [
        _event(step, action="right" if step % 2 == 0 else "left", x=step % 5)
        for step in range(120)
    ]
    run_dir = _write_run(
        tmp_path / "run",
        events,
        revision="run20-first-cleaned-run-fixes-v1",
        summary={"objective_changes": 100},
    )
    report, _ = run_doctor_release.analyze_directory(run_dir)
    finding = next(f for f in report.base.findings if f.finding_type == "objective_churn")
    assert finding.measured["historical_counter_may_be_capped"] is True
    assert any("cap at 100" in uncertainty for uncertainty in finding.uncertainties)


def _finding(finding_id, *, room, start, end):
    return RunDoctorFinding(
        finding_id=finding_id,
        finding_type="fixture",
        title=finding_id,
        severity="high",
        confidence=0.9,
        subsystem="planning/evidence utilization",
        explanation="fixture",
        recommendation="fixture",
        evidence=EvidenceRange(start, end),
        room=room,
    )


def test_run_level_finding_does_not_bridge_unrelated_room_incidents():
    incidents = run_doctor_calibration.group_findings(
        [
            _finding("global", room=None, start=0, end=1000),
            _finding("room-a", room="room_a", start=10, end=20),
            _finding("room-b", room="room_b", start=500, end=510),
        ]
    )
    assert len(incidents) == 3
    assert {tuple(incident.finding_ids) for incident in incidents} == {
        ("global",),
        ("room-a",),
        ("room-b",),
    }
