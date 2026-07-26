from types import SimpleNamespace
from unittest.mock import patch

import pytest

from deltarune_agent.actions import ACTIONS, Action
from deltarune_agent import controller as controller_module


class _FakePyAutoGUI(SimpleNamespace):
    def __init__(self):
        super().__init__(FAILSAFE=True, PAUSE=0.0)
        self.events = []
        self.fail_down_for = None
        self.fail_up_once_for = None

    def keyDown(self, key):
        self.events.append(("down", key))
        if key == self.fail_down_for:
            raise OSError(f"down failed for {key}")

    def keyUp(self, key):
        self.events.append(("up", key))
        if key == self.fail_up_once_for:
            self.fail_up_once_for = None
            raise OSError(f"up failed for {key}")


def test_continuous_movement_stays_held_until_direction_changes():
    fake = _FakePyAutoGUI()
    with patch.object(controller_module, "pyautogui", fake), patch.object(
        controller_module.time, "sleep"
    ):
        controller = controller_module.KeyboardController(live=True)
        controller.execute(ACTIONS["down"])
        controller.execute(ACTIONS["down"])
        controller.execute(ACTIONS["right"])
        controller.release_all()

    assert fake.events == [
        ("down", "down"),
        ("up", "down"),
        ("down", "right"),
        ("up", "right"),
    ]


def test_button_action_releases_continuous_movement_first():
    fake = _FakePyAutoGUI()
    with patch.object(controller_module, "pyautogui", fake), patch.object(
        controller_module.time, "sleep"
    ):
        controller = controller_module.KeyboardController(live=True)
        controller.execute(ACTIONS["left"])
        controller.execute(ACTIONS["confirm"])

    assert fake.events == [
        ("down", "left"),
        ("up", "left"),
        ("down", "z"),
        ("up", "z"),
    ]


def test_background_input_targets_only_the_game_window():
    fake = _FakePyAutoGUI()
    targeted = []
    with patch.object(controller_module, "pyautogui", fake), patch.object(
        controller_module,
        "post_window_key",
        side_effect=lambda hwnd, key, pressed: targeted.append((hwnd, key, pressed)),
    ), patch.object(controller_module.time, "sleep"):
        controller = controller_module.KeyboardController(live=True, target_hwnd=456)
        controller.set_background_input(True)
        controller.execute(ACTIONS["right"])
        controller.execute(ACTIONS["confirm"])

    assert fake.events == []
    assert targeted == [
        (456, "right", True),
        (456, "right", False),
        (456, "z", True),
        (456, "z", False),
    ]


def test_switching_input_backend_releases_held_key_before_change():
    fake = _FakePyAutoGUI()
    targeted = []
    with patch.object(controller_module, "pyautogui", fake), patch.object(
        controller_module,
        "post_window_key",
        side_effect=lambda hwnd, key, pressed: targeted.append((hwnd, key, pressed)),
    ), patch.object(controller_module.time, "sleep"):
        controller = controller_module.KeyboardController(live=True, target_hwnd=456)
        controller.execute(ACTIONS["down"])
        controller.set_background_input(True)
        controller.execute(ACTIONS["down"])
        controller.release_all()

    assert fake.events == [("down", "down"), ("up", "down")]
    assert targeted == [(456, "down", True), (456, "down", False)]


def test_partial_continuous_press_rolls_back_new_keys():
    fake = _FakePyAutoGUI()
    fake.fail_down_for = "right"
    diagonal = Action("diagonal", ("down", "right"), 0.0, 0.0, continuous=True)
    with patch.object(controller_module, "pyautogui", fake), patch.object(
        controller_module.time, "sleep"
    ):
        controller = controller_module.KeyboardController(live=True)
        with pytest.raises(OSError, match="down failed"):
            controller.execute(diagonal)

    assert controller.held_keys == ()
    assert fake.events == [
        ("down", "down"),
        ("down", "right"),
        ("up", "down"),
    ]


def test_failed_release_remains_tracked_and_can_be_retried():
    fake = _FakePyAutoGUI()
    with patch.object(controller_module, "pyautogui", fake), patch.object(
        controller_module.time, "sleep"
    ):
        controller = controller_module.KeyboardController(live=True)
        controller.execute(ACTIONS["down"])
        fake.fail_up_once_for = "down"
        with pytest.raises(OSError, match="up failed"):
            controller.release_all()
        assert controller.held_keys == ("down",)
        controller.release_all()

    assert controller.held_keys == ()
    assert fake.events == [
        ("down", "down"),
        ("up", "down"),
        ("up", "down"),
    ]


def test_partial_button_press_releases_only_successful_keys():
    fake = _FakePyAutoGUI()
    fake.fail_down_for = "x"
    combo = Action("combo", ("z", "x"), 0.0, 0.0)
    with patch.object(controller_module, "pyautogui", fake), patch.object(
        controller_module.time, "sleep"
    ):
        controller = controller_module.KeyboardController(live=True)
        with pytest.raises(OSError, match="down failed"):
            controller.execute(combo)

    assert fake.events == [
        ("down", "z"),
        ("down", "x"),
        ("up", "z"),
    ]
