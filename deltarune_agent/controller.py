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
        # messages are sent. If release fails, keep the old backend selected so
        # a later cleanup can retry the correct destination.
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

        pressed: list[str] = []
        action_error: BaseException | None = None
        try:
            for key in action.keys:
                self._key_down(key)
                pressed.append(key)
            time.sleep(action.duration)
        except BaseException as exc:
            action_error = exc
            raise
        finally:
            release_error = self._release_temporary(pressed)
            if action_error is None and release_error is not None:
                raise release_error

    def _release_temporary(self, keys: list[str]) -> BaseException | None:
        first_error: BaseException | None = None
        for key in reversed(keys):
            try:
                self._key_up(key)
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        return first_error

    def _hold(self, keys: tuple[str, ...]) -> None:
        # Preserve order while preventing duplicate key transitions.
        requested = tuple(dict.fromkeys(keys))
        current = list(self.held_keys)

        # Release obsolete keys one at a time and update the tracked state only
        # after the real backend confirms the release.
        for key in tuple(reversed(current)):
            if key in requested:
                continue
            self._key_up(key)
            current.remove(key)
            self.held_keys = tuple(current)

        newly_pressed: list[str] = []
        try:
            for key in requested:
                if key in current:
                    continue
                self._key_down(key)
                current.append(key)
                newly_pressed.append(key)
                self.held_keys = tuple(current)
        except BaseException:
            # Roll back only keys pressed by this transition. Existing held keys
            # remain tracked and can still be released normally.
            for key in reversed(newly_pressed):
                try:
                    self._key_up(key)
                except BaseException:
                    continue
                if key in current:
                    current.remove(key)
            self.held_keys = tuple(current)
            raise

        self.held_keys = requested

    def release_all(self) -> None:
        if not self.held_keys:
            return
        if not self.live:
            self.held_keys = ()
            return

        # Foreground cleanup must still work after the mouse-triggered
        # emergency stop. Keep failed keys in held_keys so a later cleanup can
        # retry them instead of silently forgetting a potentially stuck key.
        failsafe = pyautogui.FAILSAFE
        first_error: BaseException | None = None
        remaining = list(self.held_keys)
        try:
            if not self.background_input:
                pyautogui.FAILSAFE = False
            for key in tuple(reversed(remaining)):
                try:
                    self._key_up(key)
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
                    continue
                remaining.remove(key)
                self.held_keys = tuple(remaining)
        finally:
            pyautogui.FAILSAFE = failsafe

        if first_error is not None:
            raise first_error
