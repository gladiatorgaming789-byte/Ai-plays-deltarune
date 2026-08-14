import json
from pathlib import Path

from deltarune_agent import run_doctor
from deltarune_agent import run_doctor_reasoning as reasoning


def _event(step, *, room="room_a", action="right", reason="test"):
    return {
        "step": step,
        "elapsed_seconds": float(step),
        "telemetry": {"room_name": room},
        "action": action,
        "visual_valid": True,
        "reason": reason,
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


def test_blind_search_and_unconsumed_observed_evidence(tmp_path: Path):
    events = [
        _event(
            step,
            room="classroom",
            reason="no reachable frontier; probe right",
        )
        for step in range(30)
    ]
    run_dir = _write_run(tmp_path / "run", events)
    (run_dir / "navigation.json").write_text(
        json.dumps(
            {
                "screen_regions": [
                    {
                        "room": "classroom",
                        "region_x": 2,
                        "region_y": 1,
                        "hypothesis": "possible_interactable",
                        "guess_state": "proposed",
                        "completed_tests": 0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    report = reasoning.analyze_run(
        run_doctor.load_run(run_dir),
        run_doctor.DoctorThresholds(
            room_stall_steps=1000,
            room_stall_seconds=1000,
            repeated_action_streak=1000,
        ),
    )
    kinds = {finding.finding_type for finding in report.findings}
    assert "blind_search_streak" in kinds
    assert "unconsumed_observed_evidence" in kinds
    assert report.doctor_version == "0.2.0"


def test_structured_no_response_interactions_are_detected(tmp_path: Path):
    events = []
    for step in range(5):
        event = _event(step, room="room_x", action="confirm")
        event["map_updates"] = [
            {
                "type": "character_probe",
                "room": "room_x",
                "direction": "up",
                "result": "no response",
            }
        ]
        events.append(event)
    run = run_doctor.load_run(_write_run(tmp_path / "run", events))
    findings = reasoning.detect_failed_interactions(run)
    assert len(findings) == 1
    assert findings[0].measured["no_response_attempts"] == 5


def test_objective_filter_and_speed_health_are_detected(tmp_path: Path):
    run_dir = _write_run(tmp_path / "run", [_event(step) for step in range(100)])
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "objective_changes": 60,
                "single_side_interactable_routes_suppressed": 80,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "telemetry_diagnostics.json").write_text(
        json.dumps(
            {
                "received_packets": 100,
                "valid_packets": 100,
                "invalid_packets": 0,
                "speed_packets": 0,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "speed_diagnostics.json").write_text(
        json.dumps(
            {
                "requested": "10",
                "detected_multiplier": None,
                "verification_state": "unverified",
                "source": "manual",
            }
        ),
        encoding="utf-8",
    )
    run = run_doctor.load_run(run_dir)
    findings = [
        *reasoning.detect_objective_churn(run),
        *reasoning.detect_filter_pressure(run),
        *reasoning.detect_telemetry_speed_health(run),
    ]
    kinds = {finding.finding_type for finding in findings}
    assert {
        "objective_churn",
        "evidence_filter_pressure",
        "speed_verification_problem",
    } <= kinds


def test_reasoning_layer_does_not_invent_findings_without_structured_evidence(
    tmp_path: Path,
):
    events = [
        _event(0, room="a", action="right", reason="explore new edge right"),
        _event(1, room="a", action="down", reason="continue clear path down"),
        _event(2, room="b", action="right", reason="explore new edge right"),
    ]
    report = reasoning.analyze_run(
        run_doctor.load_run(_write_run(tmp_path / "healthy", events))
    )
    assert report.finding_count == 0
