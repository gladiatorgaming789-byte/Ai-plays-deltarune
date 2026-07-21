import json
from pathlib import Path
from types import SimpleNamespace

import pytest

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
