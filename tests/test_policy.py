from PIL import Image
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from deltarune_agent.actions import ACTIONS
from deltarune_agent.observer import Observation
from deltarune_agent.policy import (
    COLLISION_CONFIRM_SAMPLES,
    DIRECTION_VECTORS,
    StarterPolicy,
)
from deltarune_agent.perception import GameState, Perception, VisualFeatures
from deltarune_agent.telemetry import TelemetrySample


def test_policy_returns_known_action():
    observation = Observation(Image.new("RGB", (2, 2)), step=0)
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    perception = Perception(GameState.OVERWORLD, 0.5, features)
    action = StarterPolicy(seed=7).choose(observation, perception)
    assert action in ACTIONS.values()


def test_policy_turns_after_failed_move_and_interaction():
    observation = Observation(Image.new("RGB", (2, 2)), step=0)
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    perception = Perception(GameState.OVERWORLD, 0.99, features, "telemetry")
    sample = TelemetrySample("overworld", 1, "room_test", 100, 100, "obj_mainchara", 0)
    policy = StarterPolicy(seed=3)

    first = policy.choose(observation, perception, sample)
    policy.choose(observation, perception, replace(sample, received_at=1))
    policy.choose(observation, perception, replace(sample, received_at=2))
    interaction = policy.choose(
        observation, perception, replace(sample, received_at=3)
    )
    turned = policy.choose(observation, perception, replace(sample, received_at=4))

    assert first.name in DIRECTION_VECTORS
    assert interaction.name == "confirm"
    assert turned.name in DIRECTION_VECTORS
    assert turned.name != first.name


def test_completed_dialogue_is_not_interacted_with_twice():
    observation = Observation(Image.new("RGB", (2, 2)), step=0)
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    overworld = Perception(GameState.OVERWORLD, 0.99, features, "telemetry")
    dialogue = Perception(GameState.DIALOGUE, 0.99, features, "telemetry")
    player = TelemetrySample(
        "overworld",
        1,
        "room_test",
        100,
        100,
        "obj_mainchara",
        0,
        4,
        nearest_interactable_name="obj_interactable",
        nearest_interactable_id=100123,
        nearest_interactable_distance=12,
    )
    writer = TelemetrySample("dialogue", 1, "room_test", 0, 0, "obj_writer", 0, 2)
    policy = StarterPolicy()

    policy.choose(observation, overworld, player)
    policy.choose(observation, overworld, replace(player, received_at=1))
    policy.choose(observation, overworld, replace(player, received_at=2))
    first_interaction = policy.choose(
        observation, overworld, replace(player, received_at=3)
    )
    policy.choose(observation, dialogue, writer)
    forward_test = policy.choose(
        observation, overworld, replace(player, received_at=5)
    )
    turn = policy.choose(observation, overworld, replace(player, received_at=6))

    assert first_interaction.name == "confirm"
    assert forward_test.name in DIRECTION_VECTORS
    assert turn.name in DIRECTION_VECTORS
    assert turn.name != "confirm"
    assert len(policy.interacted_zones) == 1
    assert ("room_test", 100123) in policy.interacted_instances


def test_completed_object_is_recognized_from_opposite_side_without_v4_id():
    policy = StarterPolicy()
    policy.interaction_candidate = (
        "room_test",
        5,
        5,
        "right",
        None,
        None,
        6,
        5,
    )
    policy._complete_pending_interaction()

    assert policy._interacted_near("room_test", (7, 5), "left")


def test_room_name_does_not_change_exploration_policy():
    observation = Observation(Image.new("RGB", (2, 2)), step=0)
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    overworld = Perception(GameState.OVERWORLD, 0.99, features, "telemetry")
    kris = TelemetrySample("overworld", 1, "room_krisroom", 213, 96, "obj_mainchara", 0)
    unknown = TelemetrySample("overworld", 2, "room_unknown", 213, 96, "obj_mainchara", 0)

    assert StarterPolicy().choose(observation, overworld, kris).name == (
        StarterPolicy().choose(observation, overworld, unknown).name
    )


