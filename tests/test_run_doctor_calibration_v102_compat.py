import json
from pathlib import Path

from deltarune_agent import run_doctor_release


def _event(step, *, reason="explore", room="room_a"):
    return {
        "step": step,
        "elapsed_seconds": step / 10,
        "state": "overworld",
        "telemetry": {"room_name": room, "x": step, "y": 0},
        "action": "right" if step % 2 == 0 else "left",
        "visual_valid": True,
        "reason": reason,
    }


def _write_run(path: Path, events, *, navigation, navigation_updates=None):
    path.mkdir(parents=True)
    (path / "run.json").write_text(
        json.dumps({"agent_revision": "v102-compat-fixture", "schema_version": 2}),
        encoding="utf-8",
    )
    (path / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    (path / "navigation.json").write_text(json.dumps(navigation), encoding="utf-8")
    if navigation_updates is not None:
        (path / "navigation_updates.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in navigation_updates),
            encoding="utf-8",
        )
    return path


def _screen_update(step, region):
    return {
        "step": step,
        "update": {
            "type": "screen_region",
            "room": "room_a",
            "region": list(region),
            "hypothesis": "possible_interactable",
            "guess_state": "proposed",
            "completed_tests": 0,
            "inspections": 0,
        },
    }


def test_snapshot_only_historical_run_keeps_lower_confidence_unconsumed_finding(tmp_path: Path):
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
        navigation={
            "screen_regions": [
                {
                    "room": "room_a",
                    "hypothesis": "possible_interactable",
                    "guess_state": "proposed",
                    "completed_tests": 0,
                }
            ]
        },
    )
    report, _ = run_doctor_release.analyze_directory(run_dir)
    finding = next(
        finding
        for finding in report.base.findings
        if finding.finding_type == "unconsumed_observed_evidence"
    )
    assert finding.confidence <= 0.78
    assert any("snapshot" in uncertainty.casefold() for uncertainty in finding.uncertainties)


def test_multiple_qualifying_lifecycle_episodes_are_one_finding_per_room(tmp_path: Path):
    events = [
        _event(
            step,
            reason=(
                "no reachable frontier; probe right"
                if 20 <= step <= 23 or 40 <= step <= 42
                else "explore"
            ),
        )
        for step in range(60)
    ]
    run_dir = _write_run(
        tmp_path / "run",
        events,
        navigation={"screen_regions": []},
        navigation_updates=[_screen_update(10, (6, 4))],
    )
    report, _ = run_doctor_release.analyze_directory(run_dir)
    findings = [
        finding
        for finding in report.base.findings
        if finding.finding_type == "unconsumed_observed_evidence"
    ]
    assert len(findings) == 1
    finding = findings[0]
    assert finding.room == "room_a"
    assert finding.evidence.start_step == 20
    assert finding.evidence.end_step == 42
    assert finding.measured["qualifying_episode_count"] == 2
    assert finding.measured["blind_probe_steps_with_actionable_evidence"] == 7
    assert finding.measured["longest_consecutive_overlap"] == 4
    assert finding.measured["episodes"] == [
        {
            "start_step": 20,
            "end_step": 23,
            "overlap_steps": 4,
            "longest_consecutive_overlap": 4,
        },
        {
            "start_step": 40,
            "end_step": 42,
            "overlap_steps": 3,
            "longest_consecutive_overlap": 3,
        },
    ]
