from __future__ import annotations

from deltarune_agent import guessing_v3 as v3
from deltarune_agent.entity_detection_v2 import (
    _belief_scores_entity_v2,
    entity_candidate_state,
    response_evidence,
    single_side_entity_candidate,
)
import deltarune_agent.entity_detection_v2 as entity_v2


def _base_record(**updates: object) -> dict[str, object]:
    record: dict[str, object] = {
        "entity_approach_directions": 1,
        "obstruction_target_cells": 1,
        "failed_approaches": 0,
        "completed_tests": 0,
        "guess_state": "proposed",
        "hypothesis": "possible_interactable",
        "choice_retry": False,
        "multi_view_sample_count": 1,
        "multi_view_consistency": 0.5,
    }
    record.update(updates)
    return record


def test_one_collision_side_is_candidate_not_semantic_proof() -> None:
    record = _base_record()

    assert single_side_entity_candidate(record)
    assert entity_candidate_state(record) == "single_side_unresolved"
    assert not response_evidence(record)


def test_response_evidence_exempts_confirmed_interaction() -> None:
    record = _base_record(
        guess_state="confirmed",
        hypothesis="possible_interactable",
        confirmed_interactable_cell=[12, 8],
    )

    assert response_evidence(record)
    assert not single_side_entity_candidate(record)
    assert entity_candidate_state(record) == "confirmed_response"


def test_two_collision_sides_are_not_weakened_as_single_side() -> None:
    record = _base_record(entity_approach_directions=2)

    assert not single_side_entity_candidate(record)
    assert entity_candidate_state(record) == "multi_side_geometry"


def test_entity_v2_removes_semantic_sized_single_side_bonus() -> None:
    original = entity_v2._ORIGINAL_BELIEF_SCORES
    try:
        entity_v2._ORIGINAL_BELIEF_SCORES = lambda record, consistency, samples: {
            "possible_exit": 0.7,
            "possible_character": 0.95,
            "possible_interactable": 2.2,
            "scenery": 1.0,
        }
        scores = _belief_scores_entity_v2(_base_record(), 0.5, 1)
    finally:
        entity_v2._ORIGINAL_BELIEF_SCORES = original

    assert scores["possible_interactable"] < 1.3
    assert scores["possible_interactable"] < 2.2
    assert scores["scenery"] > 1.0


def test_failed_single_side_test_shifts_weight_toward_scenery() -> None:
    original = entity_v2._ORIGINAL_BELIEF_SCORES
    try:
        entity_v2._ORIGINAL_BELIEF_SCORES = lambda record, consistency, samples: {
            "possible_exit": 0.7,
            "possible_character": 0.95,
            "possible_interactable": 2.2,
            "scenery": 1.0,
        }
        fresh = _belief_scores_entity_v2(_base_record(), 0.5, 1)
        tested = _belief_scores_entity_v2(
            _base_record(completed_tests=1),
            0.5,
            1,
        )
    finally:
        entity_v2._ORIGINAL_BELIEF_SCORES = original

    assert tested["possible_interactable"] < fresh["possible_interactable"]
    assert tested["scenery"] > fresh["scenery"]
