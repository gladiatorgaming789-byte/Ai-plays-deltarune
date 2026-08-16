from PIL import Image, ImageDraw
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory

# StarterPolicy is patched by the shipped detector stack. Load that stack
# explicitly so this integration suite is independent of pytest collection order.
import deltarune_agent.hierarchical_policy  # noqa: F401

from deltarune_agent.actions import ACTIONS
from deltarune_agent.entity_detection_v2 import entity_candidate_state
from deltarune_agent.observer import Observation
from deltarune_agent.policy import (
    CHOICE_PATTERNS,
    COLLISION_CONFIRM_SAMPLES,
    DIRECTION_VECTORS,
    EXIT_PROBE_COMMIT_STEPS,
    MIN_VISUAL_GUESS_CONFIDENCE,
    STORY_SEARCH_STEPS,
    StarterPolicy,
    WARP_SEEK_STEPS,
)
from deltarune_agent.perception import GameState, Perception, VisualFeatures
from deltarune_agent.telemetry import TelemetrySample


def _visible_choice_frame() -> Image.Image:
    frame = Image.new("RGB", (320, 240), "black")
    draw = ImageDraw.Draw(frame)
    points = (
        (2, 0), (2, 1), (0, 2), (1, 2), (2, 2), (3, 2), (4, 2), (2, 3), (2, 4)
    )
    for marker_y in (178, 205):
        for dx, dy in points:
            draw.point((28 + dx, marker_y + dy), fill="white")
    return frame


def test_policy_returns_known_action():
    observation = Observation(Image.new("RGB", (2, 2)), step=0)
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    perception = Perception(GameState.OVERWORLD, 0.5, features)
    action = StarterPolicy(seed=7).choose(observation, perception)
    assert action in ACTIONS.values()


def test_interaction_cooldowns_block_repeated_probe_attempts():
    policy = StarterPolicy(seed=0)
    policy._mark_interaction_cooldown("room_test", (10, 10), "left")

    assert policy._interaction_is_cooldown("room_test", (10, 10), "left")
    assert not policy._interaction_is_cooldown("room_test", (10, 10), "right")


def test_exploration_score_prefers_less_visited_targets():
    policy = StarterPolicy(seed=0)
    policy.visits[("room_test", 11, 10)] = 4
    policy.visits[("room_test", 9, 10)] = 1

    left_score = policy._exploration_direction_score("room_test", (10, 10), "left")
    right_score = policy._exploration_direction_score("room_test", (10, 10), "right")

    assert left_score < right_score


def test_progress_pressure_prioritizes_story_search_when_frontier_is_stalled():
    policy = StarterPolicy(seed=0)
    policy.steps_without_frontier = WARP_SEEK_STEPS
    policy.story_stall_steps = STORY_SEARCH_STEPS

    assert policy._progress_pressure("room_test", (0, 0))


def test_visual_hypothesis_prefers_stronger_evidence_over_closer_but_weaker_guess():
    policy = StarterPolicy(seed=0)
    policy.current_visible_regions = {("room_test", 0, 0), ("room_test", 1, 0)}
    policy.screen_regions[("room_test", 0, 0)] = {
        "hypothesis": "possible_exit",
        "guess_semantic_state": "possible_exit",
        "guess_confidence": 0.61,
        "exit_detection_version": 2,
        "exit_candidate_source": "doorway_facade",
        "exit_candidate_state": "semantic_ready",
        "interest": 0.1,
        "inspections": 0,
        "views": 1,
        "last_seen_sequence": 1,
    }
    policy.screen_regions[("room_test", 1, 0)] = {
        "hypothesis": "possible_character",
        "guess_semantic_state": "possible_character",
        "guess_confidence": 0.78,
        "interest": 0.8,
        "inspections": 0,
        "views": 4,
        "last_seen_sequence": 4,
        "entity_approach_directions": 2,
        "obstruction_target_cells": 2,
    }

    plan = policy._direction_to_visual_hypothesis("room_test", (0, 0))

    assert plan is not None
    assert plan[1] == "possible_character"


def test_cutscene_advances_without_learning_a_blockage():
    observation = Observation(Image.new("RGB", (2, 2)), step=0)
    features = VisualFeatures(0, 0, 0, 0, 0)
    cutscene = Perception(GameState.CUTSCENE, 0.96, features, "telemetry-context")
    policy = StarterPolicy()

    action = policy.choose(observation, cutscene)

    assert action.name == "confirm"
    assert policy.reason == "advance detected cutscene"
    assert not policy.blocked


def test_policy_skips_unidentified_obstacle_instead_of_interacting():
    observation = Observation(Image.new("RGB", (2, 2)), step=0)
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    perception = Perception(GameState.OVERWORLD, 0.99, features, "telemetry")
    sample = TelemetrySample("overworld", 1, "room_test", 100, 100, "obj_mainchara", 0)
    policy = StarterPolicy(seed=3)

    first = policy.choose(observation, perception, sample)
    policy.choose(observation, perception, replace(sample, received_at=1))
    policy.choose(observation, perception, replace(sample, received_at=2))
    skipped = policy.choose(
        observation, perception, replace(sample, received_at=3)
    )

    assert first.name in DIRECTION_VECTORS
    assert skipped.name in DIRECTION_VECTORS
    assert skipped.name != first.name
    assert "skip interaction" in policy.reason
    assert policy.interaction_candidate is None


def test_stalled_policy_tries_a_new_direction_to_escape_repetition():
    policy = StarterPolicy(seed=0)

    policy.steps_without_frontier = WARP_SEEK_STEPS
    policy.decision_history.extend(
        [
            ("room_test", 100, 100, "down"),
            ("room_test", 100, 100, "down"),
            ("room_test", 100, 100, "down"),
        ]
    )
    policy.direction = "down"

    reason, direction = policy._stalled_recovery("room_test", (100, 100), "down")

    assert direction == "left"
    assert "stalled recovery" in reason


def test_completed_dialogue_is_not_interacted_with_twice_without_hidden_id():
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

    first = policy.choose(observation, overworld, player)
    cell = policy._cell(player)
    target = policy._interaction_target(cell, first.name)
    goal = ("room_test", *policy._region(target))
    policy.visual_goal = goal
    policy.screen_regions[goal] = {"hypothesis": "possible_character"}
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
    assert not policy.interacted_instances
    assert policy.interacted_targets


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
        policy.room_entry_from["room_b"] = "room_a"
        policy.suppressed_room_links.add(frozenset(("room_a", "room_b")))
        policy.save_memory()

        reloaded = StarterPolicy(memory_path=path)

        assert reloaded.warps
        assert reloaded.open_edges
        assert ("room_a", "room_b") in reloaded.transitions
        assert reloaded._blocked_near("room_a", reloaded._cell(source), "left")
        assert reloaded.room_entry_from["room_b"] == "room_a"
        assert frozenset(("room_a", "room_b")) in reloaded.suppressed_room_links


def test_exhausted_cell_routes_back_to_nearest_frontier():
    policy = StarterPolicy()
    room = "room_test"
    start = (1, 1)
    target = (4, 1)
    for direction in DIRECTION_VECTORS:
        policy.tried.add((room, *start, direction))
    policy._remember_open_path(room, start, "right", target)

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


