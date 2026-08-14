import json
from pathlib import Path

from deltarune_agent import run_doctor_calibration_v102, run_doctor_release
from deltarune_agent.run_doctor import EvidenceRange, RunDoctorFinding


def _event(step, *, room="room_a", reason="explore", action="right", visual_valid=True):
    return {
        "step": step,
        "elapsed_seconds": step / 10,
        "state": "overworld",
        "telemetry": {"room_name": room, "x": step, "y": 0},
        "action": action,
        "visual_valid": visual_valid,
        "reason": reason,
    }


def _write_run(
    path: Path,
    events,
    *,
    navigation=None,
    navigation_updates=None,
    speed=None,
    telemetry=None,
):
    path.mkdir(parents=True)
    (path / "run.json").write_text(
        json.dumps({"agent_revision": "v102-fixture", "schema_version": 2}),
        encoding="utf-8",
    )
    (path / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    if navigation is not None:
        (path / "navigation.json").write_text(json.dumps(navigation), encoding="utf-8")
    if navigation_updates is not None:
        (path / "navigation_updates.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in navigation_updates),
            encoding="utf-8",
        )
    if speed is not None:
        (path / "speed_diagnostics.json").write_text(json.dumps(speed), encoding="utf-8")
    if telemetry is not None:
        (path / "telemetry_diagnostics.json").write_text(
            json.dumps(telemetry), encoding="utf-8"
        )
    return path


def _finding_types(report):
    return [finding.finding_type for finding in report.base.findings]


def _screen_update(step, room, region, *, state="proposed", hypothesis="possible_interactable"):
    return {
        "step": step,
        "update": {
            "type": "screen_region",
            "room": room,
            "region": list(region),
            "hypothesis": hypothesis,
            "guess_state": state,
            "completed_tests": 0,
            "inspections": 0,
        },
    }


def test_final_snapshot_evidence_from_other_room_does_not_match_blind_search(tmp_path: Path):
    events = [
        _event(
            step,
            room="room_a",
            reason="no reachable frontier; probe right" if 20 <= step <= 30 else "explore",
        )
        for step in range(40)
    ]
    run_dir = _write_run(
        tmp_path / "run",
        events,
        navigation={
            "screen_regions": [
                {
                    "room": "room_b",
                    "region_x": 4,
                    "region_y": 5,
                    "hypothesis": "possible_interactable",
                    "guess_state": "proposed",
                    "completed_tests": 0,
                }
            ]
        },
        navigation_updates=[_screen_update(10, "room_b", (4, 5))],
    )
    report, _ = run_doctor_release.analyze_directory(run_dir)
    assert "unconsumed_observed_evidence" not in _finding_types(report)


def test_future_same_room_evidence_is_not_backdated_into_earlier_blind_search(tmp_path: Path):
    events = [
        _event(
            step,
            reason="no reachable frontier; probe right" if 20 <= step <= 30 else "explore",
        )
        for step in range(60)
    ]
    run_dir = _write_run(
        tmp_path / "run",
        events,
        navigation={
            "screen_regions": [
                {
                    "room": "room_a",
                    "region_x": 6,
                    "region_y": 4,
                    "hypothesis": "possible_interactable",
                    "guess_state": "proposed",
                    "completed_tests": 0,
                }
            ]
        },
        navigation_updates=[_screen_update(40, "room_a", (6, 4))],
    )
    report, _ = run_doctor_release.analyze_directory(run_dir)
    assert "unconsumed_observed_evidence" not in _finding_types(report)


def test_actionable_same_room_evidence_requires_repeated_overlap_and_is_precise(tmp_path: Path):
    events = [
        _event(
            step,
            reason="no reachable frontier; probe right" if 20 <= step <= 25 else "explore",
        )
        for step in range(40)
    ]
    run_dir = _write_run(
        tmp_path / "run",
        events,
        navigation={"screen_regions": []},
        navigation_updates=[_screen_update(10, "room_a", (6, 4))],
    )
    report, _ = run_doctor_release.analyze_directory(run_dir)
    finding = next(
        finding
        for finding in report.base.findings
        if finding.finding_type == "unconsumed_observed_evidence"
    )
    assert finding.room == "room_a"
    assert finding.evidence.start_step == 20
    assert finding.evidence.end_step == 25
    assert finding.severity == "medium"
    assert finding.measured["blind_probe_steps_with_actionable_evidence"] == 6
    assert finding.measured["longest_consecutive_overlap"] == 6


def test_cooldown_evidence_is_not_treated_as_currently_actionable(tmp_path: Path):
    events = [
        _event(
            step,
            reason="no reachable frontier; probe right" if 20 <= step <= 30 else "explore",
        )
        for step in range(40)
    ]
    run_dir = _write_run(
        tmp_path / "run",
        events,
        navigation={"screen_regions": []},
        navigation_updates=[_screen_update(10, "room_a", (6, 4), state="cooldown")],
    )
    report, _ = run_doctor_release.analyze_directory(run_dir)
    assert "unconsumed_observed_evidence" not in _finding_types(report)


def test_known_warp_learned_before_terminal_reentry_is_reported_when_never_selected(tmp_path: Path):
    events = []
    for step in range(30):
        events.append(_event(step, room="room_a"))
    for step in range(30, 50):
        events.append(_event(step, room="room_b"))
    for step in range(50, 400):
        events.append(_event(step, room="room_a", action="left" if step % 2 else "right"))
    updates = [
        {
            "step": 30,
            "update": {
                "type": "warp",
                "from_room": "room_a",
                "from_cell": [10, 10],
                "action": "down",
                "to_room": "room_b",
            },
        }
    ]
    run_dir = _write_run(tmp_path / "run", events, navigation_updates=updates)
    report, _ = run_doctor_release.analyze_directory(run_dir)
    finding = next(
        finding
        for finding in report.base.findings
        if finding.finding_type == "known_warp_underused_during_stall"
    )
    assert finding.room == "room_a"
    assert finding.evidence.start_step == 50
    assert finding.measured["known_warps_available_before_stall"] == 1
    assert finding.measured["known_warp_destinations"] == ["room_b"]
    assert finding.measured["selected_learned_warp_steps"] == 0
    assert finding.severity == "high"


def test_known_warp_underuse_is_not_reported_when_policy_selected_learned_warp(tmp_path: Path):
    events = []
    for step in range(30):
        events.append(_event(step, room="room_a"))
    for step in range(30, 50):
        events.append(_event(step, room="room_b"))
    for step in range(50, 400):
        reason = "follow learned warp to room_b" if step == 200 else "explore"
        events.append(_event(step, room="room_a", reason=reason))
    updates = [
        {
            "step": 30,
            "update": {
                "type": "warp",
                "from_room": "room_a",
                "from_cell": [10, 10],
                "action": "down",
                "to_room": "room_b",
            },
        }
    ]
    run_dir = _write_run(tmp_path / "run", events, navigation_updates=updates)
    report, _ = run_doctor_release.analyze_directory(run_dir)
    assert "known_warp_underused_during_stall" not in _finding_types(report)


def test_explicit_unverified_high_speed_without_drspeed_is_high_severity(tmp_path: Path):
    events = [_event(step) for step in range(20)]
    run_dir = _write_run(
        tmp_path / "run",
        events,
        speed={
            "requested": "10",
            "detected_multiplier": None,
            "source": "manual",
            "verification_state": "unverified",
            "synchronized": False,
        },
        telemetry={
            "received_packets": 100,
            "valid_packets": 100,
            "invalid_packets": 0,
            "speed_packets": 0,
        },
    )
    report, _ = run_doctor_release.analyze_directory(run_dir)
    finding = next(
        finding
        for finding in report.base.findings
        if finding.finding_type == "speed_verification_problem"
    )
    assert finding.severity == "high"
    assert finding.measured["requested_multiplier"] == 10.0
    assert finding.measured["speed_packets"] == 0


def _global_finding(finding_id, subsystem):
    return RunDoctorFinding(
        finding_id=finding_id,
        finding_type=finding_id,
        title=finding_id,
        severity="high",
        confidence=0.9,
        subsystem=subsystem,
        explanation="fixture",
        recommendation="fixture",
        evidence=EvidenceRange(0, 1000),
        room=None,
    )


def test_unrelated_global_findings_do_not_group_by_whole_run_overlap():
    incidents = run_doctor_calibration_v102.group_findings(
        [
            _global_finding("capture", "capture/perception"),
            _global_finding("objective", "planning/objectives"),
        ]
    )
    assert len(incidents) == 2
    assert {tuple(incident.finding_ids) for incident in incidents} == {
        ("capture",),
        ("objective",),
    }
