from __future__ import annotations

from pathlib import Path

from deltarune_agent.run16_semantics import install_run16_semantics

install_run16_semantics()

from deltarune_agent.run17_reinforcement import Run17ReinforcementExplorer
from deltarune_agent.world_model import WorldModel


def test_promotion_only_region_is_neutralized(tmp_path: Path):
    path = tmp_path / "navigation.json"
    world = WorldModel(path)
    world.screen_regions[("room_test", 2, 4)] = {
        "views": 8,
        "independent_views": 2,
        "interest": 0.35,
        "hypothesis": "possible_character",
        "inspections": 0,
        "completed_tests": 0,
        "approach_attempts": 0,
        "failed_approaches": 0,
        "guess_state": "proposed",
        "guess_model_version": 2,
        "evidence_kind": "repeated_compact_sprite_motion",
        "source_evidence_kind": "repeated_compact_sprite_motion",
        "motion_sprite_candidate": True,
    }
    world.save()

    explorer = Run17ReinforcementExplorer(memory_path=path)
    record = explorer.screen_regions[("room_test", 2, 4)]

    assert record["hypothesis"] is None
    assert "motion_sprite_candidate" not in record
    assert "motion_sprite_tested" not in record
    assert "source_evidence_kind" not in record
    assert explorer.removed_promoted_regions == 1


def test_observe_promotion_hook_never_creates_hypothesis():
    explorer = Run17ReinforcementExplorer()
    key = ("room_test", 2, 4)
    explorer.screen_regions[key] = {
        "views": 20,
        "interest": 0.9,
        "hypothesis": None,
        "motion": 20.0,
        "colorfulness": 1.0,
        "dark_ratio": 0.0,
        "feature_box_world": [1.0, 1.0, 4.0, 4.0],
    }
    explorer.interactables[("room_test", 1, 1)] = {
        "classification": "confirmed_npc"
    }
    explorer.interactables[("room_test", 2, 1)] = {
        "classification": "tested_nonchoice"
    }

    explorer._promote_motion_sprite_candidates("room_test")

    assert explorer.screen_regions[key]["hypothesis"] is None


def test_retry_route_does_not_create_region_hypothesis(monkeypatch):
    explorer = Run17ReinforcementExplorer()
    key = ("room_test", 8, 8)
    explorer.interactables[key] = {
        "attempts": 1,
        "progressions": 0,
        "choice_menus": 1,
        "usefulness": "choice_pending",
        "approaches": [{"x": 7, "y": 8, "direction": "right"}],
    }
    explorer.choice_trials.append(
        {
            "room": "room_test",
            "context_x": 8,
            "context_y": 8,
            "signature": "menu",
            "attempts": [0] * 9,
            "failures": [0] * 9,
            "successes": [0] * 9,
            "successful_pattern": None,
        }
    )
    monkeypatch.setattr(explorer, "_adjacency", lambda room: {})

    result = explorer._route_to_retryable_story_interaction(
        "room_test",
        (7, 8),
    )

    assert result == ("right", key)
    region_key = ("room_test", *explorer._region((8, 8)))
    assert region_key not in explorer.screen_regions


def test_rewarded_retry_is_ranked_over_lower_value_retry():
    explorer = Run17ReinforcementExplorer()
    first = ("room_test", 8, 8)
    second = ("room_test", 12, 8)
    for key, source in ((first, (7, 8)), (second, (11, 8))):
        explorer.interactables[key] = {
            "attempts": 1,
            "progressions": 0,
            "choice_menus": 1,
            "usefulness": "choice_pending",
            "approaches": [
                {"x": source[0], "y": source[1], "direction": "right"}
            ],
        }
        explorer.choice_trials.append(
            {
                "room": "room_test",
                "context_x": key[1],
                "context_y": key[2],
                "signature": str(key),
                "attempts": [0] * 9,
                "failures": [0] * 9,
                "successes": [0] * 9,
                "successful_pattern": None,
            }
        )
    explorer.open_edges.update(
        {
            ("room_test", 7, 8, "right", 8, 8),
            ("room_test", 8, 8, "left", 7, 8),
            ("room_test", 8, 8, "right", 9, 8),
            ("room_test", 9, 8, "left", 8, 8),
            ("room_test", 9, 8, "right", 10, 8),
            ("room_test", 10, 8, "left", 9, 8),
            ("room_test", 10, 8, "right", 11, 8),
            ("room_test", 11, 8, "left", 10, 8),
        }
    )
    rewarded_key = explorer._interaction_key(second)
    explorer.reinforcement.begin_action(
        rewarded_key,
        kind="interaction_retry",
        context=None,
        step=1,
        settings=explorer.reward_settings,
    )
    explorer.reinforcement.reward_key(
        rewarded_key,
        20.0,
        event="progress",
        step=2,
        kind="interaction_retry",
    )

    direction, selected = explorer._route_to_retryable_story_interaction(
        "room_test",
        (7, 8),
    )

    assert direction == "right"
    assert selected == second