def test_known_blockage_is_not_interacted_with_again():
    observation = Observation(Image.new("RGB", (2, 2)), step=0)
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    overworld = Perception(GameState.OVERWORLD, 0.99, features, "telemetry")
    sample = TelemetrySample("overworld", 1, "room_test", 100, 100, "obj_mainchara", 0)
    policy = StarterPolicy()
    cell = policy._cell(sample)
    policy._remember_blocked("room_test", cell, "down")
    policy.last_movement = "down"
    policy.last_position = (100, 100)
    policy.last_room = "room_test"
    policy.last_cell = cell
    policy.stationary_key = ("room_test", 100, 100, "down")
    policy.stationary_streak = 2
    policy.last_movement_sample_at = -1

    action = policy.choose(observation, overworld, sample)

    assert action.name in DIRECTION_VECTORS
    assert action.name != "confirm"
    assert "known blocked down" in policy.reason


def test_room_warp_and_map_memory_survive_restart():
    observation = Observation(Image.new("RGB", (2, 2)), step=0)
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    overworld = Perception(GameState.OVERWORLD, 0.99, features, "telemetry")

    with TemporaryDirectory() as directory:
        path = Path(directory) / "navigation.json"
        policy = StarterPolicy(memory_path=path)
        source = TelemetrySample("overworld", 1, "room_a", 100, 100, "obj_mainchara", 0)
        target = TelemetrySample("overworld", 2, "room_b", 20, 30, "obj_mainchara", 0)
        policy.choose(observation, overworld, source)
        moved = TelemetrySample(
            "overworld", 1, "room_a", 100, 118, "obj_mainchara", 1
        )
        policy.choose(observation, overworld, moved)
        policy.choose(observation, overworld, replace(target, received_at=2))
        policy._remember_blocked("room_a", policy._cell(source), "left")
        policy.save_memory()

        reloaded = StarterPolicy(memory_path=path)

        assert reloaded.warps
        assert reloaded.open_edges
        assert ("room_a", "room_b") in reloaded.transitions
        assert reloaded._blocked_near("room_a", reloaded._cell(source), "left")


def test_exhausted_cell_routes_back_to_nearest_frontier():
    policy = StarterPolicy()
    room = "room_test"
    start = (1, 1)
    target = (2, 1)
    for direction in DIRECTION_VECTORS:
        policy.tried.add((room, *start, direction))
    policy.open_edges.add((room, *start, "right", *target))

    assert policy._route_to_nearest_frontier(room, start) == "right"


def test_recovery_never_immediately_repeats_confirmed_blocked_direction():
    policy = StarterPolicy()
    room = "room_test"
    cell = (9, 10)
    for direction in DIRECTION_VECTORS:
        policy.tried.add((room, *cell, direction))
    policy._remember_blocked(room, cell, "down")
    policy.open_edges.add((room, *cell, "down", 9, 11))
    policy.open_edges.add((room, 9, 11, "right", 10, 11))

    choice = policy._least_visited_direction(room, cell, "down", avoid={"down"})

    assert choice != "down"


def test_recovery_rotates_when_coarse_memory_marks_every_direction_blocked():
    policy = StarterPolicy()
    room = "room_test"
    cell = (4, 4)
    for direction in DIRECTION_VECTORS:
        policy._remember_blocked(room, cell, direction)

    choice = policy._least_visited_direction(room, cell, "left", avoid={"left"})

    assert choice in DIRECTION_VECTORS
    assert choice != "left"


def test_manual_sideways_displacement_is_not_learned_as_attempted_direction():
    policy = StarterPolicy()
    policy.last_movement = "down"
    policy.last_position = (100, 100)
    policy.last_room = "room_test"
    policy.last_cell = (6, 6)
    moved_sideways = TelemetrySample(
        "overworld", 1, "room_test", 116, 100, "obj_mainchara", 0
    )

    policy._learn_movement_result(moved_sideways)

    assert not policy.failed_movement
    assert not policy.open_edges


def test_route_ignores_edges_that_do_not_match_the_recorded_action():
    policy = StarterPolicy()
    room = "room_test"
    start = (1, 1)
    policy.open_edges.add((room, *start, "down", 2, 1))
    policy.open_edges.add((room, 2, 1, "right", 3, 1))

    assert policy._route_to_nearest_frontier(room, start) is None


