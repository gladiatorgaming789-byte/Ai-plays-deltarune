from __future__ import annotations

from PIL import Image

from deltarune_agent.observer import Observation
from deltarune_agent.perception import GameState, Perception, VisualFeatures
from deltarune_agent.run8_explorer import Run8Explorer
from deltarune_agent.telemetry import TelemetrySample


def sample(
    *,
    room: str = "room_test",
    object_name: str = "obj_mainchara",
    mode: str = "overworld",
    x: float = 80.0,
    y: float = 80.0,
) -> TelemetrySample:
    return TelemetrySample(
        mode=mode,
        room_id=1,
        room_name=room,
        x=x,
        y=y,
        object_name=object_name,
        received_at=1.0,
        player_controlled=mode == "overworld",
        player_foot_x=x,
        player_foot_y=y,
        room_width=320.0,
        room_height=240.0,
    )


def perception(state: GameState) -> Perception:
    return Perception(
        state=state,
        confidence=1.0,
        features=VisualFeatures(0.0, 0.0, 0.0, 0.0, 0.0),
        source="test",
    )


def test_inset_dark_opening_is_retired_before_navigation():
    explorer = Run8Explorer()
    room = "room_test"
    key = (room, 8, 5)
    explorer.room_dimensions[room] = (320.0, 240.0)
    explorer.screen_regions[key] = {
        "hypothesis": "possible_exit",
        "guess_state": "proposed",
        "passage_box_world": [286.0, 168.0, 288.0, 184.0],
        "edge_hint": "right",
        "dark_ratio": 0.45,
        "views": 3,
        "independent_views": 2,
        "interest": 0.4,
        "completed_tests": 0,
        "failed_approaches": 0,
    }

    explorer._retire_unsupported_visual_exits(room)

    assert explorer.screen_regions[key]["hypothesis"] is None
    assert explorer.screen_regions[key]["guess_state"] == "retired"
    assert "true telemetry room boundary" in explorer.screen_regions[key]["retired_reason"]
    assert explorer.retired_inset_exit_guesses == 1


def test_true_boundary_opening_with_known_warp_is_preserved():
    explorer = Run8Explorer()
    room = "room_test"
    key = (room, 9, 5)
    explorer.room_dimensions[room] = (320.0, 240.0)
    explorer.screen_regions[key] = {
        "hypothesis": "possible_exit",
        "guess_state": "proposed",
        "passage_box_world": [312.0, 168.0, 320.0, 184.0],
        "edge_hint": "right",
        "dark_ratio": 0.9,
        "views": 3,
        "independent_views": 2,
        "interest": 0.4,
        "completed_tests": 0,
        "failed_approaches": 0,
    }
    explorer.warps[(room, 39, 22, "right", "room_next", 1, 22)] = 1

    explorer._retire_unsupported_visual_exits(room)

    assert explorer.screen_regions[key]["hypothesis"] == "possible_exit"
    assert explorer.retired_dark_void_guesses == 0


def test_dark_void_without_walkable_evidence_is_retired():
    explorer = Run8Explorer()
    room = "room_test"
    key = (room, 4, 7)
    explorer.room_dimensions[room] = (320.0, 240.0)
    explorer.screen_regions[key] = {
        "hypothesis": "possible_exit",
        "guess_state": "proposed",
        "passage_box_world": [132.0, 232.0, 156.0, 240.0],
        "edge_hint": "bottom",
        "dark_ratio": 0.92,
        "views": 4,
        "independent_views": 2,
        "interest": 0.5,
        "completed_tests": 0,
        "failed_approaches": 0,
    }

    explorer._retire_unsupported_visual_exits(room)

    assert explorer.screen_regions[key]["hypothesis"] is None
    assert "dark void" in explorer.screen_regions[key]["retired_reason"]
    assert explorer.retired_dark_void_guesses == 1