def test_v9_movement_learning_uses_collision_foot_not_sprite_origin():
    policy = StarterPolicy()
    initial = TelemetrySample(
        "overworld",
        1,
        "room_test",
        100,
        88,
        "obj_mainchara",
        1,
        version=9,
        facing_direction="down",
        player_foot_x=100,
        player_foot_y=100,
    )
    policy._select("down", "test movement", initial)

    # Animation may shift the instance/sprite origin without moving Kris's
    # collision point. That must count as stationary rather than an open edge.
    animated_origin = replace(
        initial,
        x=102,
        y=90,
        received_at=2,
    )
    policy._learn_movement_result(animated_origin)

    assert policy.stationary_streak == 1
    assert not policy.open_edges
    assert not policy.unexpected_displacement


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
    target = policy._interaction_target(policy.last_cell, "down")
    goal = ("room_test", *policy._region(target))
    policy.visual_goal = goal
    policy.screen_regions[goal] = {"hypothesis": "possible_character"}

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


def test_known_character_blockage_is_probed_instead_of_avoided():
    observation = Observation(Image.new("RGB", (2, 2)), step=0)
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    overworld = Perception(GameState.OVERWORLD, 0.99, features, "telemetry")
    base = TelemetrySample(
        "overworld", 1, "room_test", 72, 40, "obj_mainchara", 0,
        facing_direction="left",
    )
    policy = StarterPolicy()
    cell = policy._cell(base)
    policy.last_movement = "left"
    policy.last_position = (72, 40)
    policy.last_room = "room_test"
    policy.last_cell = cell
    policy.last_movement_sample_at = 0
    policy.blocked[("room_test", *cell, "left")] = 2
    target = policy._interaction_target(cell, "left")
    goal = ("room_test", *policy._region(target))
    policy.visual_goal = goal
    policy.screen_regions[goal] = {
        "hypothesis": "possible_character",
        "character_probe_version": 1,
    }

    for received_at in range(1, COLLISION_CONFIRM_SAMPLES + 1):
        action = policy.choose(
            observation,
            overworld,
            replace(base, received_at=received_at),
        )

    assert action.name == "confirm"
    assert policy.interaction_candidate is not None
    assert "try interaction" in policy.reason


def test_character_probe_rotates_after_facing_direction_gets_no_response():
    policy = StarterPolicy()
    room = "room_test"
    source = (10, 10)
    target_region = policy._region((10, 11))
    policy.blocked[(room, *source, "down")] = 1
    policy.blocked[(room, *source, "up")] = 1
    goal = (room, *target_region)
    policy.visual_goal = goal
    policy.screen_regions[goal] = {
        "hypothesis": "possible_character",
        "character_probe_version": 1,
    }

    first = policy._route_to_character_probe(room, source, target_region)
    assert first == "down"
    policy.interaction_candidate = (
        room,
        *source,
        first,
        None,
        None,
        *policy._interaction_target(source, first),
    )
    policy._remember_failed_character_probe()

    second = policy._route_to_character_probe(room, source, target_region)

    assert policy.character_probes[(room, *source, "down")] == 1
    assert second == "up"


def test_dialogue_response_does_not_mark_character_direction_failed():
    observation = Observation(Image.new("RGB", (2, 2)), step=0, visual_valid=False)
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    dialogue = Perception(GameState.DIALOGUE, 0.99, features, "telemetry")
    sample = TelemetrySample(
        "dialogue", 1, "room_test", 80, 80, "obj_writer", 1
    )
    policy = StarterPolicy()
    policy.visual_goal = ("room_test", 2, 2)
    policy.screen_regions[policy.visual_goal] = {
        "hypothesis": "possible_character"
    }
    policy.interaction_candidate = (
        "room_test", 10, 10, "down", None, None, 10, 11
    )
    policy.interaction_tried = True
    policy.pending_blocked_direction = "down"

    policy.choose(observation, dialogue, sample)

    assert not policy.character_probes


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
    assert record["attempts"] == 1
    assert record["progressions"] == 0


def test_interaction_learns_when_it_causes_a_scripted_sequence():
    observation = Observation(Image.new("RGB", (2, 2)), step=0, visual_valid=False)
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    dialogue = Perception(GameState.DIALOGUE, 0.99, features, "telemetry")
    cutscene = Perception(GameState.CUTSCENE, 0.99, features, "telemetry-sequence")
    overworld = Perception(GameState.OVERWORLD, 0.99, features, "telemetry")
    sample = TelemetrySample(
        "overworld", 1, "room_test", 40, 40, "obj_mainchara", 1
    )
    policy = StarterPolicy()
    policy.interaction_candidate = (
        "room_test", 5, 5, "right", None, None, 6, 5
    )

    policy.choose(observation, dialogue, sample)
    policy.choose(observation, cutscene, sample)
    policy.choose(observation, overworld, replace(sample, received_at=2))

    record = policy.interactables[("room_test", 6, 5)]
    assert record["dialogue_steps"] == 1
    assert record["cutscene_steps"] == 1
    assert record["progressions"] == 1
    assert record["usefulness"] == "progress"
    assert record["last_outcome"] == "scripted_sequence"
    assert record["outcome_counts"]["scripted_sequence"] == 1
    assert policy.story_progress_events == 1


def test_ordinary_dialogue_is_remembered_as_flavor_not_story_goal():
    observation = Observation(Image.new("RGB", (2, 2)), step=0, visual_valid=False)
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    dialogue = Perception(GameState.DIALOGUE, 0.99, features, "telemetry")
    overworld = Perception(GameState.OVERWORLD, 0.99, features, "telemetry")
    sample = TelemetrySample(
        "overworld", 1, "room_test", 40, 40, "obj_mainchara", 1
    )
    policy = StarterPolicy()
    policy.interaction_candidate = (
        "room_test", 5, 5, "right", None, None, 6, 5
    )

    policy.choose(observation, dialogue, sample)
    policy.choose(observation, overworld, replace(sample, received_at=2))

    key = ("room_test", 6, 5)
    record = policy.interactables[key]
    assert record["classification"] == "tested_nonchoice"
    assert record["usefulness"] == "flavor"
    assert record["last_outcome"] == "ordinary_dialogue"
    assert record["outcome_counts"]["ordinary_dialogue"] == 1
    assert not policy._story_interaction_retryable(key)


def test_interaction_finishes_after_an_unknown_state_gap():
    observation = Observation(Image.new("RGB", (2, 2)), step=0, visual_valid=False)
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    dialogue = Perception(GameState.DIALOGUE, 0.99, features, "telemetry")
    unknown = Perception(GameState.UNKNOWN, 0.0, features, "stale-capture")
    overworld = Perception(GameState.OVERWORLD, 0.99, features, "telemetry")
    sample = TelemetrySample(
        "overworld", 1, "room_test", 40, 40, "obj_mainchara", 1
    )
    policy = StarterPolicy()
    policy.interaction_candidate = (
        "room_test", 5, 5, "right", None, None, 6, 5
    )

    policy.choose(observation, dialogue, sample)
    policy.choose(observation, unknown, None)

    assert policy.active_interaction_key == ("room_test", 6, 5)

    policy.choose(observation, overworld, replace(sample, received_at=2))

    assert policy.active_interaction_key is None
    assert policy.interactables[("room_test", 6, 5)]["last_outcome"] == (
        "ordinary_dialogue"
    )


def test_interaction_that_starts_a_battle_is_story_progress():
    observation = Observation(Image.new("RGB", (2, 2)), step=0, visual_valid=False)
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    dialogue = Perception(GameState.DIALOGUE, 0.99, features, "telemetry")
    battle = Perception(GameState.BATTLE, 0.99, features, "telemetry")
    overworld = Perception(GameState.OVERWORLD, 0.99, features, "telemetry")
    sample = TelemetrySample(
        "overworld", 1, "room_test", 40, 40, "obj_mainchara", 1
    )
    policy = StarterPolicy()
    policy.interaction_candidate = (
        "room_test", 5, 5, "right", None, None, 6, 5
    )

    policy.choose(observation, dialogue, sample)
    policy.choose(observation, battle, replace(sample, mode="battle"))
    policy.choose(observation, overworld, replace(sample, received_at=2))

    record = policy.interactables[("room_test", 6, 5)]
    assert record["progressions"] == 1
    assert record["usefulness"] == "progress"
    assert record["last_outcome"] == "battle_started"
    assert policy.story_progress_events == 1


