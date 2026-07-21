import time

import pyautogui

from .actions import Action
from .window import post_window_key


class KeyboardController:
    def __init__(self, live: bool = False, target_hwnd: int | None = None):
        self.live = live
        self.target_hwnd = target_hwnd
        self.background_input = False
        self.held_keys: tuple[str, ...] = ()
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.005

    def set_target_window(self, hwnd: int | None) -> None:
        if hwnd == self.target_hwnd:
            return
        self.release_all()
        self.target_hwnd = hwnd

    def set_background_input(self, enabled: bool) -> None:
        enabled = bool(enabled and self.target_hwnd)
        if enabled == self.background_input:
            return
        # Release through the old backend before changing where future key
        # messages are sent.
        self.release_all()
        self.background_input = enabled

    def _key_down(self, key: str) -> None:
        if self.background_input and self.target_hwnd is not None:
            post_window_key(self.target_hwnd, key, True)
        else:
            pyautogui.keyDown(key)

    def _key_up(self, key: str) -> None:
        if self.background_input and self.target_hwnd is not None:
            post_window_key(self.target_hwnd, key, False)
        else:
            pyautogui.keyUp(key)

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
            self._key_down(key)
        try:
            time.sleep(action.duration)
        finally:
            for key in reversed(action.keys):
                self._key_up(key)

    def _hold(self, keys: tuple[str, ...]) -> None:
        for key in reversed(self.held_keys):
            if key not in keys:
                self._key_up(key)
        for key in keys:
            if key not in self.held_keys:
                self._key_down(key)
        self.held_keys = keys

    def release_all(self) -> None:
        if not self.held_keys:
            return
        keys = self.held_keys
        self.held_keys = ()
        if not self.live:
            return
        if self.background_input:
            for key in reversed(keys):
                self._key_up(key)
            return
        # Foreground cleanup must still work after the mouse-triggered
        # emergency stop.
        failsafe = pyautogui.FAILSAFE
        try:
            pyautogui.FAILSAFE = False
            for key in reversed(keys):
                self._key_up(key)
        finally:
            pyautogui.FAILSAFE = failsafe
