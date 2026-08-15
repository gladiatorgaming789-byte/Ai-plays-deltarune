from __future__ import annotations

from pathlib import Path

from deltarune_agent import run_doctor as foundation
from deltarune_agent import run_doctor_calibration_v103 as v103


def _run(
    tmp_path: Path,
    *,
    events: list[dict] | None = None,
    predictions: list[dict] | None = None,
    updates: list[dict] | None = None,
) -> foundation.NormalizedRun:
    return foundation.NormalizedRun(
        directory=tmp_path,
        manifest={},
        summary={},
        run_report={},
        telemetry_diagnostics={},
        speed_diagnostics={},
        events=list(events or []),
        predictions=list(predictions or []),
        navigation_updates=list(updates or []),
    )


def _screen_update(step: int, **fields: object) -> dict:
    update = {
        "type": "screen_region",
        "room": "room_a",
        "region": [2, 3],
        "hypothesis": "possible_interactable",
        "guess_state": "proposed",
        "entity_approach_directions": 1,
        "obstruction_target_cells": 1,
        "completed_tests": 0,
    }
    update.update(fields)
    return {"step": step, "update": update}


def _prediction(step: int, guess_id: str = "room_a@2,3") -> dict:
    return {
        "step": step,
        "elapsed_seconds": step / 10,
        "prediction_snapshot": {"selected_guess_id": guess_id},
    }


def test_eight_consecutive_one_side_guess_selections_are_flagged(tmp_path: Path) -> None:
    run = _run(
        tmp_path,
        predictions=[_prediction(step) for step in range(8)],
        updates=[_screen_update(0)],
    )

    findings = v103._weak_guess_chase_findings(run)

    assert len(findings) == 1
    assert findings[0].finding_type == "repeated_weak_guess_approach"
    assert findings[0].measured["consecutive_selected_steps"] == 8


def test_seven_consecutive_one_side_selections_are_below_threshold(tmp_path: Path) -> None:
    run = _run(
        tmp_path,
        predictions=[_prediction(step) for step in range(7)],
        updates=[_screen_update(0)],
    )

    assert v103._weak_guess_chase_findings(run) == []


def _room_event(step: int, room: str, *, visual_valid: bool = True, x: float = 0, y: float = 0) -> dict:
    return {
        "step": step,
        "elapsed_seconds": step / 10,
        "state": "overworld",
        "visual_valid": visual_valid,
        "telemetry": {
            "mode": "overworld",
            "room_name": room,
            "player_foot_x": x,
            "player_foot_y": y,
        },
    }


def test_four_same_link_crossings_are_flagged(tmp_path: Path) -> None:
    rooms = ["room_a", "room_b", "room_a", "room_b", "room_a"]
    run = _run(
        tmp_path,
        events=[_room_event(step, room) for step, room in enumerate(rooms)],
    )

    findings = v103._room_link_pingpong_findings(run)

    assert len(findings) == 1
    assert findings[0].finding_type == "repeated_room_link_pingpong"
    assert findings[0].measured["crossings_in_window"] == 4


def test_three_same_link_crossings_are_not_flagged(tmp_path: Path) -> None:
    rooms = ["room_a", "room_b", "room_a", "room_b"]
    run = _run(
        tmp_path,
        events=[_room_event(step, room) for step, room in enumerate(rooms)],
    )

    assert v103._room_link_pingpong_findings(run) == []


def test_unresolved_exit_with_possible_exit_label_is_an_invariant_failure(tmp_path: Path) -> None:
    run = _run(
        tmp_path,
        updates=[
            _screen_update(
                4,
                hypothesis="possible_exit",
                exit_candidate_state="geometry_candidate",
                path_continuation=True,
            )
        ],
    )

    findings = v103._exit_semantic_leak_findings(run)

    assert len(findings) == 1
    assert findings[0].finding_type == "unresolved_exit_semantic_leak"
    assert findings[0].confidence == 1.0


def test_semantic_ready_exit_is_not_called_a_leak(tmp_path: Path) -> None:
    run = _run(
        tmp_path,
        updates=[
            _screen_update(
                4,
                hypothesis="possible_exit",
                exit_candidate_state="semantic_ready",
            )
        ],
    )

    assert v103._exit_semantic_leak_findings(run) == []


def test_invalid_visual_streak_with_real_movement_is_flagged(tmp_path: Path) -> None:
    events = [
        _room_event(
            step,
            "room_a",
            visual_valid=False,
            x=float(step),
            y=0.0,
        )
        for step in range(v103.MOVING_INVALID_STREAK)
    ]
    run = _run(tmp_path, events=events)

    findings = v103._moving_invalid_capture_findings(run)

    assert len(findings) == 1
    assert findings[0].finding_type == "capture_stale_while_player_moves"
    assert findings[0].measured["distinct_player_positions"] >= 5


def test_invalid_visual_streak_with_stationary_player_is_not_blamed_on_capture(tmp_path: Path) -> None:
    events = [
        _room_event(
            step,
            "room_a",
            visual_valid=False,
            x=10.0,
            y=20.0,
        )
        for step in range(v103.MOVING_INVALID_STREAK + 10)
    ]
    run = _run(tmp_path, events=events)

    assert v103._moving_invalid_capture_findings(run) == []