def test_story_search_prefers_unresolved_choice_over_unknown_interaction():
    policy = StarterPolicy()
    room = "room_test"
    start = (5, 5)
    choice_key = (room, 6, 5)
    unknown_key = (room, 5, 6)
    policy.interactables[choice_key] = {
        "attempts": 2,
        "progressions": 0,
        "choice_menus": 1,
        "classification": "confirmed_npc",
        "usefulness": "choice_pending",
        "approaches": [{"x": 5, "y": 5, "direction": "right"}],
    }
    policy.interactables[unknown_key] = {
        "attempts": 1,
        "progressions": 0,
        "choice_menus": 1,
        "classification": "confirmed_npc",
        "usefulness": "unknown",
        "approaches": [{"x": 5, "y": 5, "direction": "down"}],
    }
    for key in (choice_key, unknown_key):
        policy.choice_trials.append(
            {
                "room": room,
                "context_x": key[1],
                "context_y": key[2],
                "signature": str(key),
                "attempts": [1, 0, 0],
                "failures": [1, 0, 0],
                "successes": [0, 0, 0],
                "successful_pattern": None,
            }
        )

    route = policy._route_to_retryable_story_interaction(room, start)

    assert route == ("right", choice_key)


def test_story_search_can_prefer_static_character_candidate_over_visible_exit():
    policy = StarterPolicy()
    room = "room_test"
    start = (5, 5)
    policy.story_stall_steps = STORY_SEARCH_STEPS
    policy.current_visible_regions = {
        (room, 3, 1),
        (room, 1, 3),
    }
    policy.screen_regions[(room, 3, 1)] = {
        "views": 1,
        "interest": 0.9,
        "hypothesis": "possible_exit",
        "guess_semantic_state": "possible_exit",
        "guess_confidence": 0.68,
        "exit_detection_version": 2,
        "exit_candidate_source": "doorway_facade",
        "exit_candidate_state": "semantic_ready",
        "inspections": 0,
        "last_seen_sequence": 1,
    }
    policy.screen_regions[(room, 1, 3)] = {
        "views": 1,
        "interest": 0.3,
        "hypothesis": "possible_character",
        "guess_semantic_state": "possible_character",
        "guess_confidence": 0.72,
        "inspections": 0,
        "walkable_evidence": True,
        "entity_approach_directions": 2,
        "obstruction_target_cells": 2,
        "last_seen_sequence": 1,
    }
    policy._remember_open_path(room, start, "right", (11, 5))
    policy._remember_open_path(room, start, "down", (5, 11))

    direction, commitment, reason = policy._plan_exploration(room, start)

    assert direction == "down"
    assert commitment == 1
    assert "story search" in reason
    assert "possible character" in reason


def test_returning_choice_menu_tries_another_pattern_and_learns_success():
    observation = Observation(Image.new("RGB", (160, 120), (20, 20, 20)), step=0)
    sample = TelemetrySample(
        "choice",
        1,
        "room_test",
        10,
        10,
        "obj_choicer_neo",
        0,
        player_x=40,
        player_y=40,
    )
    policy = StarterPolicy()

    policy._start_choice_trial(observation, sample)
    record = policy.pending_choice_record
    assert record is not None
    assert policy.pending_choice_pattern == 0
    assert list(policy.menu_action_queue)[-1] == "confirm"

    policy._start_choice_trial(observation, sample)

    assert policy.pending_choice_record is record
    assert policy.pending_choice_pattern == 1
    assert record["failures"][0] == 1
    assert "down" in policy.menu_action_queue

    policy._record_story_progress("discovered a new room", sample)

    assert record["successful_pattern"] == 1
    assert record["successes"][1] == 1


def test_menu_policy_changes_active_response_before_confirming():
    observation = Observation(Image.new("RGB", (160, 120), (20, 20, 20)), step=0)
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    menu = Perception(GameState.MENU, 0.99, features, "telemetry")
    sample = TelemetrySample(
        "choice", 1, "room_test", 10, 10, "obj_choicer_old", 0,
        player_x=40, player_y=40,
    )
    policy = StarterPolicy()

    first = policy.choose(observation, menu, sample)

    assert first.name == "up"
    assert "choice trial" in policy.reason
    assert policy.menu_action_queue


def test_choice_waits_for_confirm_result_before_starting_another_trial():
    observation = Observation(Image.new("RGB", (160, 120), (20, 20, 20)), step=0)
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    menu = Perception(GameState.MENU, 0.99, features, "telemetry")
    sample = TelemetrySample(
        "choice", 1, "room_test", 10, 10, "obj_choicer_old", 0,
        player_x=40, player_y=40,
    )
    policy = StarterPolicy()
    policy.choose(observation, menu, sample)
    record = policy.active_choice_record
    assert record is not None
    policy.menu_action_queue.clear()
    attempts_before = list(record["attempts"])

    action = policy._choose_menu_action(observation, sample, menu_started=False)

    assert action.name == "wait"
    assert policy.choice_settle_steps == 1
    assert record["attempts"] == attempts_before


def test_choice_menu_stops_after_all_patterns_instead_of_looping_forever():
    observation = Observation(Image.new("RGB", (160, 120), (20, 20, 20)), step=0)
    sample = TelemetrySample(
        "choice",
        1,
        "room_test",
        10,
        10,
        "obj_choicer_old",
        0,
        player_x=40,
        player_y=40,
    )
    policy = StarterPolicy()
    policy._choose_menu_action(observation, sample, menu_started=True)

    while policy.choice_session_trials < len(CHOICE_PATTERNS):
        policy.menu_action_queue.clear()
        policy.choice_settle_steps = 2
        policy._choose_menu_action(observation, sample, menu_started=False)

    policy.menu_action_queue.clear()
    policy.choice_settle_steps = 2
    action = policy._choose_menu_action(
        observation,
        sample,
        menu_started=False,
    )

    assert action.name == "wait"
    assert "choice patterns exhausted" in policy.reason
    assert sum(policy.active_choice_record["attempts"]) == len(CHOICE_PATTERNS)


def test_visible_writer_choice_uses_menu_policy_despite_dialogue_telemetry():
    observation = Observation(_visible_choice_frame(), step=0)
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    dialogue = Perception(GameState.DIALOGUE, 0.99, features, "telemetry")
    sample = TelemetrySample(
        "dialogue", 1, "room_test", 29, 170, "obj_writer", 0,
        player_x=80, player_y=80,
    )
    policy = StarterPolicy()
    key = ("room_test", 10, 10)
    policy.active_interaction_key = key
    policy.interactables[key] = {
        "choice_menus": 0,
        "classification": "unknown",
    }

    action = policy.choose(observation, dialogue, sample)

    assert action.name == "up"
    assert "choice trial" in policy.reason
    assert policy.interactables[key]["choice_menus"] == 1
    assert policy.interactables[key]["classification"] == "confirmed_npc"
    assert any(
        update.get("type") == "interaction_outcome"
        and update.get("classification") == "confirmed_npc"
        for update in policy.map_updates
    )


