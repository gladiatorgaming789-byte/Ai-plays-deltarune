from __future__ import annotations

from collections import Counter
from pathlib import Path

from deltarune_agent.run16_semantics import (
    classify_portal,
    install_run16_semantics,
    repair_portal_action_conflicts,
)

install_run16_semantics()

from deltarune_agent.run16_explorer import Run16Explorer
from deltarune_agent.world_model import WorldModel


def test_event_transition_is_automatic_not_navigable():
    role, confidence, basis = classify_portal(
        {
            "action": "event",
            "crossings": 2,
            "first_novel_destination": "room_next",
        }
    )

    assert role == "automatic_sequence"
    assert confidence >= 0.9
    assert any("not a navigable doorway" in value for value in basis)


def test_round_trip_outbound_portal_remains_semantically_reversible():
    record = {
        "action": "down",
        "crossings": 2,
        "first_novel_destination": "room_side",
        "round_trip_returns": 1,
    }
    role, confidence, _basis = classify_portal(
        record
    )

    assert role == "unknown"
    assert confidence >= 0.3


def test_conflicting_bottom_boundary_actions_use_room_geometry():
    world = WorldModel()
    world.room_dimensions = {"room_a": (320.0, 240.0)}
    world.warps = Counter(
        {
            ("room_a", 21, 29, "left", "room_b", 37, 18): 1,
            ("room_a", 21, 29, "down", "room_b", 37, 18): 2,
        }
    )

    repaired = repair_portal_action_conflicts(world)

    assert repaired >= 1
    matching = [
        warp
        for warp in world.warps
        if warp[0] == "room_a" and warp[4] == "room_b"
    ]
    assert len(matching) == 1
    assert matching[0][3] == "down"


def test_run16_fields_survive_save_and_reload(tmp_path: Path):
    path = tmp_path / "navigation.json"
    world = WorldModel(path)
    world.room_dimensions = {"room_test": (320.0, 240.0)}
    world.seen_cells.add(("room_test", 1, 1))
    world.visits[("room_test", 1, 1)] = 1
    world.screen_regions[("room_test", 2, 3)] = {
        "views": 1,
        "independent_views": 1,
        "interest": 0.4,
        "hypothesis": None,
        "inspections": 2,
        "completed_tests": 2,
        "approach_attempts": 20,
        "failed_approaches": 2,
        "guess_state": "retired",
        "retired_reason": (
            "visual lead was reselected too many times without reaching a concrete test"
        ),
        "animated_bonus_applied": True,
        "lifecycle_locked": True,
        "motion_sprite_candidate": True,
    }

    world.save()
    loaded = WorldModel.load(path)

    record = loaded.screen_regions[("room_test", 2, 3)]
    assert loaded.room_dimensions["room_test"] == (320.0, 240.0)
    assert record["animated_bonus_applied"] is True
    assert record["lifecycle_locked"] is True
    assert record["motion_sprite_candidate"] is True


def _candidate_record():
    return {
        "views": 8,
        "independent_views": 2,
        "interest": 0.34,
        "hypothesis": None,
        "inspections": 0,
        "completed_tests": 0,
        "approach_attempts": 0,
        "failed_approaches": 0,
        "guess_state": "proposed",
        "motion": 6.0,
        "colorfulness": 0.21,
        "dark_ratio": 0.2,
        "feature_box_world": [70.0, 130.0, 88.0, 154.0],
    }


def test_motion_sprite_requires_observed_dialogue_ecology():
    explorer = Run16Explorer()
    key = ("room_test", 2, 4)
    explorer.screen_regions[key] = _candidate_record()

    explorer._promote_motion_sprite_candidates("room_test")

    assert explorer.screen_regions[key]["hypothesis"] is None


def test_motion_sprite_promotes_after_multiple_observed_dialogue_interactions():
    explorer = Run16Explorer()
    key = ("room_test", 2, 4)
    explorer.screen_regions[key] = _candidate_record()
    explorer.interactables[("room_test", 4, 4)] = {
        "classification": "tested_nonchoice"
    }
    explorer.interactables[("room_test", 8, 4)] = {
        "classification": "confirmed_npc"
    }

    explorer._promote_motion_sprite_candidates("room_test")

    record = explorer.screen_regions[key]
    assert record["hypothesis"] == "possible_character"
    assert record["motion_sprite_candidate"] is True
    assert record["guess_confidence"] >= 0.48


def test_automatic_transition_never_becomes_priority_warp():
    explorer = Run16Explorer()
    warp = ("room_a", 1, 1, "event", "room_b", 2, 2)
    explorer.world.warps[warp] = 1
    explorer.world.reconcile_warp_portals()

    assert explorer._warp_is_priority_candidate(warp) is False
