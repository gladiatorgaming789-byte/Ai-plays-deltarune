from __future__ import annotations

from PIL import Image, ImageDraw

from deltarune_agent.run14_explorer import Run14Explorer
from deltarune_agent.run14_screen_regions import (
    DOORWAY_FACADE_PREFIX,
    _doorway_facades,
    analyze_screen_regions,
)
from deltarune_agent.telemetry import TelemetrySample


def sample() -> TelemetrySample:
    return TelemetrySample(
        mode="overworld",
        room_id=1,
        room_name="room_test",
        x=160.0,
        y=120.0,
        object_name="obj_mainchara",
        received_at=1.0,
        player_controlled=True,
        player_foot_x=160.0,
        player_foot_y=120.0,
        player_bbox_left=151.0,
        player_bbox_top=107.0,
        player_bbox_right=169.0,
        player_bbox_bottom=120.0,
        room_width=320.0,
        room_height=240.0,
        camera_x=0.0,
        camera_y=0.0,
        camera_width=320.0,
        camera_height=240.0,
    )


def doorway_scene() -> Image.Image:
    image = Image.new("RGB", (320, 240), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((38, 22, 284, 218), fill=(220, 195, 120))
    draw.rectangle((234, 24, 271, 72), fill=(40, 15, 10))
    draw.rectangle((237, 27, 268, 69), fill=(155, 65, 35))
    draw.rectangle((240, 30, 264, 45), fill=(50, 55, 80))
    draw.rectangle((241, 52, 245, 57), fill=(240, 180, 70))
    return image


def test_framed_upper_wall_door_becomes_a_visual_exit():
    observations = analyze_screen_regions(doorway_scene(), sample())

    doorway = next(
        item
        for item in observations
        if item.feature_summary.startswith(DOORWAY_FACADE_PREFIX)
    )

    assert doorway.hypothesis == "possible_exit"
    assert doorway.edge_hint == "top"
    assert doorway.edge_opening_score >= 0.86
    assert doorway.feature_box_world is not None
    assert doorway.focus_world_y is not None
    assert doorway.focus_world_y > doorway.feature_box_world[3]


def test_wide_horizontal_wall_feature_is_not_a_doorway_facade():
    image = Image.new("RGB", (320, 240), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((38, 22, 284, 218), fill=(220, 195, 120))
    draw.rectangle((80, 26, 220, 62), fill=(45, 35, 25))
    draw.rectangle((84, 30, 216, 58), fill=(90, 105, 65))

    assert _doorway_facades(image, sample()) == []


def test_same_rejected_path_probe_cannot_revive_itself_forever():
    explorer = Run14Explorer()
    key = ("room_test", 8, 6)
    explorer.screen_regions[key] = {
        "hypothesis": "possible_exit",
        "path_continuation": True,
        "path_probe": [34, 25, "right"],
        "guess_state": "rejected",
        "failed_approaches": 2,
        "approach_attempts": 7,
        "completed_tests": 0,
        "inspections": 0,
        "guess_confidence": 0.05,
    }

    explorer._remember_path_continuation(("room_test", 34, 25, "right"))

    record = explorer.screen_regions[key]
    assert record["hypothesis"] is None
    assert record["path_continuation"] is False
    assert record["path_continuation_locked"] is True
    assert record["guess_state"] == "retired"
    assert explorer.blocked_path_continuation_revivals == 1


def test_inset_doorway_facade_is_not_retired_as_dark_scenery():
    explorer = Run14Explorer()
    room = "room_test"
    key = (room, 7, 1)
    explorer.room_dimensions[room] = (320.0, 240.0)
    explorer.screen_regions[key] = {
        "hypothesis": "possible_exit",
        "guess_state": "proposed",
        "visual_summary": f"{DOORWAY_FACADE_PREFIX} near the upper wall",
        "feature_box_world": [234.0, 24.0, 271.0, 72.0],
        "passage_box_world": [234.0, 24.0, 271.0, 72.0],
        "edge_hint": "top",
        "edge_opening_score": 0.86,
        "edge_width_ratio": 0.116,
        "independent_views": 5,
        "views": 5,
        "walkable_evidence": False,
        "failed_approaches": 0,
        "completed_tests": 0,
        "inspections": 0,
        "guess_confidence": 0.56,
    }

    explorer._retire_unsupported_visual_exits(room)

    record = explorer.screen_regions[key]
    assert record["hypothesis"] == "possible_exit"
    assert record["guess_state"] == "proposed"
    assert record["doorway_facade"] is True
    assert record["guess_confidence"] >= 0.72


def test_new_path_continuation_remembers_its_exact_probe():
    explorer = Run14Explorer()

    explorer._remember_path_continuation(("room_test", 34, 25, "right"))

    record = explorer.screen_regions[("room_test", 8, 6)]
    assert record["path_probe"] == [34, 25, "right"]


def test_doorway_anchor_commits_through_the_frame():
    explorer = Run14Explorer()
    key = ("room_test", 7, 1)
    explorer.screen_regions[key] = {
        "hypothesis": "possible_exit",
        "guess_state": "approaching",
        "doorway_facade": True,
        "anchor_cell": [31, 9],
        "edge_hint": "top",
        "guess_confidence": 0.8,
        "last_seen_sequence": 1,
        "completed_tests": 0,
        "failed_approaches": 0,
    }
    explorer.visual_goal = key

    plan = explorer._direction_to_visual_hypothesis(
        "room_test",
        (31, 9),
        allowed_hypotheses={"possible_exit"},
    )

    assert plan == ("up", "possible_exit", (7, 1))
    assert explorer.screen_regions[key]["doorway_probe_attempts"] == 1
