from __future__ import annotations

from pathlib import Path

from deltarune_agent import run_doctor as foundation
from deltarune_agent.run_doctor_interactions_v103 import (
    spatial_repeated_interaction_findings,
)


def _run(tmp_path: Path, events: list[dict]) -> foundation.NormalizedRun:
    return foundation.NormalizedRun(
        directory=tmp_path,
        manifest={},
        summary={},
        run_report={},
        telemetry_diagnostics={},
        speed_diagnostics={},
        events=events,
        predictions=[],
        navigation_updates=[],
    )


def _probe_event(step: int, cell: tuple[int, int], direction: str) -> dict:
    return {
        "step": step,
        "elapsed_seconds": step / 10,
        "state": "overworld",
        "telemetry": {"mode": "overworld", "room_name": "room_a"},
        "map_updates": [
            {
                "type": "character_probe",
                "room": "room_a",
                "cell": list(cell),
                "direction": direction,
                "result": "no response",
            }
        ],
    }


def test_same_facing_direction_on_distant_objects_is_not_one_retry_loop(tmp_path: Path) -> None:
    events = [
        _probe_event(1, (10, 10), "down"),
        _probe_event(2, (30, 10), "down"),
        _probe_event(3, (50, 10), "down"),
        _probe_event(4, (70, 10), "down"),
    ]

    assert spatial_repeated_interaction_findings(_run(tmp_path, events)) == []


def test_near_same_target_retries_are_flagged_even_from_different_sides(tmp_path: Path) -> None:
    events = [
        # Both probes target cells within Chebyshev radius 1.
        _probe_event(1, (10, 9), "down"),
        _probe_event(2, (9, 10), "right"),
    ]

    findings = spatial_repeated_interaction_findings(_run(tmp_path, events))

    assert len(findings) == 1
    assert findings[0].finding_type == "repeated_failed_interaction"
    assert findings[0].measured["no_response_attempts"] == 2
