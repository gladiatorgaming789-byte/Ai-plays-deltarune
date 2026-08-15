from __future__ import annotations

from deltarune_agent.run21_multirun_fixes import Run21MultiRunExplorer


def _explorer_stub() -> Run21MultiRunExplorer:
    explorer = Run21MultiRunExplorer.__new__(Run21MultiRunExplorer)
    explorer.exit_semantic_leaks_repaired = 0
    explorer.single_side_entity_labels_downgraded = 0
    explorer.navigation_tick = 100
    explorer._room_link_cooldown_until = {}
    return explorer


def test_geometry_only_exit_label_is_downgraded_without_erasing_evidence() -> None:
    explorer = _explorer_stub()
    record = {
        "hypothesis": "possible_exit",
        "guess_semantic_state": "possible_exit",
        "guess_state": "proposed",
        "path_continuation": True,
        "edge_opening_score": 0.0,
        "exit_approach_length": 4,
        "exit_candidate_views": 0,
        "multi_view_sample_count": 0,
        "failed_approaches": 0,
        "guess_misses": 0,
    }

    explorer._sanitize_exit_semantics(record, count_repair=True)

    assert record["path_continuation"] is True
    assert record["exit_candidate_state"] == "geometry_candidate"
    assert record["hypothesis"] is None
    assert record["guess_semantic_state"] == "unknown_but_interesting"
    assert explorer.exit_semantic_leaks_repaired == 1


def test_semantic_ready_exit_remains_semantic() -> None:
    explorer = _explorer_stub()
    record = {
        "hypothesis": "possible_exit",
        "guess_semantic_state": "possible_exit",
        "guess_state": "proposed",
        "visual_summary": "rectangular doorway facade near the upper wall (frame score 90%)",
        "path_continuation": False,
        "edge_opening_score": 0.90,
        "exit_approach_length": 3,
        "exit_candidate_views": 2,
        "multi_view_sample_count": 2,
        "multi_view_consistency": 0.90,
        "failed_approaches": 0,
        "guess_misses": 0,
    }

    explorer._sanitize_exit_semantics(record, count_repair=True)

    assert record["exit_candidate_state"] == "semantic_ready"
    assert record["hypothesis"] == "possible_exit"
    assert explorer.exit_semantic_leaks_repaired == 0


def test_single_side_entity_is_downgraded_but_obstruction_is_retained() -> None:
    explorer = _explorer_stub()
    record = {
        "hypothesis": "possible_interactable",
        "guess_semantic_state": "possible_interactable",
        "guess_state": "proposed",
        "entity_approach_directions": 1,
        "obstruction_target_cells": 1,
        "failed_approaches": 0,
        "completed_tests": 0,
        "choice_retry": False,
    }

    explorer._sanitize_entity_semantics(record, count_repair=True)

    assert record["entity_approach_directions"] == 1
    assert record["obstruction_target_cells"] == 1
    assert record["hypothesis"] is None
    assert record["guess_semantic_state"] == "unknown_but_interesting"
    assert record["entity_candidate_state"] == "single_side_unresolved"
    assert explorer.single_side_entity_labels_downgraded == 1


def test_confirmed_single_side_interaction_is_preserved() -> None:
    explorer = _explorer_stub()
    record = {
        "hypothesis": "possible_interactable",
        "guess_semantic_state": "possible_interactable",
        "guess_state": "confirmed",
        "entity_approach_directions": 1,
        "obstruction_target_cells": 1,
        "confirmed_interactable_cell": [8, 9],
        "completed_tests": 1,
    }

    explorer._sanitize_entity_semantics(record, count_repair=True)

    assert record["hypothesis"] == "possible_interactable"
    assert record["guess_state"] == "confirmed"
    assert explorer.single_side_entity_labels_downgraded == 0


def test_room_link_guard_is_temporary() -> None:
    explorer = _explorer_stub()
    link = frozenset(("room_a", "room_b"))
    explorer._room_link_cooldown_until[link] = 150

    assert explorer._link_temporarily_guarded("room_a", "room_b")

    explorer.navigation_tick = 151
    assert not explorer._link_temporarily_guarded("room_a", "room_b")
    assert link not in explorer._room_link_cooldown_until
