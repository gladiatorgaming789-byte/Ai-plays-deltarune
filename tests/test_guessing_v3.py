from __future__ import annotations

import json
from pathlib import Path

from deltarune_agent.guessing_v3 import (
    BELIEF_KINDS,
    MAX_INFORMATION_PROBES,
    UNKNOWN_BUT_INTERESTING,
    information_gain_probe_plan,
    refresh_guess_record_v3,
)
from deltarune_agent.guessing_v3_bootstrap import install_guessing_v3
from deltarune_agent.run4_explorer import Run4Explorer
from deltarune_agent.world_model import WorldModel


def _ambiguous_record() -> dict[str, object]:
    return {
        "views": 3,
        "independent_views": 2,
        "interest": 0.24,
        "hypothesis": None,
        "guess_state": "proposed",
        "entity_approach_directions": 1,
        "obstruction_target_cells": 4,
        "last_seen_sequence": 1,
        "last_seen_step": 10,
        "anchor_world": [100.0, 100.0],
        "evidence_viewpoints": [[0, 0]],
    }


def test_ambiguous_structural_evidence_stays_unknown_but_interesting() -> None:
    record = _ambiguous_record()

    refresh_guess_record_v3(record, region=(3, 3))

    assert record["guess_semantic_state"] == UNKNOWN_BUT_INTERESTING
    assert record["hypothesis"] is None
    beliefs = record["guess_beliefs"]
    assert isinstance(beliefs, dict)
    assert set(beliefs) == set(BELIEF_KINDS)
    assert abs(sum(float(value) for value in beliefs.values()) - 1.0) < 0.001
    assert record["guess_label"] == "Interesting feature; type unresolved"
    assert len(record["guess_evidence_ledger"]) == 1


def test_strong_path_evidence_commits_to_exit() -> None:
    record = {
        **_ambiguous_record(),
        "hypothesis": "possible_exit",
        "path_continuation": True,
        "edge_opening_score": 0.82,
        "edge_width_ratio": 0.31,
    }

    refresh_guess_record_v3(record, region=(3, 3))

    assert record["guess_semantic_state"] == "possible_exit"
    assert record["hypothesis"] == "possible_exit"
    assert record["guess_beliefs"]["possible_exit"] > record["guess_beliefs"]["scenery"]


def test_belief_can_revise_after_new_observed_geometry() -> None:
    record = _ambiguous_record()
    refresh_guess_record_v3(record, region=(3, 3))
    assert record["guess_semantic_state"] == UNKNOWN_BUT_INTERESTING

    # Later collision mapping observes the compact feature from several sides.
    # The legacy geometry layer is allowed to propose a character at this point;
    # v3 still decides whether the combined evidence is strong enough to commit.
    record.update(
        {
            "hypothesis": "possible_character",
            "entity_approach_directions": 3,
            "obstruction_target_cells": 2,
            "last_seen_sequence": 2,
            "last_seen_step": 20,
            "anchor_world": [100.5, 100.0],
            "evidence_viewpoints": [[0, 0], [1, 0]],
        }
    )
    refresh_guess_record_v3(record, region=(3, 3))

    assert record["guess_semantic_state"] == "possible_character"
    assert record["hypothesis"] == "possible_character"
    assert record["guess_beliefs"]["possible_character"] == max(
        record["guess_beliefs"][kind] for kind in BELIEF_KINDS
    )
    assert len(record["guess_evidence_ledger"]) == 2


def test_stable_multiview_world_anchor_scores_high_consistency() -> None:
    record = _ambiguous_record()
    refresh_guess_record_v3(record, region=(3, 3))
    record.update(
        {
            "last_seen_sequence": 2,
            "last_seen_step": 15,
            "anchor_world": [100.5, 100.0],
            "evidence_viewpoints": [[0, 0], [2, 0]],
        }
    )

    refresh_guess_record_v3(record, region=(3, 3))

    assert record["multi_view_sample_count"] == 2
    assert record["multi_view_consistency"] >= 0.9
    assert "world anchor remained stable" in json.dumps(record["guess_evidence_ledger"])


def test_large_world_anchor_drift_is_negative_multiview_evidence() -> None:
    record = _ambiguous_record()
    refresh_guess_record_v3(record, region=(3, 3))
    record.update(
        {
            "last_seen_sequence": 2,
            "last_seen_step": 15,
            "anchor_world": [148.0, 100.0],
            "evidence_viewpoints": [[0, 0], [2, 0]],
        }
    )

    refresh_guess_record_v3(record, region=(3, 3))

    assert record["multi_view_sample_count"] == 2
    assert record["multi_view_consistency"] < 0.35
    assert "world anchor drifted" in json.dumps(record["guess_evidence_ledger"])


