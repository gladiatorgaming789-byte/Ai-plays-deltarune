from __future__ import annotations

from PIL import Image, ImageDraw

from deltarune_agent.run13_screen_regions import FLOOR_EVIDENCE_PREFIX
from deltarune_agent.run15_explorer import Run15Explorer
from deltarune_agent.run15_screen_regions import (
    SCROLLING_FLOOR_CONTACT_PREFIX,
    analyze_screen_regions,
)
from deltarune_agent.telemetry import TelemetrySample


def sample(
    *,
    room: str = "room_test",
    room_width: float = 320.0,
    room_height: float = 240.0,
    camera_width: float = 320.0,
    camera_height: float = 240.0,
) -> TelemetrySample:
    return TelemetrySample(
        mode="overworld",
        room_id=1,
        room_name=room,
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
        room_width=room_width,
        room_height=room_height,
        camera_x=0.0,
        camera_y=0.0,
        camera_width=camera_width,
        camera_height=camera_height,
    )


def floor_contact_scene() -> Image.Image:
    image = Image.new("RGB", (320, 240), (8, 8, 8))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 319, 218), fill=(80, 45, 28))
    draw.rectangle((230, 210, 261, 239), fill=(195, 105, 55))
    return image


def test_story_progress_retries_a_previously_locked_doorway_once():
    explorer = Run15Explorer()
    room = "room_alphysclass"
    key = (room, 7, 1)
    explorer.screen_regions[key] = {
        "hypothesis": "possible_exit",
        "guess_state": "rejected",
        "doorway_facade": True,
        "story_sensitive_doorway": True,
        "doorway_failed_story_epoch": 2,
        "failed_approaches": 3,
        "completed_tests": 0,
        "inspections": 0,
        "approach_attempts": 22,
        "doorway_probe_attempts": 8,
        "guess_confidence": 0.72,
    }
    explorer.story_epoch = 3

    explorer._revive_doorways_after_story_progress(room)
    explorer._revive_doorways_after_story_progress(room)

    record = explorer.screen_regions[key]
    assert record["hypothesis"] == "possible_exit"
    assert record["guess_state"] == "proposed"
    assert record["failed_approaches"] == 0
    assert record["approach_attempts"] == 0
    assert record["doorway_story_retry_epoch"] == 3
    assert explorer.story_unlocked_doorway_retries == 1


def test_story_progress_does_not_retry_door_failed_in_same_epoch():
    explorer = Run15Explorer()
    room = "room_alphysclass"
    key = (room, 7, 1)
    explorer.screen_regions[key] = {
        "hypothesis": "possible_exit",
        "guess_state": "rejected",
        "doorway_facade": True,
        "doorway_failed_story_epoch": 3,
        "failed_approaches": 3,
    }
    explorer.story_epoch = 3

    explorer._revive_doorways_after_story_progress(room)

    assert explorer.screen_regions[key]["guess_state"] == "rejected"
    assert explorer.story_unlocked_doorway_retries == 0


def test_animation_bonus_is_durable_and_applies_only_once():
    explorer = Run15Explorer()
    key = ("room_test", 1, 1)
    explorer.screen_regions[key] = {
        "hypothesis": "possible_character",
        "visual_summary": "compact colorful feature",
        "entity_approach_directions": 2,
        "motion": 2.0,
        "colorfulness": 0.2,
        "guess_confidence": 0.4,
        "evidence_summary": "collision-backed compact obstruction",
    }

    explorer._apply_animated_character_bonus("room_test")
    first = explorer.screen_regions[key]["guess_confidence"]
    explorer.screen_regions[key]["evidence_summary"] = "metadata refreshed"
    explorer._apply_animated_character_bonus("room_test")

    assert first == 0.52
    assert explorer.screen_regions[key]["guess_confidence"] == first
    assert explorer.screen_regions[key]["animated_bonus_applied"] is True
    assert explorer.animated_character_bonuses == 1


def test_long_scrolling_room_defers_exit_pressure_while_frontier_exists(monkeypatch):
    explorer = Run15Explorer()
    room = "room_dark2"
    explorer.room_dimensions[room] = (3640.0, 520.0)
    explorer.room_camera_dimensions[room] = (640.0, 480.0)
    explorer.story_stall_steps = 999
    monkeypatch.setattr(explorer, "_has_reachable_frontier", lambda *_args: True)

    assert explorer._progress_pressure(room, (200, 33)) is False
    assert explorer.long_room_frontier_deferrals == 1


def test_wide_room_floor_contact_is_not_promoted_to_an_exit():
    observations = analyze_screen_regions(
        floor_contact_scene(),
        sample(room="room_dark2", room_width=3640.0, room_height=240.0),
    )
    downgraded = [
        observation
        for observation in observations
        if observation.feature_summary.startswith(SCROLLING_FLOOR_CONTACT_PREFIX)
    ]

    assert downgraded
    assert all(observation.hypothesis is None for observation in downgraded)
    assert all(observation.edge_opening_score <= 0.38 for observation in downgraded)


def test_local_room_floor_contact_remains_a_possible_exit():
    observations = analyze_screen_regions(floor_contact_scene(), sample())
    exits = [
        observation
        for observation in observations
        if observation.hypothesis == "possible_exit"
        and observation.feature_summary.startswith(FLOOR_EVIDENCE_PREFIX)
    ]

    assert exits


def test_overselected_untested_visual_lead_is_retired():
    explorer = Run15Explorer()
    key = ("room_test", 8, 0)
    explorer.screen_regions[key] = {
        "hypothesis": "possible_exit",
        "guess_state": "approaching",
        "approach_attempts": 100,
        "completed_tests": 0,
        "inspections": 0,
        "path_continuation": False,
        "guess_confidence": 0.4,
    }

    explorer._retire_overselected_visual_leads("room_test")

    record = explorer.screen_regions[key]
    assert record["hypothesis"] is None
    assert record["guess_state"] == "retired"
    assert explorer.overselected_visual_leads_retired == 1
