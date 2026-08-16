from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PIL import Image

from deltarune_agent import run19_runner
from deltarune_agent.actions import Action
from deltarune_agent.observer import Observation


def _args(tmp_path: Path) -> Namespace:
    return Namespace(
        steps=1,
        interval=0.0,
        speed="auto",
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


def test_manual_speed_scales_interval_and_is_recorded(tmp_path):
    args = _args(tmp_path)
    args.no_telemetry = True
    args.speed = "10"
    args.interval = 0.20
    observation = Observation(
        step=0,
        frame=Image.new("RGB", (2, 2), "black"),
    )
    perception = SimpleNamespace(
        state=SimpleNamespace(value="overworld"),
        confidence=0.99,
        source="test",
        features=SimpleNamespace(as_dict=lambda: {}),
    )

    class Detector(_Detector):
        def classify(self, _frame):
            return perception

        def learn_from_telemetry(self, *_args):
            pass

    class Cutscene:
        def update(self, visual, *_args):
            return visual

        def note_action(self, *_args):
            pass

    class Policy(_Policy):
        reason = "test decision"
        last_visual_valid = True

        def observe_room_trace(self, _trace):
            pass

        def validate_observation(self, value, _telemetry):
            return value

        def choose(self, *_args):
            return Action("confirm", ("z",), duration=0.10, cooldown=0.20)

        def drain_map_updates(self):
            return []

        def decision_context(self):
            return {"kind": "test"}

        def prediction_snapshot(self):
            return {}

    class Controller(_Controller):
        def __init__(self):
            super().__init__()
            self.multipliers = []
            self.actions = []

        def set_speed_multiplier(self, multiplier):
            self.multipliers.append(multiplier)

        def execute(self, action):
            self.actions.append(action.name)

    class Tracker(_Tracker):
        def __init__(self, directory):
            super().__init__(directory)
            self.records = []
            self.finish_kwargs = {}

        def record(self, *_args, **kwargs):
            self.records.append(kwargs)

        def finish(self, *_args, **kwargs):
            self.finished = True
            self.finish_kwargs = kwargs

    detector = Detector()
    policy = Policy()
    controller = Controller()
    tracker = Tracker(tmp_path / "run")
    tracker.directory.mkdir()
    sleeps = []

    with patch.object(
        run19_runner, "ScreenObserver", return_value=SimpleNamespace(observe=lambda _step: observation)
    ), patch.object(
        run19_runner, "VisualStateDetector", return_value=detector
    ), patch.object(
        run19_runner, "CutsceneTracker", return_value=Cutscene()
    ), patch.object(
        run19_runner, "HierarchicalPolicy", return_value=policy
    ), patch.object(
        run19_runner, "KeyboardController", return_value=controller
    ), patch.object(
        run19_runner, "EpisodeTracker", return_value=tracker
    ), patch.object(
        run19_runner, "fuse_perception", side_effect=lambda visual, _telemetry: visual
    ), patch.object(
        run19_runner.time, "sleep", side_effect=sleeps.append
    ):
        run19_runner.run(args)

    assert controller.multipliers == [10.0]
    assert controller.actions == ["confirm"]
    assert sleeps == [0.02]
    speed = tracker.records[0]["decision_context"]["speed"]
    assert speed["requested"] == "10"
    assert speed["effective_multiplier"] == 10.0
    assert speed["effective_action_duration_seconds"] == 0.01
    assert speed["effective_cooldown_seconds"] == 0.02
    assert tracker.finish_kwargs["config"]["speed"] == "10"


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


def test_population_training_requires_live_input_and_telemetry(tmp_path):
    args = _args(tmp_path)
    args.training = True
    with pytest.raises(ValueError, match="requires --live"):
        run19_runner.run(args)
    args.live = True
    args.no_telemetry = True
    with pytest.raises(ValueError, match="requires telemetry"):
        run19_runner.run(args)


def test_synthetic_population_run_sends_one_input_and_records_four_shadows(tmp_path):
    args = _args(tmp_path)
    args.training = True
    args.live = True
    args.speed = "1"
    observation = Observation(
        step=0,
        frame=Image.new("RGB", (2, 2), "black"),
    )
    perception = SimpleNamespace(
        state=run19_runner.GameState.OVERWORLD,
        confidence=0.99,
        source="telemetry",
        features=SimpleNamespace(as_dict=lambda: {}),
    )
    telemetry = SimpleNamespace(
        room_name="room_test",
        room_id=1,
        mode="overworld",
        player_controlled=True,
        x=16.0,
        y=24.0,
        as_dict=lambda: {
            "room_name": "room_test",
            "x": 16.0,
            "y": 24.0,
            "player_controlled": True,
        },
    )

    class Detector(_Detector):
        def classify(self, _frame):
            return perception

        def learn_from_telemetry(self, *_args):
            pass

    class Receiver(_Receiver):
        latest_speed = None

        def poll(self):
            return telemetry

        def drain_overworld_trace(self):
            return []

        def diagnostics(self):
            return {"received_packets": 1, "valid_packets": 1, "invalid_packets": 0}

    class Cutscene:
        def update(self, visual, *_args):
            return visual

        def note_action(self, *_args):
            pass

    training_snapshot = {
        "active_candidate": "balanced",
        "candidates": [
            {"id": candidate_id, "shadow_ranking": [{"id": "move:right"}]}
            for candidate_id in ("balanced", "explorer", "progress", "loop_safe")
        ],
    }

    class Policy(_Policy):
        reason = "population choice"
        last_visual_valid = True
        strategy_warning = None

        def __init__(self):
            super().__init__()
            self.observed = 0
            self.handoffs = 0

        def observe_room_trace(self, _trace):
            pass

        def validate_observation(self, value, _telemetry):
            return value

        def choose(self, *_args):
            return Action("right", ("right",), duration=0.0, cooldown=0.0)

        def drain_map_updates(self):
            return []

        def observe_training_step(self, **_kwargs):
            self.observed += 1

        def commit_training_handoff(self):
            self.handoffs += 1

        def training_safe_to_stop(self):
            return True

        def decision_context(self):
            return {"kind": "population"}

        def prediction_snapshot(self):
            return {"training": training_snapshot}

    class Controller(_Controller):
        def __init__(self):
            super().__init__()
            self.actions = []

        def set_target_window(self, _hwnd):
            pass

        def set_background_input(self, _enabled):
            pass

        def set_speed_multiplier(self, _multiplier):
            pass

        def execute(self, action):
            self.actions.append(action.name)

    class Tracker(_Tracker):
        def __init__(self, directory):
            super().__init__(directory)
            self.records = []

        def record(self, *_args, **kwargs):
            self.records.append(kwargs)

    class Workspace:
        def __init__(self):
            self.navigation_path = tmp_path / "stage" / "navigation.json"
            self.visual_memory_path = tmp_path / "stage" / "visual.json"
            self.window_memory_path = tmp_path / "stage" / "windows.json"
            self.navigation_path.parent.mkdir()
            self.finalized = False

        def coordinator(self):
            return object()

        def finalize(self, *_args, **_kwargs):
            self.finalized = True

    detector = Detector()
    receiver = Receiver()
    policy = Policy()
    controller = Controller()
    tracker = Tracker(tmp_path / "run")
    tracker.directory.mkdir()
    workspace = Workspace()
    window = SimpleNamespace(hwnd=9, title="DELTARUNE", executable="DELTARUNE.exe")

    with patch.object(
        run19_runner, "EpisodeTracker", return_value=tracker
    ), patch.object(
        run19_runner.TrainingWorkspace, "create", return_value=workspace
    ), patch.object(
        run19_runner, "ScreenObserver", return_value=SimpleNamespace(observe=lambda _step: observation)
    ), patch.object(
        run19_runner, "VisualStateDetector", return_value=detector
    ), patch.object(
        run19_runner, "CutsceneTracker", return_value=Cutscene()
    ), patch.object(
        run19_runner, "TelemetryReceiver", return_value=receiver
    ), patch.object(
        run19_runner, "HierarchicalPolicy", return_value=policy
    ), patch.object(
        run19_runner, "KeyboardController", return_value=controller
    ), patch.object(
        run19_runner, "focus_window", return_value=window
    ), patch.object(
        run19_runner, "remember_window"
    ), patch.object(
        run19_runner, "is_window_foreground", return_value=True
    ), patch.object(
        run19_runner, "fuse_perception", side_effect=lambda visual, _telemetry: visual
    ), patch.object(
        run19_runner.time, "sleep"
    ):
        run19_runner.run(args)

    assert controller.actions == ["right"]
    assert policy.observed == 1
    assert policy.handoffs == 1
    assert len(tracker.records) == 1
    assert len(tracker.records[0]["prediction_snapshot"]["training"]["candidates"]) == 4
    assert workspace.finalized is True
