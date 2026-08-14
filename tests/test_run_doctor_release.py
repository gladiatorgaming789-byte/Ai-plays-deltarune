import json
from pathlib import Path

from deltarune_agent import run_doctor_release


def _write_fixture(path: Path) -> Path:
    path.mkdir(parents=True)
    events = []
    for step in range(400):
        events.append(
            {
                "step": step,
                "elapsed_seconds": step / 10,
                "state": "overworld",
                "confidence": 0.9,
                "telemetry": {"room_name": "fixture_room", "x": step % 8, "y": 0},
                "action": "right" if step % 2 == 0 else "left",
                "visual_valid": step < 50,
                "reason": (
                    "no reachable frontier; probe right"
                    if step >= 180
                    else "fixture exploration"
                ),
            }
        )
    (path / "run.json").write_text(
        json.dumps(
            {
                "agent_revision": "historical-pattern-fixture",
                "schema_version": 2,
                "config": {"speed": "10", "profile": "normal", "live": True},
            }
        ),
        encoding="utf-8",
    )
    (path / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    (path / "summary.json").write_text(
        json.dumps(
            {
                "objective_changes": 100,
                "single_side_interactable_routes_suppressed": 300,
            }
        ),
        encoding="utf-8",
    )
    (path / "navigation.json").write_text(
        json.dumps(
            {
                "screen_regions": [
                    {
                        "room": "fixture_room",
                        "hypothesis": "possible_interactable",
                        "guess_state": "proposed",
                        "completed_tests": 0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (path / "telemetry_diagnostics.json").write_text(
        json.dumps(
            {
                "received_packets": 1000,
                "valid_packets": 1000,
                "invalid_packets": 0,
                "speed_packets": 0,
            }
        ),
        encoding="utf-8",
    )
    (path / "speed_diagnostics.json").write_text(
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
    return path


def test_trusted_release_marks_report_read_only(tmp_path: Path):
    fixture = _write_fixture(tmp_path / "run")
    report, comparison = run_doctor_release.analyze_directory(fixture)
    payload = run_doctor_release.report_payload(report, comparison)
    assert payload["doctor_version"] == "1.0.0"
    assert payload["trusted_release"] is True
    assert payload["read_only"] is True
    assert payload["mutates_learning"] is False


def test_compact_historical_failure_pattern_recovers_expected_detector_families(
    tmp_path: Path,
):
    fixture = _write_fixture(tmp_path / "run")
    report, _comparison = run_doctor_release.analyze_directory(fixture)
    kinds = {finding.finding_type for finding in report.base.findings}
    assert "room_stall" in kinds
    assert "capture_validity_degradation" in kinds
    assert "invalid_visual_streak" in kinds
    assert "blind_search_streak" in kinds
    assert "unconsumed_observed_evidence" in kinds
    assert "objective_churn" in kinds
    assert "evidence_filter_pressure" in kinds
    assert "speed_verification_problem" in kinds


def test_v1_report_output_is_deterministic(tmp_path: Path):
    fixture = _write_fixture(tmp_path / "run")
    first, _ = run_doctor_release.analyze_directory(fixture)
    second, _ = run_doctor_release.analyze_directory(fixture)
    assert run_doctor_release.report_payload(first) == run_doctor_release.report_payload(second)
