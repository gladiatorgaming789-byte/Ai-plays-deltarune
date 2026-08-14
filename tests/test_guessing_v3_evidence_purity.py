from __future__ import annotations

from deltarune_agent import guessing_v3 as v3
from deltarune_agent.guessing_v3_calibration import install_guessing_v3_calibration


install_guessing_v3_calibration()


def test_old_semantic_label_alone_cannot_keep_guess_interesting() -> None:
    record = {
        "views": 3,
        "independent_views": 1,
        "interest": 0.40,
        "hypothesis": "possible_character",
        "guess_state": "proposed",
        "entity_approach_directions": 0,
        "obstruction_target_cells": 0,
        "edge_opening_score": 0.0,
        "path_continuation": False,
        "choice_retry": False,
        "last_seen_sequence": 1,
        "last_seen_step": 10,
        "anchor_world": [100.0, 100.0],
        "evidence_viewpoints": [[0, 0]],
    }

    v3.refresh_guess_record_v3(record, region=(3, 3))

    assert record["guess_semantic_state"] != v3.UNKNOWN_BUT_INTERESTING
    assert record["hypothesis"] is None


def test_structural_gate_uses_independent_observation_fields() -> None:
    no_structure = {
        "hypothesis": "possible_interactable",
        "entity_approach_directions": 0,
        "obstruction_target_cells": 0,
        "edge_opening_score": 0.0,
        "path_continuation": False,
        "choice_retry": False,
    }
    with_structure = {
        **no_structure,
        "hypothesis": None,
        "entity_approach_directions": 1,
        "obstruction_target_cells": 2,
    }

    assert not v3._structural_evidence(no_structure)
    assert v3._structural_evidence(with_structure)
