from types import SimpleNamespace
from unittest.mock import patch

from deltarune_agent.actions import ACTIONS
from deltarune_agent import controller as controller_module


class _FakePyAutoGUI(SimpleNamespace):
    def __init__(self):
        super().__init__(FAILSAFE=True, PAUSE=0.0)
        self.events = []

    def keyDown(self, key):
        self.events.append(("down", key))

    def keyUp(self, key):
        self.events.append(("up", key))


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
        controller_module, "post_window_key", side_effect=lambda hwnd, key, pressed: targeted.append(
            (hwnd, key, pressed)
        )
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
        controller_module, "post_window_key", side_effect=lambda hwnd, key, pressed: targeted.append(
            (hwnd, key, pressed)
        )
    ), patch.object(controller_module.time, "sleep"):
        controller = controller_module.KeyboardController(live=True, target_hwnd=456)
        controller.execute(ACTIONS["down"])
        controller.set_background_input(True)
        controller.execute(ACTIONS["down"])
        controller.release_all()

    assert fake.events == [("down", "down"), ("up", "down")]
    assert targeted == [(456, "down", True), (456, "down", False)]