def test_reentered_known_npc_refreshes_live_map_to_choice_pending():
    observation = Observation(_visible_choice_frame(), step=0)
    sample = TelemetrySample(
        "dialogue",
        1,
        "room_test",
        29,
        170,
        "obj_writer",
        0,
        player_x=80,
        player_y=80,
    )
    key = ("room_test", 10, 10)
    policy = StarterPolicy()
    policy.active_interaction_key = key
    policy.interactables[key] = {
        "choice_menus": 1,
        "classification": "confirmed_npc",
        "usefulness": "flavor",
    }

    policy._start_choice_trial(observation, sample)

    assert policy.interactables[key]["usefulness"] == "choice_pending"
    assert any(
        update.get("type") == "interaction_outcome"
        and update.get("usefulness") == "choice_pending"
        for update in policy.map_updates
    )


def test_stale_writer_frame_cannot_create_or_advance_a_choice():
    valid = Observation(_visible_choice_frame(), step=0)
    stale = Observation(_visible_choice_frame(), step=1, visual_valid=False)
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    dialogue = Perception(GameState.DIALOGUE, 0.99, features, "telemetry")
    sample = TelemetrySample(
        "dialogue",
        1,
        "room_test",
        29,
        170,
        "obj_writer",
        0,
        player_x=80,
        player_y=80,
    )
    policy = StarterPolicy()

    first = policy.choose(stale, dialogue, sample)

    assert first.name == "confirm"
    assert policy.active_choice_record is None

    policy.choose(valid, dialogue, sample)
    queued_actions = list(policy.menu_action_queue)
    second = policy.choose(stale, dialogue, sample)

    assert second.name == "wait"
    assert "choice capture stale" in policy.reason
    assert list(policy.menu_action_queue) == queued_actions


def test_failed_choice_keeps_npc_retryable_and_selects_next_pattern():
    observation = Observation(_visible_choice_frame(), step=0)
    sample = TelemetrySample(
        "dialogue", 1, "room_test", 29, 170, "obj_writer", 0,
        player_x=80, player_y=80,
    )
    policy = StarterPolicy()
    key = ("room_test", 10, 10)
    policy.interactables[key] = {
        "choice_menus": 0,
        "classification": "unknown",
        "progressions": 0,
        "dialogue_steps": 0,
        "cutscene_steps": 0,
    }
    policy.active_interaction_key = key
    policy._start_choice_trial(observation, sample)
    first_record = policy.pending_choice_record
    assert first_record is not None
    assert policy.pending_choice_pattern == 0

    policy._finish_active_interaction(
        TelemetrySample(
            "overworld", 1, "room_test", 80, 80, "obj_mainchara", 1
        )
    )

    assert first_record["failures"][0] == 1
    assert policy._story_interaction_retryable(key)
    assert policy.story_stall_steps >= STORY_SEARCH_STEPS
    policy.active_interaction_key = key
    policy._start_choice_trial(observation, sample)
    assert policy.pending_choice_record is first_record
    assert policy.pending_choice_pattern == 1
    assert "down" in policy.menu_action_queue


def test_choice_reengagement_allows_every_pattern_then_stops():
    policy = StarterPolicy()
    key = ("room_test", 10, 10)
    policy.interactables[key] = {
        "choice_menus": 1,
        "classification": "confirmed_npc",
        "progressions": 0,
    }
    policy.choice_trials.append(
        {
            "room": "room_test",
            "context_x": 10,
            "context_y": 10,
            "signature": "0123",
            "attempts": [1, 1, 1],
            "failures": [1, 1, 1],
            "successes": [0, 0, 0],
            "successful_pattern": None,
        }
    )

    assert policy._story_interaction_retryable(key)

    policy.choice_trials[0]["attempts"] = [1] * len(CHOICE_PATTERNS)
    policy.choice_trials[0]["failures"] = [1] * len(CHOICE_PATTERNS)

    assert not policy._story_interaction_retryable(key)


def test_choice_learning_reaches_the_third_vertical_option_pattern():
    observation = Observation(Image.new("RGB", (160, 120), (20, 20, 20)), step=0)
    sample = TelemetrySample(
        "choice",
        1,
        "room_test",
        10,
        10,
        "obj_choicer_old",
        0,
        player_x=40,
        player_y=40,
    )
    policy = StarterPolicy()

    for expected_pattern in range(4):
        policy._start_choice_trial(observation, sample)
        assert policy.pending_choice_pattern == expected_pattern
        if expected_pattern == 3:
            assert list(policy.menu_action_queue).count("down") == 2
            break
        policy._mark_pending_choice_failed("no progress")


def test_standalone_menu_choice_cannot_claim_later_story_progress():
    observation = Observation(Image.new("RGB", (160, 120), (20, 20, 20)), step=0)
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    menu = Perception(GameState.MENU, 0.99, features, "telemetry")
    overworld = Perception(GameState.OVERWORLD, 0.99, features, "telemetry")
    sample = TelemetrySample(
        "choice",
        1,
        "room_test",
        40,
        40,
        "obj_choicer_old",
        0,
        player_x=40,
        player_y=40,
    )
    policy = StarterPolicy()

    policy.choose(observation, menu, sample)
    record = policy.pending_choice_record
    assert record is not None

    policy.choose(
        observation,
        overworld,
        replace(sample, mode="overworld", object_name="obj_mainchara"),
    )

    assert policy.pending_choice_record is None
    assert record["failures"][0] == 1
    assert policy.story_stall_steps < STORY_SEARCH_STEPS
    policy._record_story_progress("unrelated later room change", sample)
    assert record["successes"] == [0] * len(CHOICE_PATTERNS)
    assert record["successful_pattern"] is None


def test_legacy_long_dialogue_gets_one_visible_choice_migration_retry():
    policy = StarterPolicy()
    room = "room_test"
    key = (room, 4, 6)
    source = (4, 7)
    policy.interactables[key] = {
        "choice_menus": 0,
        "classification": "tested_nonchoice",
        "progressions": 0,
        "dialogue_steps": 41,
        "attempts": 1,
        "approaches": [{"x": 4, "y": 7, "direction": "up"}],
    }
    policy.interacted_targets.add(key)
    policy.story_stall_steps = STORY_SEARCH_STEPS

    assert policy._story_interaction_retryable(key)
    direction, commitment, reason = policy._plan_exploration(room, source)
    assert direction == "up"
    assert commitment == 1
    assert "retry another response" in reason
    assert policy.visual_goal == (room, 1, 1)

    policy.interactables[key]["attempts"] = 2
    assert not policy._story_interaction_retryable(key)


def test_choice_learning_persists_without_hidden_option_data():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "navigation.json"
        policy = StarterPolicy(memory_path=path)
        policy.choice_trials.append(
            {
                "room": "room_test",
                "context_x": 5,
                "context_y": 5,
                "signature": "0123",
                "attempts": [1, 2],
                "failures": [1, 0],
                "successes": [0, 1],
                "successful_pattern": 1,
            }
        )
        policy.save_memory()
        reloaded = StarterPolicy(memory_path=path)

    assert len(reloaded.choice_trials) == 1
    assert reloaded.choice_trials[0]["successful_pattern"] == 1
    assert reloaded.choice_trials[0]["successes"][:2] == [0, 1]


def test_nonchoice_interaction_is_not_retried_as_an_npc():
    policy = StarterPolicy()
    key = ("room_test", 6, 5)
    policy.interactables[key] = {
        "attempts": 1,
        "progressions": 0,
        "choice_menus": 0,
        "classification": "tested_nonchoice",
    }
    policy.interacted_targets.add(key)
    policy.story_stall_steps = STORY_SEARCH_STEPS

    assert policy._interacted_near("room_test", (5, 5), "right")

    policy.interactables[key]["choice_menus"] = 1
    policy.interactables[key]["classification"] = "confirmed_npc"

    assert policy._interacted_near("room_test", (5, 5), "right")


