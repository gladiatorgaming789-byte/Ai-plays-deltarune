import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from deltarune_agent.progress import EpisodeTracker


def test_rapid_consecutive_runs_get_distinct_output_directories(tmp_path: Path):
    first = EpisodeTracker(tmp_path)
    second = EpisodeTracker(tmp_path)

    assert first.directory != second.directory
    assert first.directory.is_dir()
    assert second.directory.is_dir()


def test_frame_interval_must_be_positive(tmp_path: Path):
    with pytest.raises(ValueError, match="frame_interval must be positive"):
        EpisodeTracker(tmp_path, frame_interval=0)


def test_record_keeps_logging_when_frame_saving_fails(tmp_path: Path):
    tracker = EpisodeTracker(tmp_path, frame_interval=1)

    class BrokenFrame:
        def save(self, *_args, **_kwargs):
            raise OSError("disk is full")

    observation = SimpleNamespace(step=1, frame=BrokenFrame())
    perception = SimpleNamespace(
        state=SimpleNamespace(value="overworld"),
        confidence=0.9,
        features=SimpleNamespace(as_dict=lambda: {}),
        source="test",
    )
    action = SimpleNamespace(name="wait")

    tracker.record(observation, perception, None, action, "test reason", False)

    lines = tracker.events.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["action"] == "wait"
    assert event["visual_valid"] is True


def test_finished_run_packages_predictions_memory_and_rendered_maps(
    tmp_path: Path,
):
    memory = tmp_path / "memory"
    room_views = memory / "room_views"
    tile_directory = room_views / "room_test"
    tile_directory.mkdir(parents=True)
    Image.new("RGBA", (128, 128), (64, 96, 128, 255)).save(
        tile_directory / "0_0.png"
    )
    (room_views / "index.json").write_text(
        json.dumps(
            {
                "version": 3,
                "region_pixels": 32,
                "pixels_per_world": 4,
                "tile_pixels": 128,
                "rooms": {
                    "room_test": {
                        "tiles": {
                            "0,0": {
                                "region_x": 0,
                                "region_y": 0,
                                "path": "room_test/0_0.png",
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    navigation = memory / "navigation.json"
    navigation.write_text(
        json.dumps(
            {
                "version": 3,
                "cell_size": 8,
                "cells": [{"room": "room_test", "x": 1, "y": 1, "visits": 2}],
                "open_edges": [],
                "blocked_edges": [],
                "interactables": [],
                "warps": [],
                "warp_portals": [
                    {
                        "id": "portal-test",
                        "from_room": "room_test",
                        "to_room": "room_next",
                        "action": "right",
                        "role": "progression",
                        "source_footprint": {
                            "center": [2, 1],
                            "bounds": [2, 1, 2, 1],
                        },
                    }
                ],
                "screen_regions": [
                    {
                        "room": "room_test",
                        "region_x": 0,
                        "region_y": 0,
                        "hypothesis": "possible_interactable",
                        "guess_id": "room_test@0,0",
                        "guess_model_version": 2,
                        "guess_state": "proposed",
                        "guess_confidence": 0.7,
                        "feature_box_world": [8, 8, 16, 16],
                        "anchor_world": [12, 12],
                        "anchor_cell": [1, 1],
                        "views": 2,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    tracker = EpisodeTracker(tmp_path / "runs", frame_interval=1)
    observation = SimpleNamespace(
        step=0,
        frame=Image.new("RGB", (32, 24), (20, 30, 40)),
        visual_valid=True,
    )
    perception = SimpleNamespace(
        state=SimpleNamespace(value="overworld"),
        confidence=0.99,
        features=SimpleNamespace(as_dict=lambda: {}),
        source="telemetry",
    )
    telemetry = SimpleNamespace(
        as_dict=lambda: {
            "room_name": "room_test",
            "player_foot_x": 12,
            "player_foot_y": 20,
            "x": 10,
            "y": 10,
        }
    )
    action = SimpleNamespace(name="right")
    tracker.record(
        observation,
        perception,
        telemetry,
        action,
        "inspect exact object",
        False,
        decision_context={"kind": "visual_guess", "id": "room_test@0,0"},
        map_updates=[{"type": "screen_region", "room": "room_test"}],
        prediction_snapshot={"selected_guess_id": "room_test@0,0"},
    )
    tracker.finish(
        {"story_progress_events": 0},
        navigation_path=navigation,
        room_views_path=room_views,
    )

    prediction = json.loads(
        tracker.predictions.read_text(encoding="utf-8").splitlines()[0]
    )
    assert prediction["location"]["x"] == 12
    assert prediction["location"]["y"] == 20
    assert prediction["prediction_snapshot"]["selected_guess_id"] == "room_test@0,0"
    assert tracker.navigation_updates.is_file()
    assert (tracker.directory / "navigation.json").is_file()
    assert (tracker.directory / "room_views" / "index.json").is_file()
    assert (tracker.directory / "navigation_maps" / "room_test.png").is_file()
    manifest = json.loads(tracker.manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["recording"]["predictions"] == 1
