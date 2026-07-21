from deltarune_agent.run4_explorer import (
    EXIT_PRIORITY_COOLDOWN_STEPS,
    EXIT_PRIORITY_EPISODE_STEPS,
    MAX_FLAVOR_INTERACTIONS_BEFORE_EXIT,
    ROOM_EXIT_PRIORITY_MIN_CELLS,
    Run4Explorer,
)


def _strong_visual_exit():
    return {
        "hypothesis": "possible_exit",
        "guess_state": "proposed",
        "guess_confidence": 0.72,
        "edge_opening_score": 0.68,
        "edge_width_ratio": 0.3,
        "completed_tests": 0,
        "failed_approaches": 0,
        "inspections": 0,
    }


def test_exit_priority_activates_after_flavor_interactions():
    explorer = Run4Explorer()
    room = "room_test"
    explorer.screen_regions[(room, 1, 1)] = _strong_visual_exit()
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
    explorer.screen_regions[(room, 2, 2)] = _strong_visual_exit()
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


def test_one_wrong_side_does_not_retire_strong_character_candidate():
    explorer = Run4Explorer()
    room = "room_test"
    key = (room, 4, 4)
    explorer.screen_regions[key] = {
        "hypothesis": "possible_character",
        "inspections": 1,
        "completed_tests": 1,
        "failed_approaches": 0,
        "views": 4,
        "interest": 0.5,
        "entity_approach_directions": 3,
        "obstruction_target_cells": 2,
    }

    explorer._retire_weak_character_hypotheses(room)

    assert explorer.screen_regions[key]["hypothesis"] == "possible_character"
    assert explorer.retired_weak_character_leads == 0


def test_priority_exit_probe_commits_from_adjacent_approach_cell():
    explorer = Run4Explorer()
    room = "room_test"
    explorer._remember_open_path(room, (2, 5), "right", (5, 5))
    explorer.seen_cells.update((room, x, 5) for x in range(2, 6))

    direction, commitment, reason = explorer._prioritized_exit_plan(room, (3, 5))

    assert direction == "left"
    assert commitment > 1
    assert "commit to strong mapped passage" in reason
    assert explorer.exit_probes[(room, 2, 5, "left")] == 1


def test_broad_dark_visual_seam_does_not_activate_exit_priority():
    explorer = Run4Explorer()
    room = "room_test"
    explorer.story_stall_steps = 500
    record = _strong_visual_exit()
    record.update(
        {
            "edge_opening_score": 0.18,
            "edge_width_ratio": 0.91,
        }
    )
    explorer.screen_regions[(room, 1, 1)] = record

    assert not explorer._has_exit_lead(room)
    assert not explorer._exit_priority_active(room)


def test_exit_priority_episode_expires_and_cools_down_failed_visual():
    explorer = Run4Explorer()
    room = "room_test"
    key = (room, 1, 1)
    explorer.story_stall_steps = 500
    explorer.screen_regions[key] = _strong_visual_exit()
    explorer.visual_goal = key

    assert explorer._exit_priority_active(room)
    explorer.navigation_tick = EXIT_PRIORITY_EPISODE_STEPS

    assert not explorer._exit_priority_active(room)
    assert explorer.exit_priority_timeouts == 1
    assert explorer.screen_regions[key]["failed_approaches"] == 1
    assert explorer.screen_regions[key]["guess_state"] == "cooldown"
    assert (
        explorer.exit_priority_cooldown_until[room]
        == EXIT_PRIORITY_EPISODE_STEPS + EXIT_PRIORITY_COOLDOWN_STEPS
    )


def test_exit_priority_counts_episodes_instead_of_each_step():
    explorer = Run4Explorer()
    room = "room_test"
    explorer.story_stall_steps = 500
    explorer.screen_regions[(room, 1, 1)] = _strong_visual_exit()

    for tick in range(EXIT_PRIORITY_EPISODE_STEPS):
        explorer.navigation_tick = tick
        assert explorer._exit_priority_active(room)

    assert explorer.exit_priority_activations == 1


def test_observed_warp_outranks_visual_exit_in_priority_plan():
    explorer = Run4Explorer()
    room = "room_test"
    explorer.open_edges.update(
        {
            (room, 1, 1, "right", 2, 1),
            (room, 2, 1, "left", 1, 1),
        }
    )
    warp = (room, 2, 1, "right", "room_next", 1, 1)
    explorer.warps[warp] = 3
    explorer.screen_regions[(room, 0, 0)] = _strong_visual_exit()

    direction, commitment, reason = explorer._prioritized_exit_plan(
        room,
        (1, 1),
    )

    assert direction == "right"
    assert commitment == 1
    assert "observed unknown warp to room_next" in reason
    assert explorer.priority_known_warp_steps == 1