def test_static_compact_obstacle_with_multiple_approaches_builds_character_candidate():
    policy = StarterPolicy()
    policy.room_view = None
    policy.visits[("room_test", 12, 8)] = 1
    policy.seen_cells.add(("room_test", 12, 8))
    policy.blocked[("room_test", 12, 9, "right")] = 1
    policy.blocked[("room_test", 13, 10, "up")] = 1
    sample = TelemetrySample(
        "overworld",
        1,
        "room_test",
        16,
        144,
        "obj_mainchara",
        0,
        version=7,
        room_width=224,
        room_height=160,
        camera_x=0,
        camera_y=0,
        camera_width=224,
        camera_height=160,
    )

    frame = Image.new("RGB", (224, 160), (90, 90, 90))
    pixels = frame.load()
    for y in range(68, 92):
        for x in range(104, 120):
            pixels[x, y] = (40, 60, 140)
    policy._observe_screen(Observation(frame, 0), sample)

    record = policy.screen_regions[("room_test", 3, 2)]
    assert record["entity_approach_directions"] == 2
    assert record["hypothesis"] == "possible_character"


def test_retired_character_scenery_is_not_recreated_by_same_visual_evidence():
    policy = StarterPolicy()
    policy.room_view = None
    policy.visits[("room_test", 12, 8)] = 1
    policy.seen_cells.add(("room_test", 12, 8))
    policy.blocked[("room_test", 12, 9, "right")] = 1
    policy.blocked[("room_test", 13, 10, "up")] = 1
    sample = TelemetrySample(
        "overworld", 1, "room_test", 16, 144, "obj_mainchara", 0,
        version=7, room_width=224, room_height=160,
        camera_x=0, camera_y=0, camera_width=224, camera_height=160,
    )
    frame = Image.new("RGB", (224, 160), (90, 90, 90))
    pixels = frame.load()
    for y in range(68, 92):
        for x in range(104, 120):
            pixels[x, y] = (40, 60, 140)

    policy._observe_screen(Observation(frame, 0), sample)
    record = policy.screen_regions[("room_test", 3, 2)]
    assert record["hypothesis"] == "possible_character"
    record["hypothesis"] = None
    record["inspections"] = 3
    record["retired_reason"] = "exit evidence outranked weak scenery lead"

    policy._observe_screen(Observation(frame, 5), sample)

    assert record.get("hypothesis") != "possible_character"


def test_compact_static_obstacle_stays_unresolved_from_one_approach():
    policy = StarterPolicy()
    policy.room_view = None
    policy.visits[("room_test", 12, 8)] = 1
    policy.seen_cells.add(("room_test", 12, 8))
    policy.blocked[("room_test", 12, 9, "right")] = 1
    sample = TelemetrySample(
        "overworld", 1, "room_test", 16, 144, "obj_mainchara", 0,
        version=7, room_width=224, room_height=160,
        camera_x=0, camera_y=0, camera_width=224, camera_height=160,
    )
    frame = Image.new("RGB", (224, 160), (90, 90, 90))
    pixels = frame.load()
    for y in range(64, 96):
        for x in range(96, 128):
            pixels[x, y] = (230, 180, 30) if (x + y) % 4 < 2 else (25, 35, 100)

    for step in (0, 5, 10):
        policy._observe_screen(Observation(frame, step), sample)

    record = policy.screen_regions[("room_test", 3, 2)]
    assert record["entity_approach_directions"] == 1
    assert record["obstruction_target_cells"] == 1
    assert record["hypothesis"] is None
    assert record["guess_semantic_state"] == "unknown_but_interesting"
    assert entity_candidate_state(record) in {
        "single_side_unresolved",
        "single_side_stable",
    }


def test_character_goal_routes_to_exact_known_interaction_side():
    policy = StarterPolicy()
    room = "room_test"
    target_region = (2, 1)
    policy._remember_open_path(room, (9, 10), "up", (9, 5))
    policy.seen_cells.add((room, 9, 5))
    policy.blocked[(room, 9, 5, "left")] = 2
    policy.screen_regions[(room, *target_region)] = {
        "views": 4,
        "interest": 0.5,
        "hypothesis": "possible_character",
        "guess_semantic_state": "possible_character",
        "guess_confidence": 0.74,
        "inspections": 0,
        "character_probe_version": 1,
        "entity_approach_directions": 2,
        "obstruction_target_cells": 2,
        "last_seen_sequence": 4,
    }

    route = policy._direction_to_visual_hypothesis(
        room,
        (9, 10),
        story_focus=True,
        allowed_hypotheses={"possible_character"},
    )
    probe = policy._direction_to_visual_hypothesis(
        room,
        (9, 5),
        story_focus=True,
        allowed_hypotheses={"possible_character"},
    )

    assert route is not None and route[0] == "up"
    assert probe is not None and probe[0] == "left"


def test_animated_scenery_without_walkable_topology_is_not_a_character():
    policy = StarterPolicy()
    policy.room_view = None
    sample = TelemetrySample(
        "overworld",
        1,
        "room_test",
        112,
        80,
        "obj_mainchara",
        0,
        version=7,
        room_width=224,
        room_height=160,
        camera_x=0,
        camera_y=0,
        camera_width=224,
        camera_height=160,
    )

    for index in range(8):
        frame = Image.new("RGB", (224, 160), (90, 90, 90))
        pixels = frame.load()
        for y in range(64, 96):
            for x in range(96, 128):
                pixels[x, y] = (220, 80, 70) if (x + index) % 8 < 4 else (30, 40, 100)
        policy._observe_screen(Observation(frame, index * 5), sample)

    record = policy.screen_regions[("room_test", 3, 2)]
    assert record.get("entity_approach_directions", 0) == 0
    assert record.get("hypothesis") != "possible_character"


def test_long_wall_seen_from_one_direction_is_not_a_character():
    policy = StarterPolicy()
    policy.room_view = None
    policy.visits[("room_test", 12, 8)] = 1
    policy.seen_cells.add(("room_test", 12, 8))
    for source_x in range(12, 16):
        policy.blocked[("room_test", source_x, 9, "up")] = 1
    sample = TelemetrySample(
        "overworld", 1, "room_test", 16, 144, "obj_mainchara", 0,
        version=7, room_width=224, room_height=160,
        camera_x=0, camera_y=0, camera_width=224, camera_height=160,
    )
    frame = Image.new("RGB", (224, 160), (90, 90, 90))
    pixels = frame.load()
    for y in range(64, 96):
        for x in range(96, 128):
            pixels[x, y] = (180, 80, 40)

    for step in (0, 5, 10):
        policy._observe_screen(Observation(frame, step), sample)

    record = policy.screen_regions[("room_test", 3, 2)]
    assert record["entity_approach_directions"] <= 1
    assert record.get("hypothesis") != "possible_character"


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
    target = policy._interaction_target(policy.last_cell, "down")
    goal = ("room_test", *policy._region(target))
    policy.visual_goal = goal
    policy.screen_regions[goal] = {"hypothesis": "possible_character"}

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
    bottom = (7, 19)
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


def test_one_directional_probe_covers_a_region_not_every_tile():
    policy = StarterPolicy()
    room = "room_test"
    policy.tried.add((room, 1, 1, "right"))

    assert not policy._direction_is_unexplored(room, (3, 3), "right")
    assert policy._direction_is_unexplored(room, (4, 3), "right")


def test_observed_path_marks_reverse_direction_known_for_whole_region():
    policy = StarterPolicy()
    policy._remember_open_path("room_test", (1, 1), "right", (2, 1))

    assert policy._region_direction_tried("room_test", (3, 3), "left")