def test_stationary_input_with_unmatched_facing_retries_without_marking_wall():
    observation = Observation(Image.new("RGB", (2, 2)), step=0)
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    overworld = Perception(GameState.OVERWORLD, 0.99, features, "telemetry")
    sample = TelemetrySample(
        "overworld",
        1,
        "room_test",
        100,
        100,
        "obj_mainchara",
        0,
        facing_direction="down",
    )
    policy = StarterPolicy()
    policy.last_movement = "right"
    policy.last_position = (100, 100)
    policy.last_room = "room_test"
    policy.last_cell = policy._cell(sample)

    action = policy.choose(observation, overworld, sample)

    assert action.name == "right"
    assert "retry right once" in policy.reason
    assert not policy.blocked


def test_repeated_unregistered_input_switches_without_poisoning_wall_map():
    observation = Observation(Image.new("RGB", (2, 2)), step=0)
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    overworld = Perception(GameState.OVERWORLD, 0.99, features, "telemetry")
    sample = TelemetrySample(
        "overworld",
        1,
        "room_test",
        100,
        100,
        "obj_mainchara",
        0,
        facing_direction="down",
    )
    policy = StarterPolicy()
    policy.last_movement = "right"
    policy.last_position = (100, 100)
    policy.last_room = "room_test"
    policy.last_cell = policy._cell(sample)

    policy.choose(observation, overworld, sample)
    switched = policy.choose(
        observation, overworld, replace(sample, received_at=1)
    )

    assert switched.name != "right"
    assert "input remained frozen" in policy.reason
    assert not policy.blocked


def test_wall_evidence_does_not_hide_an_adjacent_doorway():
    policy = StarterPolicy()
    room = "room_test"
    policy._remember_blocked(room, (9, 10), "down")

    assert policy._blocked_near(room, (9, 10), "down")
    assert not policy._blocked_near(room, (10, 10), "down")


def test_old_bedroom_blocks_do_not_force_horizontal_corridor_loop():
    policy = StarterPolicy()
    room = "room_krisroom"
    cell = (11, 8)
    policy._remember_blocked(room, (9, 10), "down")
    policy._remember_blocked(room, (13, 6), "up")
    policy._remember_blocked(room, (13, 6), "right")
    policy.tried.add((room, *cell, "left"))
    policy.tried.add((room, *cell, "right"))

    choice = policy._least_visited_direction(room, cell, "left")

    assert choice == "up"


def test_ordered_trace_records_consecutive_same_direction_room_warps():
    policy = StarterPolicy()
    policy.last_movement = "right"
    policy.observed_room = "room_a"
    policy.observed_cell = (10, 5)
    trace = [
        TelemetrySample("overworld", 2, "room_b", 16, 80, "obj_mainchara", 0),
        TelemetrySample("overworld", 2, "room_b", 304, 80, "obj_mainchara", 0),
        TelemetrySample("overworld", 3, "room_c", 16, 80, "obj_mainchara", 0),
    ]

    policy.observe_room_trace(trace)

    assert (
        "room_a",
        10,
        5,
        "right",
        "room_b",
        2,
        10,
    ) in policy.warps
    assert (
        "room_b",
        38,
        10,
        "right",
        "room_c",
        2,
        10,
    ) in policy.warps


def test_stale_telemetry_packet_cannot_create_blockage_evidence():
    observation = Observation(Image.new("RGB", (2, 2)), step=0)
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    overworld = Perception(GameState.OVERWORLD, 0.99, features, "telemetry")
    sample = TelemetrySample(
        "overworld",
        1,
        "room_test",
        100,
        100,
        "obj_mainchara",
        10,
        facing_direction="down",
    )
    policy = StarterPolicy()
    policy.last_movement = "down"
    policy.last_position = (100, 100)
    policy.last_room = "room_test"
    policy.last_cell = policy._cell(sample)
    policy.last_movement_sample_at = 10

    action = policy.choose(observation, overworld, sample)

    assert action.name == "down"
    assert policy.stationary_streak == 0
    assert "await fresh telemetry" in policy.reason
    assert not policy.blocked


