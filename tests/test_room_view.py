import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from PIL import Image

from deltarune_agent.room_view import (
    RoomViewMemory,
    camera_region_coordinates,
    camera_viewport_box,
)
from deltarune_agent.telemetry import TelemetrySample


def _telemetry(**overrides) -> TelemetrySample:
    values = {
        "mode": "overworld",
        "room_id": 1,
        "room_name": "room_test",
        "x": 16,
        "y": 48,
        "object_name": "obj_mainchara",
        "received_at": 0,
        "version": 7,
        "room_width": 1000,
        "room_height": 1000,
        "camera_x": 0,
        "camera_y": 0,
        "camera_width": 128,
        "camera_height": 64,
    }
    values.update(overrides)
    return TelemetrySample(**values)


def _colored_regions(width: int = 128, height: int = 64) -> Image.Image:
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            pixels[x, y] = (
                (x // 32) * 40 + 10,
                (y // 32) * 100 + 20,
                30,
            )
    return image


def test_camera_regions_do_not_expand_to_reported_room_bounds():
    assert camera_region_coordinates(0, 0, 128, 64) == {
        (0, 0),
        (1, 0),
        (2, 0),
        (3, 0),
        (0, 1),
        (1, 1),
        (2, 1),
        (3, 1),
    }


def test_viewport_uses_full_matching_frame_and_centered_letterbox_crop():
    assert camera_viewport_box((1280, 960), (320, 240)) == (0, 0, 1280, 960)
    assert camera_viewport_box((1400, 960), (320, 240)) == (60, 0, 1340, 960)


def test_capture_persists_only_seen_camera_tiles_and_skips_player_region():
    with TemporaryDirectory() as directory:
        memory = RoomViewMemory(Path(directory) / "room_views")
        changed = memory.capture(_colored_regions(), _telemetry(), step=10)

        assert len(changed) == 6
        assert {(tile.region_x, tile.region_y) for tile in changed} == {
            (0, 0),
            (1, 0),
            (2, 0),
            (3, 0),
            (2, 1),
            (3, 1),
        }
        selected = next(tile for tile in changed if (tile.region_x, tile.region_y) == (3, 0))
        with Image.open(selected.path) as tile:
            assert tile.size == (128, 128)
            assert tile.convert("RGB").getpixel((64, 64)) == (130, 20, 30)

        index = json.loads(memory.index_path.read_text(encoding="utf-8"))
        room_data = index["rooms"]["room_test"]
        tiles = room_data["tiles"]
        assert not (memory.root / room_data["directory"] / "0_1.png").exists()
        assert "0,1" not in tiles
        assert "1,1" not in tiles
        assert "4,0" not in tiles
        assert len(tiles) == 6


def test_identical_camera_frame_does_not_rewrite_unchanged_tiles():
    with TemporaryDirectory() as directory:
        memory = RoomViewMemory(Path(directory) / "room_views")
        first = memory.capture(_colored_regions(), _telemetry(), step=0)
        mtimes = {tile.path: tile.path.stat().st_mtime_ns for tile in first}

        second = memory.capture(_colored_regions(), _telemetry(), step=5)

        assert second == []
        assert {path: path.stat().st_mtime_ns for path in mtimes} == mtimes


def test_complete_scene_tile_stays_stable_when_animation_changes():
    with TemporaryDirectory() as directory:
        memory = RoomViewMemory(Path(directory) / "room_views")
        first = memory.capture(_colored_regions(), _telemetry(), step=0)
        selected = next(tile for tile in first if (tile.region_x, tile.region_y) == (3, 0))

        changed_frame = _colored_regions()
        for y in range(0, 32):
            for x in range(96, 128):
                changed_frame.putpixel((x, y), (5, 210, 90))
        changed = memory.capture(changed_frame, _telemetry(), step=5)

        assert (3, 0) not in {(tile.region_x, tile.region_y) for tile in changed}
        with Image.open(selected.path) as tile:
            assert tile.convert("RGB").getpixel((64, 64)) == (130, 20, 30)


def test_partial_tile_fills_only_pixels_that_were_not_seen_before():
    with TemporaryDirectory() as directory:
        memory = RoomViewMemory(Path(directory) / "room_views")
        memory.capture(
            Image.new("RGB", (64, 64), (200, 20, 20)),
            _telemetry(
                x=999,
                y=999,
                camera_x=16,
                camera_width=16,
                camera_height=16,
            ),
            step=0,
        )
        changed = memory.capture(
            Image.new("RGB", (128, 64), (20, 200, 20)),
            _telemetry(
                x=999,
                y=999,
                camera_x=0,
                camera_width=32,
                camera_height=16,
            ),
            step=5,
        )

        tile_record = next(
            tile for tile in changed if (tile.region_x, tile.region_y) == (0, 0)
        )
        with Image.open(tile_record.path) as tile:
            rgb = tile.convert("RGB")
            assert rgb.getpixel((16, 16)) == (20, 200, 20)
            assert rgb.getpixel((96, 16)) == (200, 20, 20)


def test_scrolling_camera_extends_memory_without_filling_gap_or_unseen_room():
    with TemporaryDirectory() as directory:
        memory = RoomViewMemory(Path(directory) / "room_views")
        memory.capture(_colored_regions(), _telemetry(), step=0)
        shifted = Image.new("RGB", (64, 64), (200, 80, 20))
        memory.capture(
            shifted,
            _telemetry(
                x=160,
                y=48,
                camera_x=128,
                camera_width=64,
            ),
            step=5,
        )

        index = json.loads(memory.index_path.read_text(encoding="utf-8"))
        tiles = index["rooms"]["room_test"]["tiles"]
        assert "4,0" in tiles
        assert "5,0" in tiles
        assert "4,1" not in tiles  # Kris's conservative footprint covered it.
        assert "5,1" not in tiles
        assert "6,0" not in tiles


def test_blank_white_tiles_are_removed_when_scene_memory_loads():
    with TemporaryDirectory() as directory:
        root = Path(directory) / "room_views"
        room = root / "room-test"
        room.mkdir(parents=True)
        blank = room / "0_0.png"
        good = room / "1_0.png"
        Image.new("RGBA", (128, 128), (255, 255, 255, 255)).save(blank)
        Image.new("RGBA", (128, 128), (20, 40, 60, 255)).save(good)
        (root / "index.json").write_text(
            json.dumps(
                {
                    "version": 2,
                    "region_pixels": 32,
                    "rooms": {
                        "room_test": {
                            "directory": "room-test",
                            "tiles": {
                                "0,0": {
                                    "region_x": 0,
                                    "region_y": 0,
                                    "path": "room-test/0_0.png",
                                },
                                "1,0": {
                                    "region_x": 1,
                                    "region_y": 0,
                                    "path": "room-test/1_0.png",
                                },
                            },
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        memory = RoomViewMemory(root)
        tiles = memory.data["rooms"]["room_test"]["tiles"]

        assert "0,0" not in tiles
        assert not blank.exists()
        assert "1,0" in tiles
        assert good.exists()


def test_partial_transparent_tile_with_only_white_capture_is_unusable():
    from deltarune_agent.room_view import room_view_image_is_usable

    tile = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    for y in range(24):
        for x in range(128):
            tile.putpixel((x, y), (255, 255, 255, 255))

    assert not room_view_image_is_usable(tile)


def test_room_view_memory_ignores_broken_tile_data_and_still_keeps_index(tmp_path: Path):
    memory = RoomViewMemory(tmp_path / "room_views")
    frame = Image.new("RGB", (64, 64), (10, 20, 30))
    telemetry = SimpleNamespace(
        mode="overworld",
        room_name="room_test",
        room_id="room_test",
        camera_x=0.0,
        camera_y=0.0,
        camera_width=32.0,
        camera_height=32.0,
        bbox_left=None,
        bbox_top=None,
        bbox_right=None,
        bbox_bottom=None,
        x=16.0,
        y=16.0,
    )

    broken_tile = tmp_path / "room_views" / "room_test" / "0_0.png"
    broken_tile.parent.mkdir(parents=True, exist_ok=True)
    broken_tile.write_bytes(b"not-an-image")

    changed = memory.capture(frame, telemetry, step=1)

    assert changed == []
    assert memory.index_path.exists()
