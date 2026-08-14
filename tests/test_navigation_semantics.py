from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

from deltarune_agent.navigation_semantics import (
    WARP_CLASSIFICATION_VERSION,
    canonicalize_warp_observations,
)
from deltarune_agent.world_model import WorldModel


def test_canonicalizer_groups_only_nearby_same_direction_samples() -> None:
    near_a = ("room_a", 10, 20, "down", "room_b", 4, 2)
    near_b = ("room_a", 12, 20, "down", "room_b", 6, 2)
    other_direction = ("room_a", 11, 20, "right", "room_b", 6, 2)
    far = ("room_a", 30, 20, "down", "room_b", 8, 2)

    clusters = canonicalize_warp_observations(
        Counter({near_a: 2, near_b: 1, other_direction: 1, far: 1})
    )

    assert len(clusters) == 3
    combined = next(cluster for cluster in clusters if len(cluster.variants) == 2)
    assert combined.crossings == 3
    assert combined.source_bounds == (10, 20, 12, 20)
    assert combined.source_center == (11, 20)
    assert combined.aperture == {
        "axis": "horizontal",
        "span_cells": 3,
        "bounds": [10, 20, 12, 20],
    }
    assert {warp for warp, _count in combined.variants} == {near_a, near_b}


def test_new_room_discovery_is_new_area_not_story_progression() -> None:
    model = WorldModel()
    warp = ("room_a", 10, 20, "down", "room_b", 4, 2)

    portal_id = model.record_warp_transition(
        warp,
        destination_was_novel=True,
        step=12,
    )
    record = model.warp_portals[portal_id]

    assert model.warps[warp] == 1
    assert model.transitions[("room_a", "room_b")] == 1
    assert record["classification_version"] == WARP_CLASSIFICATION_VERSION
    assert record["role"] == "new_area"
    assert record["semantic_role"] == "new_area"
    assert record["classification_state"] == "observed"
    assert record["behavior_tags"] == []
    assert record["first_novel_destination"] == "room_b"
    assert record["non_discovery_progress_outcomes"] == 0
    assert "no independent story-progress" in " ".join(record["basis"])

    model.record_warp_progress(
        portal_id,
        "discovered a new room",
        discovery_only=True,
    )
    assert record["role"] == "new_area"

    model.record_warp_progress(portal_id, "cutscene advanced story", step=30)
    assert record["role"] == "progression"
    assert record["semantic_role"] == "progression"
    assert record["classification_state"] == "confirmed"
    assert record["non_discovery_progress_outcomes"] == 1
    assert record["progress_outcomes"] == {"cutscene advanced story": 1}


def test_return_evidence_is_behavior_not_optional_or_return_semantic_role() -> None:
    model = WorldModel()
    outbound = ("hall", 8, 4, "right", "side_room", 1, 4)
    returning = ("side_room", 1, 4, "left", "hall", 8, 4)
    outbound_id = model.record_warp_transition(
        outbound,
        destination_was_novel=True,
    )
    returning_id = model.record_warp_transition(
        returning,
        destination_was_novel=False,
    )
    model.record_warp_transition(outbound, destination_was_novel=False)

    model.record_warp_return(
        outbound_id,
        dwell_steps=7,
        returned_via=returning_id,
    )

    outbound_record = model.warp_portals[outbound_id]
    return_record = model.warp_portals[returning_id]

    assert outbound_record["role"] == "unknown"
    assert outbound_record["semantic_role"] == "new_area"
    assert outbound_record["classification_state"] == "provisional"
    assert "quick_return" in outbound_record["behavior_tags"]
    assert "likely_optional" not in {outbound_record["role"], return_record["role"]}
    assert "return/backtrack" not in {outbound_record["role"], return_record["role"]}

    assert return_record["role"] == "unknown"
    assert return_record["semantic_role"] == "unknown"
    assert "observed_return_leg" in return_record["behavior_tags"]
    assert outbound_record["dwell_steps_total"] == 7