def test_collision_requires_three_fresh_stationary_packets():
    observation = Observation(Image.new("RGB", (2, 2)), step=0)
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    overworld = Perception(GameState.OVERWORLD, 0.99, features, "telemetry")
    base = TelemetrySample(
        "overworld",
        1,
        "room_test",
        100,
        100,
        "obj_mainchara",
        0,
        facing_direction="down",
    )
    policy = StarterPolicy()
    policy.last_movement = "down"
    policy.last_position = (100, 100)
    policy.last_room = "room_test"
    policy.last_cell = policy._cell(base)
    policy.last_movement_sample_at = 0

    probes = [
        policy.choose(
            observation, overworld, replace(base, received_at=received_at)
        )
        for received_at in range(1, COLLISION_CONFIRM_SAMPLES + 1)
    ]

    assert all(action.name == "down" for action in probes[:-1])
    assert probes[-1].name == "confirm"
    assert policy.stationary_streak == COLLISION_CONFIRM_SAMPLES
    assert not policy.blocked


def test_successful_movement_removes_contradicted_wall_evidence():
    policy = StarterPolicy()
    room = "room_test"
    source = (6, 6)
    policy._remember_blocked(room, source, "right")
    policy.drain_map_updates()
    policy.last_movement = "right"
    policy.last_position = (100, 100)
    policy.last_room = room
    policy.last_cell = source
    policy.last_movement_sample_at = 0
    moved = TelemetrySample(
        "overworld", 1, room, 116, 100, "obj_mainchara", 1,
        facing_direction="right",
    )

    policy._learn_movement_result(moved)
    updates = policy.drain_map_updates()

    assert (room, *source, "right") not in policy.blocked
    assert any(update["type"] == "unblocked" for update in updates)


def test_confirmed_interactable_persists_in_world_memory():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "navigation.json"
        policy = StarterPolicy(memory_path=path)
        policy.interaction_candidate = (
            "room_test",
            5,
            5,
            "right",
            100123,
            "obj_bookshelf",
            6,
            5,
        )
        policy._complete_pending_interaction()
        policy.save_memory()

        reloaded = StarterPolicy(memory_path=path)

    record = reloaded.interactables[("room_test", 6, 5)]
    assert record["name"] == "obj_bookshelf"
    assert record["instance_id"] == 100123


def test_reverse_of_observed_path_is_known_not_a_new_frontier():
    policy = StarterPolicy()
    policy._remember_open_path("room_test", (5, 5), "right", (6, 5))

    assert not policy._direction_is_unexplored("room_test", (6, 5), "left")


def test_packet_gap_is_recorded_as_adjacent_edges_without_diagonals_or_leaps():
    policy = StarterPolicy()
    policy._remember_open_path("room_test", (2, 3), "right", (5, 3))

    assert ("room_test", 2, 3, "right", 3, 3) in policy.open_edges
    assert ("room_test", 3, 3, "right", 4, 3) in policy.open_edges
    assert ("room_test", 4, 3, "right", 5, 3) in policy.open_edges
    assert ("room_test", 2, 3, "right", 5, 3) not in policy.open_edges


def test_warp_navigation_uses_only_previously_discovered_transitions():
    policy = StarterPolicy()
    room = "room_test"
    start = (2, 3)
    endpoint = (5, 3)
    policy._remember_open_path(room, start, "right", endpoint)

    assert policy._route_to_learned_warp(room, start) is None

    warp = (room, *endpoint, "right", "room_next", 1, 3)
    policy.warps[warp] = 1

    assert policy._route_to_learned_warp(room, start) == ("right", warp)


def test_exhausted_room_follows_a_discovered_warp_instead_of_wandering():
    observation = Observation(Image.new("RGB", (2, 2)), step=100)
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    overworld = Perception(GameState.OVERWORLD, 0.99, features, "telemetry")
    policy = StarterPolicy()
    room = "room_test"
    start = (2, 3)
    endpoint = (5, 3)
    policy._remember_open_path(room, start, "right", endpoint)
    for x in range(start[0], endpoint[0] + 1):
        policy.seen_cells.add((room, x, start[1]))
        for direction in DIRECTION_VECTORS:
            policy.tried.add((room, x, start[1], direction))
    warp = (room, *endpoint, "right", "room_next", 1, 3)
    policy.warps[warp] = 1
    policy.steps_without_frontier = 20
    policy.last_room = room
    telemetry = TelemetrySample(
        "overworld",
        1,
        room,
        start[0] * 8,
        start[1] * 8,
        "obj_mainchara",
        1,
        facing_direction="right",
    )

    action = policy.choose(observation, overworld, telemetry)

    assert action.name == "right"
    assert "follow learned warp to room_next" in policy.reason


