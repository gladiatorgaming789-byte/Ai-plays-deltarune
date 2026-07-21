from deltarune_agent.run4_explorer import (
    MAX_FLAVOR_INTERACTIONS_BEFORE_EXIT,
    ROOM_EXIT_PRIORITY_MIN_CELLS,
    Run4Explorer,
)


def test_exit_priority_activates_after_flavor_interactions():
    explorer = Run4Explorer()
    room = "room_test"
    explorer.screen_regions[(room, 1, 1)] = {
        "hypothesis": "possible_exit",
        "inspections": 0,
    }
    for index in range(MAX_FLAVOR_INTERACTIONS_BEFORE_EXIT):
        explorer.interactables[(room, index, 0)] = {
            "usefulness": "flavor",
        }

    assert explorer._exit_priority_active(room)


def test_exit_priority_activates_in_well_mapped_stalled_room():
    explorer = Run4Explorer()
    room = "room_test"
    explorer.room_entered_at[room] = 0
    explorer.navigation_tick = 300
    explorer.story_stall_steps = 200
    explorer.screen_regions[(room, 2, 2)] = {
        "hypothesis": "possible_exit",
        "inspections": 0,
    }
    explorer.seen_cells.update(
        (room, index, 0)
        for index in range(ROOM_EXIT_PRIORITY_MIN_CELLS)
    )

    assert explorer._exit_priority_active(room)


def test_weak_character_lead_is_retired_when_exit_has_priority():
    explorer = Run4Explorer()
    room = "room_test"
    key = (room, 4, 4)
    explorer.screen_regions[key] = {
        "hypothesis": "possible_character",
        "inspections": 1,
        "views": 2,
        "interest": 0.2,
        "entity_approach_directions": 1,
        "obstruction_target_cells": 8,
    }

    explorer._retire_weak_character_hypotheses(room)

    assert explorer.screen_regions[key]["hypothesis"] is None
    assert explorer.screen_regions[key]["inspections"] >= 3
    assert explorer.retired_weak_character_leads == 1
