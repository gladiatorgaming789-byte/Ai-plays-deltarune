import time

import pyautogui

from .actions import Action


class KeyboardController:
    def __init__(self, live: bool = False):
        self.live = live
        self.held_keys: tuple[str, ...] = ()
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.005

    def execute(self, action: Action) -> None:
        if not self.live:
            time.sleep(action.duration)
            return
        if action.continuous and action.keys:
            self._hold(action.keys)
            time.sleep(action.duration)
            return

        self.release_all()
        if not action.keys:
            time.sleep(action.duration)
            return
        for key in action.keys:
            pyautogui.keyDown(key)
        try:
            time.sleep(action.duration)
        finally:
            for key in reversed(action.keys):
                pyautogui.keyUp(key)

    def _hold(self, keys: tuple[str, ...]) -> None:
        for key in reversed(self.held_keys):
            if key not in keys:
                pyautogui.keyUp(key)
        for key in keys:
            if key not in self.held_keys:
                pyautogui.keyDown(key)
        self.held_keys = keys

    def release_all(self) -> None:
        if not self.held_keys:
            return
        keys = self.held_keys
        self.held_keys = ()
        if not self.live:
            return
        # Cleanup must still work after the mouse-triggered emergency stop.
        failsafe = pyautogui.FAILSAFE
        try:
            pyautogui.FAILSAFE = False
            for key in reversed(keys):
                pyautogui.keyUp(key)
        finally:
            pyautogui.FAILSAFE = failsafe
