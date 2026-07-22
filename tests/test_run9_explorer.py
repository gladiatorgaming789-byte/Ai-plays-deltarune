from __future__ import annotations

from collections import deque
from pathlib import Path

from PIL import Image

from deltarune_agent.aligned_room_view import AlignedRoomViewMemory
from deltarune_agent.observer import Observation
from deltarune_agent.room_view import RoomViewMemory
from deltarune_agent.run9_explorer import Run9Explorer
from deltarune_agent.telemetry import TelemetrySample


def sample(
    *,
    room: str = "room_test",
    x: float = 96.0,
    y: float = 80.0,
    camera_x: float = 0.0,
    camera_y: float = 0.0,
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
        player_foot_x=x,
        player_foot_y=y,
        room_width=320.0,
        room_height=240.0,
        camera_x=camera_x,
        camera_y=camera_y,
        camera_width=320.0,
        camera_height=240.0,
    )


def test_vertical_two_cell_pinch_chooses_perpendicular_escape():
    explorer = Run9Explorer()
    room = "room_alphysclass"
    top = (12, 14)
    bottom = (12, 15)
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
    explorer.blocked[(room, *top, "up")] = 1
    explorer.blocked[(room, *bottom, "down")] = 1

    direction = explorer._least_visited_direction(room, top, "up")

    assert direction in {"left", "right"}
    assert explorer.pinch_recoveries == 1
    assert explorer._active_pinch_direction == direction


def test_active_pinch_commits_to_the_perpendicular_direction():
    explorer = Run9Explorer()
    room = "room_alphysclass"
    cells = frozenset({(12, 14), (12, 15)})
    explorer._active_pinch_room = room
    explorer._active_pinch_cells = cells
    explorer._active_pinch_direction = "left"
    explorer._active_pinch_until = 20
    explorer.navigation_tick = 2

    direction, steps, reason = explorer._plan_exploration(room, (12, 14))

    assert direction == "left"
    assert steps >= 1
    assert "two-cell pinch" in reason


def test_same_camera_signature_change_counts_as_motion():
    explorer = Run9Explorer()
    room = "room_test"
    key = (room, 2, 3)
    explorer.screen_regions[key] = {
        "last_seen_step": 5,
        "last_signature": "bbbb",
    }
    explorer._viewpoint_signatures[(room, 2, 3, 0, 0)] = "aaaa"

    explorer._update_same_view_motion(
        Observation(Image.new("RGB", (320, 240)), step=5),
        sample(room=room),
    )

    assert explorer.screen_regions[key]["motion"] == 1.0
    assert explorer.same_view_motion_updates == 1


def test_animated_character_bonus_requires_same_view_motion_and_applies_once():
    explorer = Run9Explorer()
    room = "room_test"
    key = (room, 1, 1)
    explorer.screen_regions[key] = {
        "hypothesis": "possible_character",
        "visual_summary": "compact colorful feature",
        "entity_approach_directions": 2,
        "motion": 2.0,
        "colorfulness": 0.2,
        "guess_confidence": 0.4,
        "evidence_summary": "collision-backed compact obstruction",
    }

    explorer._apply_animated_character_bonus(room)
    first = explorer.screen_regions[key]["guess_confidence"]
    explorer._apply_animated_character_bonus(room)

    assert first == 0.52
    assert explorer.screen_regions[key]["guess_confidence"] == first
    assert explorer.animated_character_bonuses == 1


def test_walkable_inset_opening_is_retained_at_low_confidence():
    explorer = Run9Explorer()
    room = "room_test"
    key = (room, 8, 3)
    explorer.room_dimensions[room] = (320.0, 240.0)
    explorer.screen_regions[key] = {
        "hypothesis": "possible_exit",
        "guess_state": "proposed",
        "edge_hint": "right",
        "passage_box_world": [250.0, 96.0, 288.0, 128.0],
        "walkable_evidence": True,
        "failed_approaches": 0,
        "independent_views": 3,
        "guess_confidence": 0.7,
        "evidence_summary": "localized dark opening",
    }

    explorer._retire_unsupported_visual_exits(room)

    record = explorer.screen_regions[key]
    assert record["hypothesis"] == "possible_exit"
    assert record["embedded_opening"] is True
    assert record["guess_confidence"] == 0.44


def test_room_view_tile_failure_does_not_stop_capture(
    tmp_path: Path,
    monkeypatch,
):
    memory = AlignedRoomViewMemory(tmp_path / "room_views")

    def broken_capture(*_args, **_kwargs):
        raise SystemError("tile cannot extend outside image")

    monkeypatch.setattr(RoomViewMemory, "capture", broken_capture)

    changed = memory.capture(
        Image.new("RGB", (320, 240)),
        sample(),
        step=0,
    )

    assert changed == []
    assert memory.capture_failures == 1
    assert memory.last_capture_error == (
        "SystemError: tile cannot extend outside image"
    )
