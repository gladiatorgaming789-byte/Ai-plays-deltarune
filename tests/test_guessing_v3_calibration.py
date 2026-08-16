from __future__ import annotations

from copy import deepcopy

# Entity and exit calibrations are part of the shipped Guessing-v3 stack.
# Loading them explicitly keeps isolated and full-suite runs equivalent.
import deltarune_agent.hierarchical_policy  # noqa: F401

from deltarune_agent import guessing_v3 as v3
from deltarune_agent.guessing_v3_calibration import install_guessing_v3_calibration


# This remains idempotent when the production bootstrap already installed it.
install_guessing_v3_calibration()


def _base_observation() -> dict[str, object]:
    return {
        "views": 3,
        "independent_views": 1,
        "interest": 0.24,
        "guess_state": "proposed",
        "entity_approach_directions": 1,
        "obstruction_target_cells": 4,
        "last_seen_sequence": 1,
        "last_seen_step": 10,
        "anchor_world": [100.0, 100.0],
        "evidence_viewpoints": [[0, 0]],
    }


def test_legacy_routing_label_is_not_evidence_for_itself() -> None:
    character_labeled = {
        **_base_observation(),
        "hypothesis": "possible_character",
    }
    interactable_labeled = {
        **_base_observation(),
        "hypothesis": "possible_interactable",
    }

    scores_a = v3._belief_scores(character_labeled, 0.5, 1)
    scores_b = v3._belief_scores(interactable_labeled, 0.5, 1)

    assert scores_a == scores_b


def test_ambiguous_broad_one_side_obstruction_stays_unresolved() -> None:
    record = _base_observation()

    v3.refresh_guess_record_v3(record, region=(3, 3))

    assert record["guess_semantic_state"] == v3.UNKNOWN_BUT_INTERESTING
    assert record["hypothesis"] is None
    assert record["guess_beliefs"]["possible_interactable"] < 0.40


def test_compact_one_side_obstruction_stays_unresolved_in_production_stack() -> None:
    record = {
        **_base_observation(),
        "obstruction_target_cells": 2,
    }

    v3.refresh_guess_record_v3(record, region=(3, 3))

    assert record["guess_semantic_state"] == v3.UNKNOWN_BUT_INTERESTING
    assert record["hypothesis"] is None
    assert record["guess_beliefs"]["possible_interactable"] < 0.40


def test_same_geometry_produces_same_beliefs_after_previous_semantic_commit() -> None:
    first = {
        **_base_observation(),
        "obstruction_target_cells": 2,
    }
    v3.refresh_guess_record_v3(first, region=(3, 3))
    assert first["hypothesis"] is None
    assert first["guess_semantic_state"] == v3.UNKNOWN_BUT_INTERESTING

    second = deepcopy(first)
    # Simulate the next observation with the same independent evidence. The
    # exposed routing label is present, but must not strengthen its own belief.
    second["last_seen_sequence"] = 2
    second["last_seen_step"] = 20
    second["evidence_viewpoints"] = [[0, 0]]
    before = dict(first["guess_beliefs"])
    v3.refresh_guess_record_v3(second, region=(3, 3))

    assert second["guess_beliefs"] == before