def test_interactable_is_not_exposed_on_map_until_interaction_succeeds():
    observation = Observation(Image.new("RGB", (2, 2)), step=0)
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    overworld = Perception(GameState.OVERWORLD, 0.99, features, "telemetry")
    sample = TelemetrySample(
        "overworld",
        1,
        "room_test",
        80,
        80,
        "obj_mainchara",
        0,
        facing_direction="down",
        nearest_interactable_name="obj_bookshelf",
        nearest_interactable_id=100123,
        nearest_interactable_x=80,
        nearest_interactable_y=88,
        nearest_interactable_distance=8,
    )
    policy = StarterPolicy()
    policy.last_movement = "down"
    policy.last_position = (80, 80)
    policy.last_room = "room_test"
    policy.last_cell = policy._cell(sample)
    policy.last_movement_sample_at = 0

    for received_at in range(1, COLLISION_CONFIRM_SAMPLES + 1):
        action = policy.choose(
            observation,
            overworld,
            replace(sample, received_at=received_at),
        )

    assert action.name == "confirm"
    assert policy.interaction_candidate is not None
    assert not policy.interactables
    assert not any(
        update["type"] == "interactable" for update in policy.drain_map_updates()
    )


def test_nearby_approaches_to_a_completed_object_are_not_interacted_again():
    policy = StarterPolicy()
    policy.interaction_candidate = (
        "room_test",
        10,
        10,
        "right",
        None,
        None,
        11,
        10,
    )
    policy._complete_pending_interaction()

    assert policy._interacted_near("room_test", (16, 10), "left")


def test_nearby_objects_with_different_verified_ids_remain_distinct():
    policy = StarterPolicy()
    policy.interaction_candidate = (
        "room_test",
        10,
        10,
        "right",
        100001,
        "first",
        11,
        10,
    )
    policy._complete_pending_interaction()
    policy.interaction_candidate = (
        "room_test",
        12,
        10,
        "right",
        100002,
        "second",
        13,
        10,
    )
    policy._complete_pending_interaction()

    assert len(policy.interactables) == 2


def test_version_one_map_is_migrated_to_finer_adjacent_cells():
    data = {
        "version": 1,
        "cells": [{"room": "room_test", "x": 1, "y": 2, "visits": 3}],
        "tried_edges": [],
        "blocked_edges": [],
        "open_edges": [
            {
                "room": "room_test",
                "from_x": 1,
                "from_y": 2,
                "direction": "right",
                "to_x": 3,
                "to_y": 2,
            }
        ],
        "warps": [],
        "interactables": [],
    }
    with TemporaryDirectory() as directory:
        path = Path(directory) / "navigation.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        policy = StarterPolicy(memory_path=path)

    assert ("room_test", 2, 4) in policy.seen_cells
    assert ("room_test", 2, 4, "right", 3, 4) in policy.open_edges
    assert ("room_test", 5, 4, "right", 6, 4) in policy.open_edges
    assert ("room_test", 2, 4, "right", 6, 4) not in policy.open_edges


def test_routing_to_intermediate_frontier_replans_after_one_sample():
    policy = StarterPolicy()
    room = "room_test"
    bottom = (7, 15)
    policy._remember_open_path(room, (7, 12), "down", bottom)
    for direction in DIRECTION_VECTORS:
        policy.tried.add((room, *bottom, direction))

    direction, commitment, reason = policy._plan_exploration(room, bottom)

    assert direction == "up"
    assert commitment == 1
    assert reason == "route to mapped frontier up"


def test_repeated_two_endpoint_corridor_loop_chooses_side_exit():
    policy = StarterPolicy()
    room = "room_test"
    bottom = (7, 15)
    top = (7, 12)
    policy._remember_open_path(room, bottom, "right", (8, 15))
    policy._remember_blocked(room, bottom, "left")
    policy.decision_history.extend(
        [
            (room, *bottom, "up"),
            (room, *top, "down"),
            (room, *bottom, "up"),
            (room, *top, "down"),
        ]
    )

    direction, broke_loop = policy._break_oscillation(room, bottom, "up")

    assert broke_loop
    assert direction == "right"
    assert policy.oscillation_breaks == 1