def test_information_gain_probe_changes_viewpoint_without_completing_guess() -> None:
    explorer = Run4Explorer()
    room = "room_test"
    key = (room, 2, 2)
    record = _ambiguous_record()
    record.update(
        {
            "guess_semantic_state": UNKNOWN_BUT_INTERESTING,
            "guess_beliefs": {
                "possible_exit": 0.24,
                "possible_character": 0.26,
                "possible_interactable": 0.27,
                "scenery": 0.23,
            },
            "anchor_cell": [8, 8],
            "information_probe_attempts": 0,
            "information_probe_cooldown_until": 0,
        }
    )
    explorer.screen_regions[key] = record
    explorer.current_visible_regions = {key}
    explorer.navigation_tick = 100
    # Target is horizontally displaced, so a vertical sidestep provides a new
    # viewpoint. Mark up as a known-open move so it is preferred over down.
    explorer.open_edges.update(
        {
            (room, 4, 8, "up", 4, 7),
            (room, 4, 7, "down", 4, 8),
        }
    )

    plan = information_gain_probe_plan(explorer, room, (4, 8))

    assert plan is not None
    direction, commitment, reason = plan
    assert direction == "up"
    assert commitment == 2
    assert "information gain" in reason
    assert record["information_probe_attempts"] == 1
    assert int(record.get("completed_tests", 0)) == 0
    assert record["guess_state"] == "proposed"
    assert any(
        entry.get("kind") == "information_probe"
        for entry in record["guess_evidence_ledger"]
    )


def test_information_gain_probes_are_bounded() -> None:
    explorer = Run4Explorer()
    room = "room_test"
    key = (room, 2, 2)
    record = _ambiguous_record()
    record.update(
        {
            "guess_semantic_state": UNKNOWN_BUT_INTERESTING,
            "guess_beliefs": {
                "possible_exit": 0.24,
                "possible_character": 0.26,
                "possible_interactable": 0.27,
                "scenery": 0.23,
            },
            "anchor_cell": [8, 8],
            "information_probe_attempts": MAX_INFORMATION_PROBES,
            "information_probe_cooldown_until": 0,
        }
    )
    explorer.screen_regions[key] = record
    explorer.current_visible_regions = {key}
    explorer.navigation_tick = 100

    assert information_gain_probe_plan(explorer, room, (4, 8)) is None


def test_v3_metadata_survives_world_model_save_and_reload(tmp_path: Path) -> None:
    # The bootstrap captures whichever persistence layers are active in this
    # process, so the test is valid whether Run16 was installed earlier or not.
    install_guessing_v3()
    path = tmp_path / "navigation.json"
    model = WorldModel(path)
    key = ("room_test", 2, 3)
    record = _ambiguous_record()
    refresh_guess_record_v3(record, region=(2, 3))
    model.screen_regions[key] = record

    model.save()
    raw = json.loads(path.read_text(encoding="utf-8"))
    stored = raw["screen_regions"][0]
    assert stored["guessing_version"] == 3
    assert stored["guess_semantic_state"] == UNKNOWN_BUT_INTERESTING
    assert isinstance(stored["guess_beliefs"], dict)
    assert stored["guess_evidence_ledger"]

    loaded = WorldModel.load(path)
    restored = loaded.screen_regions[key]
    assert restored["guessing_version"] == 3
    assert restored["guess_semantic_state"] == UNKNOWN_BUT_INTERESTING
    assert restored["guess_beliefs"] == stored["guess_beliefs"]
    assert restored["guess_evidence_ledger"]


def test_evidence_ledger_is_bounded() -> None:
    record = _ambiguous_record()
    for sequence in range(1, 40):
        record["last_seen_sequence"] = sequence
        record["last_seen_step"] = sequence * 5
        record["anchor_world"] = [100.0 + sequence * 0.05, 100.0]
        record["evidence_viewpoints"] = [[sequence, 0]]
        refresh_guess_record_v3(record, region=(3, 3))

    assert len(record["guess_evidence_ledger"]) <= 24
    assert len(record["guess_world_samples"]) <= 10


def test_confirmed_guess_lifecycle_is_not_demoted() -> None:
    record = {
        **_ambiguous_record(),
        "hypothesis": "possible_character",
        "guess_state": "confirmed",
        "failed_approaches": 5,
        "guess_misses": 5,
    }

    refresh_guess_record_v3(record, region=(3, 3))

    assert record["guess_state"] == "confirmed"
    assert record["hypothesis"] == "possible_character"
