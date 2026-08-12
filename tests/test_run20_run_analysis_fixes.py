from __future__ import annotations

from pathlib import Path

import pyautogui
import pytest

from deltarune_agent.actions import ACTIONS
from deltarune_agent.controller import KeyboardController
from deltarune_agent.run20_reporting import (
    build_run_diagnostics,
    calculate_metrics,
)
from deltarune_agent.run20_run_analysis_fixes import Run20RunAnalysisExplorer


def explorer(tmp_path: Path) -> Run20RunAnalysisExplorer:
    return Run20RunAnalysisExplorer(memory_path=tmp_path / "navigation.json")


def test_post_entry_escape_uses_observed_reverse_portal(tmp_path: Path) -> None:
    policy = explorer(tmp_path)
    policy.warps[("new_room", 10, 10, "up", "old_room", 4, 4)] = 2

    direction = policy._derive_post_entry_escape(
        "new_room",
        "old_room",
        (10, 11),
        transition_direction="up",
    )

    assert direction == "down"


def test_repeatedly_unreachable_doorway_is_retired(tmp_path: Path) -> None:
    policy = explorer(tmp_path)
    key = ("classroom", 7, 1)
    policy.screen_regions[key] = {
        "hypothesis": "possible_exit",
        "guess_state": "approaching",
        "guess_confidence": 0.82,
        "doorway_facade": True,
        "doorway_box_world": [220.0, 0.0, 260.0, 48.0],
        "edge_hint": "top",
        "approach_attempts": 6,
        "completed_tests": 0,
        "inspections": 0,
        "failed_approaches": 2,
        "views": 20,
        "independent_views": 3,
        "interest": 0.55,
    }

    policy._retire_run20_visual_leads("classroom")

    record = policy.screen_regions[key]
    assert record["hypothesis"] is None
    assert record["guess_state"] == "retired"
    assert record["doorway_failed_story_epoch"] == policy.story_epoch
    assert policy.unreachable_doorways_retired == 1


def _one_sided_interactable_record() -> dict[str, object]:
    return {
        "hypothesis": "possible_interactable",
        "guess_state": "proposed",
        "guess_confidence": 0.8,
        "entity_approach_directions": 1,
        "obstruction_target_cells": 1,
        "anchor_cell": [10, 10],
        "views": 5,
        "last_seen_sequence": 2,
        "completed_tests": 0,
        "failed_approaches": 0,
        "interest": 0.5,
    }


def test_one_sided_interactable_is_not_routed_during_normal_exploration(
    tmp_path: Path,
) -> None:
    policy = explorer(tmp_path)
    key = ("room", 2, 2)
    policy.screen_regions[key] = _one_sided_interactable_record()
    policy.seen_cells.add(("room", 0, 0))

    first = policy._direction_to_visual_hypothesis("room", (0, 0))
    second = policy._direction_to_visual_hypothesis("room", (0, 0))

    assert first is None
    assert second is None
    assert policy.screen_regions[key]["hypothesis"] == "possible_interactable"
    # The diagnostic counts unique suppressed leads, not every planning call.
    assert policy.single_side_interactable_routes_suppressed == 1


def test_story_focus_can_route_to_one_sided_interactable(tmp_path: Path) -> None:
    policy = explorer(tmp_path)
    key = ("room", 2, 2)
    policy.screen_regions[key] = _one_sided_interactable_record()
    policy.seen_cells.add(("room", 0, 0))

    result = policy._direction_to_visual_hypothesis(
        "room",
        (0, 0),
        story_focus=True,
        allowed_hypotheses={"possible_interactable"},
    )

    assert result is not None
    direction, hypothesis, target_region = result
    assert direction in {"down", "right"}
    assert hypothesis == "possible_interactable"
    assert target_region == (2, 2)
    assert policy.single_side_interactable_routes_suppressed == 0


def test_automatic_progress_does_not_reward_stale_actions(tmp_path: Path) -> None:
    policy = explorer(tmp_path)
    before_events = policy.reinforcement_rewards_applied
    before_story = policy.story_progress_events

    policy._record_story_progress("automatic scripted sequence", None)

    assert policy.story_progress_events == before_story + 1
    assert policy.reinforcement_rewards_applied == before_events
    assert policy.automatic_reward_events_suppressed == 1


def test_structured_progress_updates_override_reason_guessing() -> None:
    events = [
        {
            "state": "overworld",
            "confidence": 1.0,
            "action": "right",
            "reason": "story progress search only",
            "visual_valid": True,
            "telemetry": {"room_name": "a", "x": 0, "y": 0},
            "map_updates": [],
        },
        {
            "state": "overworld",
            "confidence": 1.0,
            "action": "down",
            "reason": "ordinary movement",
            "visual_valid": True,
            "telemetry": {"room_name": "b", "x": 0, "y": 0},
            "map_updates": [{"type": "story_progress", "event": "new room"}],
        },
    ]

    assert calculate_metrics(events).story_progress_events == 1


def test_loop_diagnostics_recognize_actual_loop_reasons() -> None:
    diagnostics = build_run_diagnostics(
        [
            {"action": "left", "state": "overworld", "reason": "detected repeated corridor loop"},
            {"action": "right", "state": "overworld", "reason": "stalled recovery; diversify"},
            {"action": "down", "state": "overworld", "reason": "explore new edge"},
        ]
    )

    assert diagnostics["loop_recovery_steps"] == 2


def test_mouse_corner_failsafe_uses_interrupt_path(monkeypatch) -> None:
    controller = KeyboardController(live=True)

    def fail(_action) -> None:
        raise pyautogui.FailSafeException("corner")

    monkeypatch.setattr(controller, "_execute", fail)
    with pytest.raises(KeyboardInterrupt, match="mouse-corner emergency stop"):
        controller.execute(ACTIONS["wait"])
