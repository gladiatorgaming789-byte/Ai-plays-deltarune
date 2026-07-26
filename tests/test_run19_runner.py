from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from deltarune_agent import run19_runner


def _args(tmp_path: Path) -> Namespace:
    return Namespace(
        steps=1,
        interval=0.0,
        countdown=0,
        region=(0, 0, 10, 10),
        visual_memory=tmp_path / "visual.json",
        no_telemetry=False,
        telemetry_port=42069,
        seed=0,
        memory=tmp_path / "navigation.json",
        live=False,
        stop_file=None,
        event_stream=False,
        game_window="deltarune",
        window_memory=tmp_path / "windows.json",
    )


class _Detector:
    memory_warning = None

    def __init__(self):
        self.saved = False

    def save_memory(self):
        self.saved = True


class _Receiver:
    def __init__(self):
        self.closed = False

    def diagnostics(self):
        return {"healthy": True}

    def close(self):
        self.closed = True


class _Policy:
    memory_warning = None

    def __init__(self):
        self.saved = False

    def save_memory(self):
        self.saved = True

    def summary(self):
        return {"policy": "ok"}


class _Controller:
    def __init__(self, *, fail_release=False):
        self.fail_release = fail_release

    def release_all(self):
        if self.fail_release:
            raise OSError("release failed")


class _Tracker:
    def __init__(self, directory: Path):
        self.directory = directory
        self.finished = False

    def finish(self, *_args, **_kwargs):
        self.finished = True


def test_constructor_failure_closes_resources_created_so_far(tmp_path):
    detector = _Detector()
    receiver = _Receiver()

    with patch.object(run19_runner, "ScreenObserver", return_value=object()), patch.object(
        run19_runner, "VisualStateDetector", return_value=detector
    ), patch.object(run19_runner, "CutsceneTracker", return_value=object()), patch.object(
        run19_runner, "TelemetryReceiver", return_value=receiver
    ), patch.object(
        run19_runner, "HierarchicalPolicy", side_effect=OSError("policy failed")
    ):
        with pytest.raises(OSError, match="policy failed"):
            run19_runner.run(_args(tmp_path))

    assert receiver.closed is True
    assert detector.saved is True


def test_primary_error_is_preserved_while_all_cleanup_continues(tmp_path):
    detector = _Detector()
    receiver = _Receiver()
    policy = _Policy()
    controller = _Controller(fail_release=True)
    tracker = _Tracker(tmp_path / "run")
    tracker.directory.mkdir()
    observer = SimpleNamespace(observe=lambda _step: (_ for _ in ()).throw(ValueError("frame failed")))

    with patch.object(run19_runner, "ScreenObserver", return_value=observer), patch.object(
        run19_runner, "VisualStateDetector", return_value=detector
    ), patch.object(run19_runner, "CutsceneTracker", return_value=object()), patch.object(
        run19_runner, "TelemetryReceiver", return_value=receiver
    ), patch.object(run19_runner, "HierarchicalPolicy", return_value=policy), patch.object(
        run19_runner, "KeyboardController", return_value=controller
    ), patch.object(run19_runner, "EpisodeTracker", return_value=tracker):
        with pytest.raises(ValueError, match="frame failed"):
            run19_runner.run(_args(tmp_path))

    assert receiver.closed is True
    assert policy.saved is True
    assert detector.saved is True
    assert tracker.finished is True


def test_cleanup_failure_without_primary_error_is_reported(tmp_path):
    errors = []

    result = run19_runner._attempt_cleanup(
        errors,
        "release keyboard input",
        lambda: (_ for _ in ()).throw(OSError("release failed")),
    )

    assert result is None
    assert len(errors) == 1
    assert "release keyboard input" in run19_runner._cleanup_message(errors)