def test_forward_warp_is_preferred_over_the_entry_room_warp():
    policy = StarterPolicy()
    room = "room_b"
    start = (2, 3)
    endpoint = (5, 3)
    policy._remember_open_path(room, start, "right", endpoint)
    warp = (room, *endpoint, "right", "room_a", 1, 3)
    policy.warps[warp] = 1
    forward = (room, *start, "up", "room_c", 2, 8)
    policy.warps[forward] = 1
    policy.room_entry_from[room] = "room_a"

    assert policy._route_to_learned_warp(room, start) == ("up", forward)


def test_only_known_entry_warp_can_leave_an_exhausted_dead_end():
    policy = StarterPolicy()
    room = "room_dead_end"
    start = (2, 3)
    endpoint = (5, 3)
    policy._remember_open_path(room, start, "right", endpoint)
    warp = (room, *endpoint, "right", "room_previous", 1, 3)
    policy.warps[warp] = 1
    policy.room_entry_from[room] = "room_previous"

    assert policy._route_to_learned_warp(room, start) == ("right", warp)


def test_coarse_visited_room_map_bridges_sample_gaps_to_progress_warp():
    policy = StarterPolicy()
    room = "room_house"
    start = (18, 11)
    progress_endpoint = (72, 17)
    for region in (
        (4, 2),
        (4, 3),
        (4, 4),
        (5, 5),
        (6, 4),
        (7, 4),
        (8, 4),
        (9, 4),
        (10, 4),
        (11, 4),
        (12, 4),
        (13, 4),
        (14, 4),
        (15, 3),
        (16, 3),
        (17, 4),
        (18, 4),
    ):
        policy.seen_regions.add((room, *region))
    bathroom = (room, 12, 20, "left", "room_bathroom", 26, 15)
    progress = (room, *progress_endpoint, "down", "room_yard", 19, 22)
    policy.warps[bathroom] = 1
    policy.warps[progress] = 1
    for x in range(10):
        policy.seen_regions.add(("room_bathroom", x, 0))
    policy.seen_regions.add(("room_yard", 0, 0))

    direction, selected = policy._route_to_learned_warp(room, start)

    assert selected == progress
    assert direction == "down"


def test_returning_across_same_room_pair_suppresses_that_link():
    policy = StarterPolicy()
    policy._observe_room(
        TelemetrySample("overworld", 1, "room_a", 80, 80, "obj_mainchara", 0)
    )
    policy.last_overworld_movement = "right"
    policy._observe_room(
        TelemetrySample("overworld", 2, "room_b", 16, 80, "obj_mainchara", 1)
    )
    policy.last_overworld_movement = "left"
    policy._observe_room(
        TelemetrySample("overworld", 1, "room_a", 80, 80, "obj_mainchara", 2)
    )

    assert frozenset(("room_a", "room_b")) in policy.suppressed_room_links


def test_room_transition_keeps_last_overworld_direction_through_unknown_frame():
    policy = StarterPolicy()
    source = TelemetrySample(
        "overworld", 1, "room_a", 80, 80, "obj_mainchara", 0
    )
    target = TelemetrySample(
        "overworld", 2, "room_b", 16, 80, "obj_mainchara", 2
    )
    policy._observe_room(source)
    policy._select("down", "moving toward exit", source)
    policy._select("wait", "transition frame", None)

    policy._observe_room(target)

    assert ("room_a", 10, 10, "down", "room_b", 2, 10) in policy.warps


def test_v9_transition_uses_exact_source_foot_and_discovery_is_not_progression():
    policy = StarterPolicy()
    source = TelemetrySample(
        "overworld", 1, "room_a", 80, 72, "obj_mainchara", 0,
        version=9, player_foot_x=88, player_foot_y=96,
    )
    target = TelemetrySample(
        "overworld", 2, "room_b", 16, 24, "obj_mainchara", 1,
        version=9,
        player_foot_x=24,
        player_foot_y=40,
        transition_from_room_name="room_a",
        transition_from_x=80,
        transition_from_y=72,
        transition_from_foot_x=104,
        transition_from_foot_y=112,
        transition_from_facing="down",
    )
    policy._observe_room(source)
    policy.last_overworld_movement = "down"
    policy._observe_room(target)

    warp = ("room_a", 13, 14, "down", "room_b", 3, 5)
    assert policy.warps[warp] == 1
    portal = policy.world.portal_metadata(warp)
    assert portal is not None
    assert portal["role"] == "new_area"
    assert portal["non_discovery_progress_outcomes"] == 0


def test_observed_story_outcome_can_promote_recent_portal_to_progression():
    policy = StarterPolicy()
    source = TelemetrySample("overworld", 1, "room_a", 80, 80, "obj_mainchara", 0)
    target = TelemetrySample("overworld", 2, "room_b", 16, 80, "obj_mainchara", 1)
    policy._observe_room(source)
    policy.last_overworld_movement = "right"
    policy._observe_room(target)

    policy.navigation_tick += 10
    policy._record_story_progress("interaction caused a scripted sequence", target)

    assert policy.last_portal_id is not None
    portal = policy.world.portal_metadata(policy.last_portal_id)
    assert portal is not None
    assert portal["role"] == "progression"
    assert portal["non_discovery_progress_outcomes"] == 1


def test_exit_search_keeps_a_learned_outline_goal_until_it_is_probed():
    policy = StarterPolicy()
    room = "room_test"
    policy._remember_open_path(room, (2, 5), "right", (5, 5))
    policy.seen_cells.update((room, x, 5) for x in range(2, 6))
    policy.steps_without_frontier = WARP_SEEK_STEPS
    policy.exit_search_goal = (room, 2, 5, "left")

    direction, commitment, reason = policy._plan_exploration(room, (4, 5))

    assert direction == "left"
    assert commitment == 1
    assert "search room edge" in reason
    assert policy.exit_search_goal == (room, 2, 5, "left")

    # Even after the other endpoint becomes closer, the active goal remains
    # stable instead of producing another left/right choice reversal.
    direction, commitment, reason = policy._plan_exploration(room, (5, 5))
    assert direction == "left"
    assert policy.exit_search_goal == (room, 2, 5, "left")

    # Movement pulses can skip the exact target cell. One adjacent cell is close
    # enough for the committed input to reach and test the same learned edge.
    direction, commitment, reason = policy._plan_exploration(room, (3, 5))
    assert direction == "left"
    assert commitment == EXIT_PROBE_COMMIT_STEPS
    assert "probe possible room exit" in reason
    assert policy.exit_probes[(room, 2, 5, "left")] == 1


def test_retired_character_reason_survives_restart():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "navigation.json"
        policy = StarterPolicy(memory_path=path)
        key = ("room_test", 3, 2)
        policy.screen_regions[key] = {
            "views": 4,
            "interest": 0.5,
            "hypothesis": None,
            "inspections": 3,
            "retired_reason": "exit evidence outranked weak scenery lead",
        }
        policy.save_memory()
        reloaded = StarterPolicy(memory_path=path)

    assert reloaded.screen_regions[key]["retired_reason"] == (
        "exit evidence outranked weak scenery lead"
    )


