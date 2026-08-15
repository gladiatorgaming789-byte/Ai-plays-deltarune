from __future__ import annotations

from deltarune_agent.run21_final import Run21Explorer


def _stub() -> Run21Explorer:
    explorer = Run21Explorer.__new__(Run21Explorer)
    explorer.screen_regions = {}
    explorer.map_updates = []
    explorer.reopened_legacy_route_only_entity_candidates = 0
    return explorer


def test_route_only_failure_can_be_reopened_without_erasing_collision_evidence(monkeypatch) -> None:
    explorer = _stub()
    key = ("room_a", 2, 3)
    record = {
        "hypothesis": None,
        "guess_semantic_state": "unknown_but_interesting",
        "guess_state": "cooldown",
        "entity_approach_directions": 1,
        "obstruction_target_cells": 1,
        "completed_tests": 0,
        "failed_approaches": 1,
        "last_failure_reason": "no safe learned approach remained",
    }
    explorer.screen_regions[key] = record
    monkeypatch.setattr(
        explorer,
        "_refresh_visual_guess_metadata",
        lambda region, target: None,
    )
    monkeypatch.setattr(
        explorer,
        "_screen_region_map_update",
        lambda key, target: {"type": "screen_region"},
    )

    explorer._reopen_legacy_route_only_entity_candidates()

    assert record["entity_approach_directions"] == 1
    assert record["failed_approaches"] == 0
    assert record["entity_route_retry_migrated"] is True
    assert explorer.reopened_legacy_route_only_entity_candidates == 1


def test_real_no_response_test_is_not_reopened(monkeypatch) -> None:
    explorer = _stub()
    key = ("room_a", 2, 3)
    record = {
        "hypothesis": None,
        "guess_semantic_state": "unknown_but_interesting",
        "guess_state": "rejected",
        "entity_approach_directions": 1,
        "obstruction_target_cells": 1,
        "completed_tests": 1,
        "failed_approaches": 0,
        "last_failure_reason": "interaction produced no state change",
    }
    explorer.screen_regions[key] = record

    explorer._reopen_legacy_route_only_entity_candidates()

    assert record["completed_tests"] == 1
    assert record["guess_state"] == "rejected"
    assert explorer.reopened_legacy_route_only_entity_candidates == 0


def test_room_wide_lifetime_cap_is_removed() -> None:
    explorer = _stub()
    explorer._weak_entity_room_probes = {("room_a", 0): 999}
    explorer.story_epoch = 0

    assert explorer._weak_entity_probe_count("room_a") == 0