def test_compact_animated_collision_figure_gets_character_bonus():
    explorer = Run8Explorer()
    room = "room_test"
    key = (room, 2, 2)
    explorer.screen_regions[key] = {
        "hypothesis": "possible_character",
        "guess_state": "proposed",
        "visual_summary": "compact detailed colorful feature toward the center",
        "appearance_changes": 8,
        "colorfulness": 0.22,
        "guess_confidence": 0.44,
        "last_seen_sequence": 11,
        "views": 6,
        "independent_views": 2,
        "interest": 0.4,
        "completed_tests": 0,
        "failed_approaches": 0,
        "entity_approach_directions": 2,
        "obstruction_target_cells": 3,
    }

    explorer._apply_animated_character_bonus(room)

    record = explorer.screen_regions[key]
    assert record["guess_confidence"] == 0.56
    assert record["animated_sprite_evidence"] is True
    assert explorer.animated_character_bonuses == 1

    # The same observation sequence must not compound the bonus every step.
    explorer._apply_animated_character_bonus(room)
    assert record["guess_confidence"] == 0.56
    assert explorer.animated_character_bonuses == 1


def test_save_menu_confirms_once_without_creating_choice_trial():
    explorer = Run8Explorer()
    telemetry = sample(
        room="room_dark1a",
        object_name="obj_savemenu",
        mode="choice",
        x=296.0,
        y=184.0,
    )
    observation = Observation(Image.new("RGB", (320, 240)), step=1)

    first = explorer.choose(observation, perception(GameState.MENU), telemetry)
    second = explorer.choose(
        Observation(Image.new("RGB", (320, 240)), step=2),
        perception(GameState.MENU),
        telemetry,
    )

    assert first.name == "confirm"
    assert second.name == "wait"
    assert explorer.save_menu_confirms == 1
    assert explorer.save_menu_waits == 1
    assert explorer.choice_trials == []
    assert "do not learn a story choice" in explorer.reason or "already confirmed" in explorer.reason


def test_save_point_interaction_is_not_story_retryable():
    explorer = Run8Explorer()
    key = ("room_dark1a", 37, 22)
    explorer.interactables[key] = {
        "classification": "save_point",
        "usefulness": "utility",
        "choice_menus": 0,
        "progressions": 0,
        "attempts": 1,
    }

    assert not explorer._story_interaction_retryable(key)


def test_near_duplicate_unresolved_choice_records_are_merged():
    explorer = Run8Explorer()
    first = {
        "room": "room_dark1a",
        "context_x": 37,
        "context_y": 22,
        "signature": "0" * 192,
        "attempts": [1, 0],
        "failures": [1, 0],
        "successes": [0, 0],
        "successful_pattern": None,
    }
    second = {
        "room": "room_dark1a",
        "context_x": 37,
        "context_y": 22,
        "signature": "1" * 39 + "0" * 153,
        "attempts": [0, 1],
        "failures": [0, 1],
        "successes": [0, 0],
        "successful_pattern": None,
    }
    explorer.choice_trials[:] = [first, second]

    explorer._merge_near_duplicate_choice_records()

    assert len(explorer.choice_trials) == 1
    assert explorer.choice_trials[0]["attempts"] == [1, 1]
    assert explorer.choice_trials[0]["failures"] == [1, 1]
    assert explorer.merged_choice_records == 1


def test_confirmed_bidirectional_doorway_is_not_permanently_suppressed():
    explorer = Run8Explorer()
    link = frozenset(("room_a", "room_b"))
    explorer.suppressed_room_links.add(link)
    explorer.world.suppressed_room_links.add(link)
    explorer.warps[("room_a", 5, 5, "right", "room_b", 1, 5)] = 1
    explorer.warps[("room_b", 1, 5, "left", "room_a", 5, 5)] = 1

    explorer._clear_bidirectional_suppressions()

    assert link not in explorer.suppressed_room_links
    assert link not in explorer.world.suppressed_room_links
    assert explorer.cleared_bidirectional_suppressions == 1
