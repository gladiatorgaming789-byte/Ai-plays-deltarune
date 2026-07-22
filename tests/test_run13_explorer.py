from __future__ import annotations

from collections import deque

from PIL import Image, ImageDraw

from deltarune_agent.observer import Observation
from deltarune_agent.run13_explorer import Run13Explorer
from deltarune_agent.run13_screen_regions import (
    FLOOR_EVIDENCE_PREFIX,
    analyze_screen_regions,
)
from deltarune_agent.telemetry import TelemetrySample


def sample(
    *,
    x: float = 180.0,
    y: float = 113.0,
    bbox: tuple[float, float, float, float] = (171.0, 100.0, 189.0, 113.0),
) -> TelemetrySample:
    return TelemetrySample(
        mode="overworld",
        room_id=1,
        room_name="room_test",
        x=x,
        y=y,
        object_name="obj_mainchara",
        received_at=1.0,
        player_controlled=True,
        player_foot_x=x,
        player_foot_y=y,
        player_bbox_left=bbox[0],
        player_bbox_top=bbox[1],
        player_bbox_right=bbox[2],
        player_bbox_bottom=bbox[3],
        room_width=320.0,
        room_height=240.0,
        camera_x=0.0,
        camera_y=0.0,
        camera_width=320.0,
        camera_height=240.0,
    )


def test_two_position_pinch_with_one_unsampled_cell_escapes_sideways():
    explorer = Run13Explorer()
    room = "room_test"
    top = (33, 20)
    bottom = (33, 22)
    explorer.recent_cells = deque(
        [
            (room, *top),
            (room, *bottom),
            (room, *top),
            (room, *bottom),
            (room, *top),
            (room, *bottom),
            (room, *top),
            (room, *bottom),
        ],
        maxlen=24,
    )
    explorer.blocked[(room, *top, "up")] = 5
    explorer.blocked[(room, *bottom, "down")] = 5

    direction = explorer._least_visited_direction(room, top, "up")

    assert direction in {"left", "right"}
    assert explorer._active_pinch_direction == direction


def test_player_overlapping_region_does_not_count_as_animation():
    explorer = Run13Explorer()
    key = ("room_test", 5, 3)
    explorer.screen_regions[key] = {
        "last_seen_step": 5,
        "last_signature": "bbbb",
    }
    explorer._viewpoint_signatures[("room_test", 5, 3, 0, 0)] = "aaaa"

    explorer._update_same_view_motion(
        Observation(Image.new("RGB", (320, 240)), step=5),
        sample(),
    )

    assert explorer.screen_regions[key].get("motion", 0.0) == 0.0
    assert explorer.player_motion_regions_ignored == 1


def test_visible_floor_continuation_beats_black_void_at_bottom_edge():
    image = Image.new("RGB", (320, 240), (8, 8, 8))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 319, 218), fill=(80, 45, 28))
    draw.rectangle((230, 210, 261, 239), fill=(195, 105, 55))

    observations = analyze_screen_regions(image, sample())
    exits = [item for item in observations if item.hypothesis == "possible_exit"]
    floor_exit = next(
        item
        for item in exits
        if item.edge_hint == "bottom"
        and item.feature_summary.startswith(FLOOR_EVIDENCE_PREFIX)
    )

    assert floor_exit.passage_box_world is not None
    assert floor_exit.passage_box_world[0] <= 230
    assert floor_exit.passage_box_world[2] >= 261
    assert floor_exit.edge_opening_score >= 0.82
