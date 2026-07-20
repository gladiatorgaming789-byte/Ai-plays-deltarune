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