def test_return_prone_route_can_later_promote_to_progression() -> None:
    model = WorldModel()
    outbound = ("room_a", 5, 5, "right", "room_b", 1, 5)
    returning = ("room_b", 1, 5, "left", "room_a", 5, 5)
    outbound_id = model.record_warp_transition(outbound, destination_was_novel=True)
    returning_id = model.record_warp_transition(returning, destination_was_novel=False)

    # Two observed quick returns are enough to establish return-prone behavior,
    # but not enough to declare the route optional or return-only.
    model.record_warp_return(outbound_id, dwell_steps=6, returned_via=returning_id)
    model.record_warp_transition(outbound, destination_was_novel=False)
    model.record_warp_return(outbound_id, dwell_steps=8, returned_via=returning_id)

    record = model.warp_portals[outbound_id]
    assert record["role"] == "unknown"
    assert record["semantic_role"] == "new_area"
    assert "return_prone" in record["behavior_tags"]
    assert record["return_tendency"] > 0.0

    model.record_warp_progress(outbound_id, "observed story progress after crossing")

    assert record["role"] == "progression"
    assert record["semantic_role"] == "progression"
    assert record["classification_state"] == "confirmed"
    assert "return_prone" in record["behavior_tags"]
    assert "cannot demote observed progression" in " ".join(record["basis"])


def test_single_loop_suppression_is_caution_not_hard_role() -> None:
    model = WorldModel()
    warp = ("room_a", 5, 5, "up", "room_b", 5, 9)
    portal_id = model.record_warp_transition(
        warp,
        destination_was_novel=False,
    )

    model.record_warp_suppression(portal_id, "A-B-A room loop", step=50)
    record = model.warp_portals[portal_id]

    assert record["role"] == "unknown"
    assert "loop_risk" in record["behavior_tags"]
    assert record["suppression_reasons"] == {"A-B-A room loop": 1}


def test_repeated_loop_suppression_is_temporary_but_progress_has_precedence() -> None:
    model = WorldModel()
    warp = ("room_a", 5, 5, "up", "room_b", 5, 9)
    portal_id = model.record_warp_transition(
        warp,
        destination_was_novel=False,
    )

    model.record_warp_suppression(portal_id, "A-B-A room loop", step=50)
    model.record_warp_suppression(portal_id, "A-B-A room loop", step=70)
    record = model.warp_portals[portal_id]

    assert record["role"] == "loop_suppressed"
    assert record["classification_state"] == "safety_hold"
    assert "loop_risk" in record["behavior_tags"]

    model.record_warp_progress(portal_id, "battle began after crossing")
    assert record["role"] == "progression"
    assert record["semantic_role"] == "progression"
    assert record["classification_state"] == "confirmed"


def test_nearby_variants_keep_stable_id_and_expand_aperture(tmp_path: Path) -> None:
    path = tmp_path / "navigation.json"
    model = WorldModel(path)
    first = ("room_a", 10, 20, "down", "room_b", 3, 2)
    nearby = ("room_a", 12, 20, "down", "room_b", 5, 2)

    portal_id = model.record_warp_transition(
        first,
        destination_was_novel=True,
    )
    assert model.record_warp_transition(
        nearby,
        destination_was_novel=False,
    ) == portal_id
    model.save()

    reloaded = WorldModel.load(path)
    record = reloaded.warp_portals[portal_id]
    assert record["crossings"] == 2
    assert record["source_footprint"] == {
        "bounds": [10, 20, 12, 20],
        "center": [11, 20],
        "sample_count": 2,
        "variant_count": 2,
    }
    assert record["aperture"]["span_cells"] == 3
    assert len(record["variants"]) == 2
    assert reloaded.portal_id_for_warp(nearby) == portal_id
    assert record["classification_version"] == WARP_CLASSIFICATION_VERSION


