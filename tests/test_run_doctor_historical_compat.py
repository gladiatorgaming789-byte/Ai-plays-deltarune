import json
from pathlib import Path

from deltarune_agent import run_doctor
from deltarune_agent import run_doctor_release


def test_trusted_v1_falls_back_to_historical_policy_summary(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(
        json.dumps({"agent_revision": "historical", "schema_version": 2}),
        encoding="utf-8",
    )
    (run_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "step": 0,
                "elapsed_seconds": 0.0,
                "telemetry": {"room_name": "room_a"},
                "action": "right",
                "visual_valid": True,
                "reason": "fixture",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    # Modern summary exists but does not contain the older policy counters.
    (run_dir / "summary.json").write_text(
        json.dumps({"story_progress_events": 1}),
        encoding="utf-8",
    )
    (run_dir / "run_report.json").write_text(
        json.dumps(
            {
                "policy_summary": {
                    "objective_changes": 72,
                    "single_side_interactable_routes_suppressed": 55,
                }
            }
        ),
        encoding="utf-8",
    )

    run = run_doctor.load_run(run_dir)
    assert run_doctor_release._historical_summary_value(run, "objective_changes") == 72
    assert (
        run_doctor_release._historical_summary_value(
            run,
            "single_side_interactable_routes_suppressed",
        )
        == 55
    )
