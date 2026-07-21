from PIL import Image

from deltarune_agent.screen_regions import (
    analyze_screen_regions,
    visible_region_coordinates,
)
from deltarune_agent.telemetry import TelemetrySample


def _telemetry(**overrides):
    values = {
        "mode": "overworld",
        "room_id": 1,
        "room_name": "room_test",
        "x": 16,
        "y": 48,
        "object_name": "obj_mainchara",
        "received_at": 0,
        "version": 7,
        "room_width": 128,
        "room_height": 64,
        "camera_x": 0,
        "camera_y": 0,
        "camera_width": 128,
        "camera_height": 64,
    }
    values.update(overrides)
    return TelemetrySample(**values)


def test_visible_regions_are_clipped_to_reported_room_bounds():
    regions = visible_region_coordinates(-32, -32, 128, 128, 96, 64)

    assert regions == {
        (0, 0),
        (0, 1),
        (1, 0),
        (1, 1),
        (2, 0),
        (2, 1),
    }


def test_missing_camera_data_does_not_invent_visible_regions():
    assert not visible_region_coordinates(None, None, None, None, 320, 240)


def test_visual_structure_forms_an_unconfirmed_region_hypothesis():
    frame = Image.new("RGB", (128, 64), (90, 90, 90))
    pixels = frame.load()
    for y in range(0, 32):
        for x in range(96, 128):
            pixels[x, y] = (245, 220, 40) if (x + y) % 4 < 2 else (15, 20, 45)

    observations = analyze_screen_regions(frame, _telemetry())
    interesting = next(
        item for item in observations if (item.region_x, item.region_y) == (3, 0)
    )

    assert len(observations) == 8
    assert interesting.interest > 0.2
    assert interesting.hypothesis == "possible_exit"
    assert all(
        item.hypothesis is None
        for item in observations
        if (item.region_x, item.region_y) == (0, 1)
    )


def test_repeated_floor_texture_does_not_hide_visible_right_edge_landmark():
    frame = Image.new("RGB", (320, 240), (105, 105, 105))
    pixels = frame.load()

    def checker(region_x, region_y, bright, dark):
        for y in range(region_y * 32, (region_y + 1) * 32):
            for x in range(region_x * 32, (region_x + 1) * 32):
                pixels[x, y] = bright if (x + y) % 4 < 2 else dark

    for region_x in range(2, 8):
        checker(region_x, 6, (250, 220, 30), (10, 15, 35))
    checker(8, 6, (250, 220, 30), (10, 15, 35))
    checker(8, 1, (190, 130, 80), (45, 30, 25))

    observations = analyze_screen_regions(
        frame,
        _telemetry(
            x=160,
            y=120,
            room_width=320,
            room_height=240,
            camera_width=320,
            camera_height=240,
        ),
    )
    landmark = next(
        item for item in observations if (item.region_x, item.region_y) == (8, 1)
    )

    assert landmark.hypothesis == "possible_exit"


def test_high_contrast_interior_is_not_assumed_to_be_interactable_or_npc():
    frame = Image.new("RGB", (192, 96), (90, 90, 90))
    pixels = frame.load()
    for y in range(32, 64):
        for x in range(96, 128):
            pixels[x, y] = (245, 220, 40) if (x + y) % 4 < 2 else (15, 20, 45)

    observations = analyze_screen_regions(
        frame,
        _telemetry(
            x=16,
            y=80,
            room_width=192,
            room_height=96,
            camera_width=192,
            camera_height=96,
        ),
    )
    interior = next(
        item for item in observations if (item.region_x, item.region_y) == (3, 1)
    )

    assert interior.interest > 0.2
    assert interior.hypothesis is None