def test_version_two_memory_migrates_without_inventing_portal_meaning(
    tmp_path: Path,
) -> None:
    path = tmp_path / "navigation.json"
    old_warp = {
        "from_room": "room_a",
        "from_x": 7,
        "from_y": 9,
        "action": "left",
        "to_room": "room_b",
        "to_x": 20,
        "to_y": 9,
        "count": 3,
    }
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "cell_size": 8,
                "cells": [],
                "warps": [old_warp],
            }
        ),
        encoding="utf-8",
    )

    model = WorldModel.load(path)

    assert model.load_warning is None
    assert len(model.warp_portals) == 1
    record = next(iter(model.warp_portals.values()))
    assert record["role"] == "unknown"
    assert record["semantic_role"] == "unknown"
    assert record["classification_version"] == WARP_CLASSIFICATION_VERSION
    assert record["crossings"] == 3
    assert record["first_novel_destination"] is None
    assert record["non_discovery_progress_outcomes"] == 0

    model.save()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["version"] == 3
    assert saved["warps"] == [old_warp]
    assert len(saved["warp_portals"]) == 1


def test_legacy_optional_role_is_recomputed_from_observed_counters(tmp_path: Path) -> None:
    path = tmp_path / "navigation.json"
    path.write_text(
        json.dumps(
            {
                "version": 3,
                "cell_size": 8,
                "cells": [],
                "warps": [],
                "warp_portals": [
                    {
                        "id": "portal_legacy",
                        "from_room": "room_a",
                        "to_room": "room_b",
                        "action": "right",
                        "role": "likely_optional",
                        "confidence": 0.88,
                        "crossings": 2,
                        "first_novel_destination": "room_b",
                        "immediate_returns": 1,
                        "return_backtracks": 0,
                        "dwell_samples": 1,
                        "dwell_steps_total": 5,
                        "source_samples": [{"x": 5, "y": 5, "count": 2}],
                        "arrival_samples": [{"x": 1, "y": 5, "count": 2}],
                        "variants": [
                            {
                                "from_x": 5,
                                "from_y": 5,
                                "to_x": 1,
                                "to_y": 5,
                                "action": "right",
                                "count": 2,
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    model = WorldModel.load(path)
    record = model.warp_portals["portal_legacy"]

    assert record["role"] == "unknown"
    assert record["semantic_role"] == "new_area"
    assert "quick_return" in record["behavior_tags"]
    assert record["classification_version"] == WARP_CLASSIFICATION_VERSION


def test_outcomes_cannot_be_attached_to_an_unobserved_warp() -> None:
    model = WorldModel()
    hypothetical = ("room_a", 1, 1, "up", "room_secret", 2, 2)

    model.record_warp_progress(hypothetical, "hypothetical progress")
    model.record_warp_suppression(hypothetical)

    assert not model.warp_portals
    assert not model.warps


def test_visual_guess_geometry_and_lifecycle_survive_memory_reload(
    tmp_path: Path,
) -> None:
    path = tmp_path / "navigation.json"
    key = ("room_a", 3, 4)
    model = WorldModel(path)
    model.screen_regions[key] = {
        "views": 7,
        "independent_views": 3,
        "appearance_changes": 2,
        "interest": 0.61,
        "hypothesis": "possible_character",
        "guess_id": "room_a@3,4",
        "guess_state": "cooldown",
        "guess_model_version": 2,
        "approach_attempts": 2,
        "completed_tests": 1,
        "failed_approaches": 1,
        "cooldown_until_tick": 120,
        "choice_retry": True,
        "anchor_cell": [13, 17],
        "anchor_world": [108.0, 140.0],
        "obstruction_box_world": [104, 136, 112, 144],
        "evidence_viewpoints": [[0, 0], [8, 0]],
        "last_seen_sequence": 41,
        "last_failure_reason": "wrong interaction side",
    }

    model.save()
    record = WorldModel.load(path).screen_regions[key]

    assert record["guess_id"] == "room_a@3,4"
    assert record["guess_state"] == "cooldown"
    assert record["completed_tests"] == 1
    assert record["failed_approaches"] == 1
    assert record["choice_retry"] is True
    assert record["anchor_world"] == [108.0, 140.0]
    assert record["obstruction_box_world"] == [104, 136, 112, 144]
    assert record["evidence_viewpoints"] == [[0, 0], [8, 0]]
    assert record["last_seen_sequence"] == 41
