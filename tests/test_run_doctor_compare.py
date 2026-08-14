import json
from pathlib import Path

from deltarune_agent import run_doctor
from deltarune_agent import run_doctor_compare as compare


def _event(step, room):
    return {
        "step": step,
        "elapsed_seconds": float(step),
        "telemetry": {"room_name": room, "x": step * 8, "y": 0},
        "action": "right" if step % 2 == 0 else "down",
        "visual_valid": True,
        "state": "overworld",
        "confidence": 0.9,
        "reason": "fixture",
    }


def _write_run(path: Path, *, start_room="a", speed="1", revision="rev"):
    path.mkdir(parents=True)
    events = [_event(step, start_room if step < 3 else "b") for step in range(6)]
    (path / "run.json").write_text(
        json.dumps(
            {
                "agent_revision": revision,
                "schema_version": 2,
                "config": {"speed": speed, "live": True, "profile": "normal"},
            }
        ),
        encoding="utf-8",
    )
    (path / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    return run_doctor.load_run(path)


def test_metric_directionality_is_only_assigned_when_semantics_are_known():
    assert compare.classify_metric("visual_coverage", 0.2, 0.8) == "improved"
    assert compare.classify_metric("room_bounces", 7, 2) == "improved"
    assert compare.classify_metric("steps", 100, 90) == "neutral"


def test_unlike_runs_are_explicitly_weak_comparability(tmp_path: Path):
    baseline = _write_run(tmp_path / "baseline", start_room="a", speed="1")
    candidate = _write_run(tmp_path / "candidate", start_room="z", speed="10")
    level, _reasons, caveats = compare.comparability(baseline, candidate)
    assert level == "weak"
    assert any("different observed starting rooms" in caveat for caveat in caveats)


def test_same_start_and_config_produce_strong_comparability_across_revisions(
    tmp_path: Path,
):
    baseline = _write_run(tmp_path / "baseline", revision="old")
    candidate = _write_run(tmp_path / "candidate", revision="new")
    level, reasons, _caveats = compare.comparability(baseline, candidate)
    assert level == "strong"
    assert any("different agent revisions" in reason for reason in reasons)


def test_weak_comparison_never_claims_aggregate_improvement(tmp_path: Path):
    baseline = _write_run(tmp_path / "baseline", start_room="a", speed="1")
    candidate = _write_run(tmp_path / "candidate", start_room="z", speed="10")
    _report, comparison = compare.compare_runs(baseline, candidate)
    assert comparison.comparability == "weak"
    assert comparison.verdict == "inconclusive"
