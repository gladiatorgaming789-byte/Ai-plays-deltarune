from __future__ import annotations

from deltarune_agent import guessing_v3 as v3
from deltarune_agent.guessing_v3_calibration import install_guessing_v3_calibration


install_guessing_v3_calibration()


def test_salient_unlabeled_visual_feature_can_be_unknown_but_interesting() -> None:
    record = {
        "views": 2,
        "independent_views": 1,
        "interest": 0.24,
        "contrast": 0.22,
        "edge_density": 0.19,
        "colorfulness": 0.10,
        "feature_box_world": [96.0, 96.0, 112.0, 112.0],
        "hypothesis": None,
        "guess_state": "proposed",
        "entity_approach_directions": 0,
        "obstruction_target_cells": 0,
        "edge_opening_score": 0.0,
        "last_seen_sequence": 1,
        "last_seen_step": 10,
        "anchor_world": [104.0, 104.0],
        "evidence_viewpoints": [[0, 0]],
    }

    v3.refresh_guess_record_v3(record, region=(3, 3))

    assert record["guess_semantic_state"] == v3.UNKNOWN_BUT_INTERESTING
    assert record["hypothesis"] is None


def test_weak_visual_noise_does_not_become_unknown_but_interesting() -> None:
    record = {
        "views": 2,
        "independent_views": 1,
        "interest": 0.05,
        "contrast": 0.04,
        "edge_density": 0.04,
        "colorfulness": 0.03,
        "feature_box_world": [96.0, 96.0, 112.0, 112.0],
        "hypothesis": None,
        "guess_state": "proposed",
        "entity_approach_directions": 0,
        "obstruction_target_cells": 0,
        "edge_opening_score": 0.0,
        "last_seen_sequence": 1,
        "last_seen_step": 10,
        "anchor_world": [104.0, 104.0],
        "evidence_viewpoints": [[0, 0]],
    }

    v3.refresh_guess_record_v3(record, region=(3, 3))

    assert record["guess_semantic_state"] != v3.UNKNOWN_BUT_INTERESTING
