from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

from deltarune_agent.aligned_navigation_maps import (
    crop_navigation_map_to_room_bounds,
)
from deltarune_agent.aligned_room_view import player_render_box
from deltarune_agent.battle import BattleController
from deltarune_agent.observer import Observation
from deltarune_agent.perception import GameState, Perception, VisualFeatures
from deltarune_agent.run7_explorer import (
    ENTRY_ESCAPE_MAX_ATTEMPTS,
    Run7Explorer,
)
from deltarune_agent.telemetry import TelemetrySample


def sample(
    *,
    room: str = "room_a",
    x: float = 80.0,
    y: float = 80.0,
    foot_x: float | None = None,
    foot_y: float | None = None,
) -> TelemetrySample:
    return TelemetrySample(
        mode="overworld",
        room_id=1,
        room_name=room,
        x=x,
        y=y,
        object_name="obj_mainchara",
        received_at=1.0,
        player_controlled=True,
        player_foot_x=foot_x,
        player_foot_y=foot_y,
        room_width=320.0,
        room_height=240.0,
    )


def overworld() -> Perception:
    return Perception(
        state=GameState.OVERWORLD,
        confidence=1.0,
        features=VisualFeatures(0.0, 0.0, 0.0, 0.0, 0.0),
        source="test",
    )


def test_entry_escape_uses_normal_exploration_clock_and_learning():
    explorer = Run7Explorer()
    telemetry = sample(foot_x=80.0, foot_y=80.0)
    cell = explorer._cell(telemetry)
    explorer.observed_room = "room_a"
    explorer.observed_cell = cell
    explorer.entry_escape["room_a"] = (
        "down",
        cell,
        100,
    )

    action = explorer.choose(
        Observation(Image.new("RGB", (320, 240)), 0),
        overworld(),
        telemetry,
    )

    assert action.name == "down"
    assert "bounded move" in explorer.reason
    # The old early return left this at zero forever and never evaluated the
    # attempted movement on the following sample.
    assert explorer.navigation_tick == 1
    assert explorer.entry_escape_attempts["room_a"] == 1


def test_entry_escape_is_abandoned_after_bounded_attempts():
    explorer = Run7Explorer()
    room = "room_a"
    cell = (10, 10)
    explorer.entry_escape[room] = ("down", cell, 100)

    for attempt in range(ENTRY_ESCAPE_MAX_ATTEMPTS):
        plan = explorer._entry_escape_plan(room, cell)
        assert plan is not None
        assert plan[0] == "down"
        assert str(attempt + 1) in plan[2]

    assert explorer._entry_escape_plan(room, cell) is None
    assert room not in explorer.entry_escape
    assert explorer.entry_escape_abandons == 1


def test_persisted_room_entry_context_is_cleared(tmp_path: Path):
    memory = tmp_path / "navigation.json"
    memory.write_text(
        json.dumps(
            {
                "version": 3,
                "cell_size": 8,
                "room_entry_from": {"room_a": "room_b"},
            }
        ),
        encoding="utf-8",
    )

    explorer = Run7Explorer(memory_path=memory)

    assert explorer.room_entry_from == {}
    assert explorer.world.room_entry_from == {}
    assert explorer.cleared_persistent_room_entries == 1


def test_only_known_return_portal_can_still_be_used():
    explorer = Run7Explorer()
    warp = ("room_a", 5, 5, "down", "room_b", 5, 1)
    explorer.warps[warp] = 2
    explorer._portal_role = lambda _warp: "return/backtrack"  # type: ignore[method-assign]

    assert explorer._warp_is_priority_candidate(warp)

    explorer.room_entry_from["room_a"] = "room_b"
    assert not explorer._warp_is_priority_candidate(warp)


def test_exported_navigation_map_crops_to_exact_room_dimensions(
    tmp_path: Path,
):
    path = tmp_path / "room.png"
    Image.new("RGB", (1280, 1024), "black").save(path)
    record = {
        "room_width": 320.0,
        "room_height": 240.0,
        "origin_world": [0.0, 0.0],
        "tiles": {
            "0,0": {"region_x": 0, "region_y": 0},
            "9,7": {"region_x": 9, "region_y": 7},
        },
    }

    crop_navigation_map_to_room_bounds(
        path,
        record,
        region_world=32.0,
        pixels_per_world=4.0,
    )

    with Image.open(path) as result:
        assert result.size == (1280, 960)


def test_player_render_box_masks_more_than_collision_feet():
    telemetry = TelemetrySample(
        mode="overworld",
        room_id=1,
        room_name="room_a",
        x=206.0,
        y=97.0,
        object_name="obj_mainchara",
        received_at=1.0,
        player_origin_x=206.0,
        player_origin_y=97.0,
        player_bbox_left=206.0,
        player_bbox_top=122.0,
        player_bbox_right=224.0,
        player_bbox_bottom=135.0,
        sprite_width=19.0,
        sprite_height=38.0,
        sprite_xoffset=0.0,
        sprite_yoffset=0.0,
        image_xscale=1.0,
        image_yscale=1.0,
    )

    box = player_render_box(telemetry)

    assert box is not None
    assert box[1] < telemetry.player_bbox_top
    assert box[3] > telemetry.player_bbox_bottom
    assert box[3] - box[1] >= 38.0


def test_battle_threats_are_returned_in_full_frame_coordinates():
    frame = Image.new("RGB", (320, 240), "black")
    draw = ImageDraw.Draw(frame)
    draw.rectangle((98, 98, 102, 102), fill="white")

    threats = BattleController().detect_threats(frame)

    assert threats
    threat = min(threats, key=lambda item: abs(item.x - 100) + abs(item.y - 100))
    assert 98 <= threat.x <= 102
    assert 98 <= threat.y <= 102