def test_story_search_prefers_plain_path_continuation_over_visual_door_guess():
    policy = StarterPolicy()
    room = "room_test"
    policy._remember_open_path(room, (2, 5), "right", (5, 5))
    policy.seen_cells.update((room, x, 5) for x in range(2, 6))
    policy.steps_without_frontier = WARP_SEEK_STEPS
    policy.story_stall_steps = STORY_SEARCH_STEPS
    policy.current_visible_regions.add((room, 8, 1))
    policy.screen_regions[(room, 8, 1)] = {
        "views": 3,
        "interest": 0.9,
        "hypothesis": "possible_exit",
        "inspections": 0,
    }
    policy.screen_regions[(room, 0, 1)] = {
        "views": 3,
        "interest": 0.02,
        "hypothesis": None,
        "inspections": 0,
    }

    direction, _commitment, reason = policy._plan_exploration(room, (3, 5))

    assert direction == "left"
    assert "possible room exit" in reason
    assert "visual passage" not in reason
    assert policy.screen_regions[(room, 0, 1)]["path_continuation"] is True
    assert policy.screen_regions[(room, 0, 1)]["hypothesis"] == "possible_exit"


def test_exit_probe_memory_survives_restart():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "navigation.json"
        policy = StarterPolicy(memory_path=path)
        policy.exit_probes[("room_test", 2, 5, "left")] = 2
        policy.save_memory()

        reloaded = StarterPolicy(memory_path=path)

    assert reloaded.exit_probes[("room_test", 2, 5, "left")] == 2


def test_failed_character_direction_survives_restart():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "navigation.json"
        policy = StarterPolicy(memory_path=path)
        policy.character_probes[("room_test", 10, 10, "down")] = 1
        policy.save_memory()

        reloaded = StarterPolicy(memory_path=path)

    assert reloaded.character_probes[("room_test", 10, 10, "down")] == 1


def test_legacy_character_without_target_geometry_is_downgraded():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "navigation.json"
        policy = StarterPolicy(memory_path=path)
        policy.screen_regions[("room_test", 3, 2)] = {
            "views": 4,
            "interest": 0.71,
            "hypothesis": "possible_character",
            "inspections": 1,
            "last_interest": 0.69,
            "remote_observations": 8,
            "remote_motion_samples": 4,
            "character_confidence": 0.5,
            "last_signature": "0123",
            "walkable_evidence": True,
            "entity_approach_directions": 2,
        }
        policy.visits[("room_test", 12, 8)] = 1
        policy.seen_cells.add(("room_test", 12, 8))
        policy.blocked[("room_test", 12, 9, "right")] = 1
        policy.blocked[("room_test", 13, 10, "up")] = 1
        policy.save_memory()

        reloaded = StarterPolicy(memory_path=path)

    record = reloaded.screen_regions[("room_test", 3, 2)]
    expected_legacy_fields = {
        "views": 4,
        "interest": 0.71,
        "hypothesis": None,
        "inspections": 0,
        "motion": 0.0,
        "last_interest": 0.69,
        "last_signature": "0123",
        "walkable_evidence": True,
        "entity_approach_directions": 2,
        "obstruction_target_cells": 0,
        "character_probe_version": 1,
        "path_continuation": False,
        "guess_misses": 0,
    }
    assert {
        key: record[key] for key in expected_legacy_fields
    } == expected_legacy_fields
    assert record["guess_semantic_state"] == "unknown_but_interesting"
    assert len(record["anchor_cell"]) == 2


def test_backtrack_avoidance_covers_approach_to_jittery_warp_endpoint():
    policy = StarterPolicy()
    room = "room_b"
    policy.room_entry_from[room] = "room_a"
    policy.warps[(room, 5, 5, "down", "room_a", 10, 10)] = 1

    assert policy._is_entry_warp_direction(room, (5, 3), "down")
    assert not policy._is_entry_warp_direction(room, (5, 3), "up")

    policy._remember_open_path(room, (5, 3), "down", (5, 4))
    assert not any(
        direction == "down"
        for direction, _target in policy._adjacency(room).get((5, 3), [])
    )


def test_low_area_vertical_loop_gets_horizontal_cooldown():
    policy = StarterPolicy()
    room = "room_test"
    positions = [(51, y) for y in (12, 13, 15, 14, 12, 13, 15, 14, 12, 13)]
    policy.recent_cells.extend((room, x, y) for x, y in positions)
    policy.decision_history.extend(
        (room, 51, 12 if index % 2 == 0 else 15, "up" if index % 2 == 0 else "down")
        for index in range(8)
    )
    policy.navigation_tick = 50

    direction, broke_loop = policy._break_oscillation(room, (51, 13), "up")

    assert broke_loop
    assert direction in {"left", "right"}
    assert policy._loop_avoid_directions(room, (51, 13)) == {"up", "down"}
    assert policy._least_visited_direction(room, (51, 13), "up") in {
        "left",
        "right",
    }


def test_camera_regions_create_unresolved_visual_exit_evidence():
    frame = Image.new("RGB", (128, 64), (90, 90, 90))
    pixels = frame.load()
    for y in range(8, 24):
        for x in range(108, 128):
            pixels[x, y] = (8, 12, 18)
    telemetry = TelemetrySample(
        "overworld",
        1,
        "room_test",
        16,
        48,
        "obj_mainchara",
        0,
        version=7,
        room_width=128,
        room_height=64,
        camera_x=0,
        camera_y=0,
        camera_width=128,
        camera_height=64,
    )
    policy = StarterPolicy()

    policy._observe_screen(Observation(frame, step=0), telemetry)
    visual_plan = policy._direction_to_visual_hypothesis("room_test", (2, 6))

    assert len(policy.current_visible_regions) == 8
    assert len(policy.screen_regions) == 8
    candidates = [
        record
        for record in policy.screen_regions.values()
        if record.get("exit_candidate_source") is not None
    ]
    assert candidates
    assert all(record.get("hypothesis") != "possible_exit" for record in candidates)
    assert visual_plan is None
    record = max(candidates, key=lambda item: float(item.get("interest", 0.0)))
    assert "unresolved" in record["guess_label"].lower()
    assert record["evidence_kind"] == "multi_hypothesis_observation"
    assert record["guess_semantic_state"] == "unknown_but_interesting"
    assert len(record["anchor_cell"]) == 2
    assert len(record["feature_box_world"]) == 4


def test_visual_route_uses_precise_anchor_inside_a_coarse_region():
    policy = StarterPolicy()
    room = "room_test"
    policy._remember_open_path(room, (8, 4), "right", (9, 4))
    policy._remember_open_path(room, (9, 4), "right", (10, 4))

    assert policy._route_toward_visible_region(room, (8, 4), (2, 1)) is None
    assert (
        policy._route_toward_visible_region(
            room,
            (8, 4),
            (2, 1),
            anchor_cell=(11, 4),
        )
        == "right"
    )


def test_exit_probe_approach_must_align_with_its_outward_direction():
    policy = StarterPolicy()
    probe = ("room_test", 10, 10, "right")

    assert policy._within_exit_probe_approach((10, 10), probe)
    assert policy._within_exit_probe_approach((9, 10), probe)
    assert not policy._within_exit_probe_approach((10, 9), probe)
    assert not policy._within_exit_probe_approach((9, 9), probe)
    assert not policy._within_exit_probe_approach((11, 10), probe)

    adjacency = {
        (9, 9): [("right", (10, 9)), ("down", (9, 10))],
        (10, 9): [("left", (9, 9))],
        (9, 10): [("up", (9, 9)), ("right", (10, 10))],
        (10, 10): [("left", (9, 10))],
    }
    assert policy._route_to_exit_approach(adjacency, (9, 9), probe) == (
        "down",
        1,
    )


def test_finishing_visual_guess_refreshes_its_evidence_score_and_gui_update():
    policy = StarterPolicy()
    key = ("room_test", 3, 0)
    record = {
        "views": 4,
        "interest": 0.8,
        "hypothesis": "possible_exit",
        "inspections": 0,
        "edge_hint": "right",
        "visual_summary": "tall bright feature",
    }
    policy.screen_regions[key] = record
    policy._refresh_visual_guess_metadata((3, 0), record)
    previous_score = record["guess_confidence"]
    policy.visual_goal = key

    policy._finish_visual_goal()
    update = policy.drain_map_updates()[-1]

    assert record["inspections"] == 1
    assert record["guess_confidence"] < previous_score
    assert update["guess_confidence"] == record["guess_confidence"]
    assert update["guess_label"] == "Possible right opening (0% edge span)"
    assert update["evidence_summary"] == (
        "tall bright feature; opening-shape score 0%"
    )


def test_route_failures_do_not_pretend_the_guess_was_tested_and_eventually_reject_it():
    policy = StarterPolicy()
    key = ("room_test", 3, 0)
    record = {
        "views": 3,
        "last_seen_sequence": 3,
        "interest": 0.8,
        "hypothesis": "possible_exit",
        "edge_opening_score": 0.8,
        "edge_width_ratio": 0.3,
        "guess_state": "approaching",
    }
    policy.screen_regions[key] = record
    policy._refresh_visual_guess_metadata((3, 0), record)

    for attempt in range(2):
        policy.visual_goal = key
        policy._finish_visual_goal("route_failed", f"failed route {attempt + 1}")

    assert record["completed_tests"] == 0
    assert record["failed_approaches"] == 2
    assert record["guess_state"] == "rejected"


def test_loop_recovery_never_reports_a_stale_visual_lead():
    policy = StarterPolicy()
    key = ("room_test", 3, 0)
    policy.screen_regions[key] = {
        "hypothesis": "possible_exit",
        "guess_id": "room_test@3,0",
        "guess_label": "Possible right opening",
    }
    policy.decision_visual_goal = key
    policy.reason = "detected up/down loop in a small area; commit left away from it"

    assert policy.decision_context() is None


def test_prediction_snapshot_identifies_the_exact_visual_feature_and_lifecycle():
    policy = StarterPolicy()
    key = ("room_test", 3, 0)
    policy.observed_room = "room_test"
    policy.observed_cell = (2, 6)
    policy.screen_regions[key] = {
        "hypothesis": "possible_exit",
        "guess_id": "room_test@3,0",
        "guess_label": "Possible right opening",
        "guess_confidence": 0.77,
        "guess_state": "approaching",
        "last_seen_sequence": 2,
        "anchor_cell": [12, 2],
        "anchor_world": [110.0, 16.0],
        "passage_box_world": [108.0, 8.0, 128.0, 24.0],
    }
    policy.decision_visual_goal = key

    snapshot = policy.prediction_snapshot()

    assert snapshot["selected_guess_id"] == "room_test@3,0"
    candidate = snapshot["candidates"][0]
    assert candidate["id"] == "room_test@3,0"
    assert candidate["feature_box_world"] == [108.0, 8.0, 128.0, 24.0]
    assert candidate["selected"] is True


def test_startup_enrichment_is_sent_to_an_already_open_gui():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "navigation.json"
        writer = StarterPolicy(memory_path=path)
        key = ("room_test", 3, 0)
        writer.screen_regions[key] = {
            "views": 4,
            "interest": 0.8,
            "hypothesis": "possible_exit",
            "inspections": 0,
        }
        writer.save_memory()

        reloaded = StarterPolicy(memory_path=path)
        updates = reloaded.drain_map_updates()

    update = next(item for item in updates if item.get("region") == [3, 0])
    assert update["guess_label"] == "Possible localized boundary opening"
    assert update["evidence_kind"] == "visual_edge_landmark"
    # Legacy edge guesses without an opening profile remain visible for
    # inspection, but are deliberately too weak to drive navigation.
    assert 0 < update["guess_confidence"] < MIN_VISUAL_GUESS_CONFIDENCE
    assert len(update["anchor_cell"]) == 2


def test_non_overworld_action_does_not_report_lingering_visual_goal():
    observation = Observation(Image.new("RGB", (2, 2)), step=0)
    perception = Perception(
        GameState.DIALOGUE,
        0.99,
        VisualFeatures(0, 0, 0, 0, 0, 0),
        "telemetry",
    )
    policy = StarterPolicy()
    key = ("room_test", 3, 0)
    policy.screen_regions[key] = {
        "hypothesis": "possible_exit",
        "guess_label": "Possible room-edge passage",
    }
    policy.visual_goal = key
    policy.decision_visual_goal = key

    action = policy.choose(observation, perception)

    assert action.name == "confirm"
    assert policy.decision_context() is None


def test_camera_observation_emits_persistent_room_view_tile_updates():
    frame = Image.new("RGB", (128, 64), (70, 90, 110))
    telemetry = TelemetrySample(
        "overworld",
        1,
        "room_test",
        16,
        48,
        "obj_mainchara",
        0,
        version=7,
        room_width=128,
        room_height=64,
        camera_x=0,
        camera_y=0,
        camera_width=128,
        camera_height=64,
    )
    with TemporaryDirectory() as directory:
        memory_path = Path(directory) / "navigation.json"
        policy = StarterPolicy(memory_path=memory_path)

        policy._observe_screen(Observation(frame, step=0), telemetry)
        updates = policy.drain_map_updates()

        view_updates = [
            update for update in updates if update["type"] == "room_view_tile"
        ]
        assert len(view_updates) == 8
        assert all(Path(str(update["path"])).is_file() for update in view_updates)
        assert (memory_path.parent / "room_views" / "index.json").is_file()


def test_repeated_blank_views_do_not_promote_stale_exit_evidence():
    room = "room_test"
    telemetry = TelemetrySample(
        "overworld",
        1,
        room,
        16,
        48,
        "obj_mainchara",
        0,
        version=7,
        room_width=128,
        room_height=64,
        camera_x=0,
        camera_y=0,
        camera_width=128,
        camera_height=64,
    )
    policy = StarterPolicy()
    policy.seen_cells.add((room, 12, 2))
    landmark = Image.new("RGB", (128, 64), (90, 90, 90))
    pixels = landmark.load()
    for y in range(8, 24):
        for x in range(108, 128):
            pixels[x, y] = (8, 12, 18)
    policy._observe_screen(Observation(landmark, step=0), telemetry)
    candidates = [
        key for key, record in policy.screen_regions.items()
        if record.get("exit_candidate_source") is not None
    ]
    assert candidates
    guess = candidates[0]
    original_feature = {
        field: policy.screen_regions[guess][field]
        for field in ("focus_world", "feature_box_world", "visual_summary")
    }

    ordinary = Image.new("RGB", (128, 64), (90, 90, 90))
    for step in (10, 20):
        policy._observe_screen(Observation(ordinary, step=step), telemetry)
    assert policy.screen_regions[guess]["hypothesis"] is None
    assert {
        field: policy.screen_regions[guess][field] for field in original_feature
    } == original_feature

    policy._observe_screen(Observation(ordinary, step=30), telemetry)

    assert policy.screen_regions[guess]["hypothesis"] is None
    assert policy.screen_regions[guess]["exit_candidate_state"] not in {
        "semantic_ready",
        "confirmed",
    }


def test_hidden_nearby_interactable_telemetry_is_not_used_by_policy():
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
        facing_direction="right",
        nearest_interactable_name="obj_hidden",
        nearest_interactable_id=100999,
        nearest_interactable_x=160,
        nearest_interactable_y=160,
        nearest_interactable_distance=8,
    )
    policy = StarterPolicy()
    policy.last_movement = "right"
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

    assert action.name in DIRECTION_VECTORS
    assert action.name != "confirm"
    assert policy.interaction_candidate is None
    assert not policy.interactables
    assert "skip interaction" in policy.reason
