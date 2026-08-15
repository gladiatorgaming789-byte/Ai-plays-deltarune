"""Adaptive capture recovery for usable-but-frozen Windows screenshots."""

from __future__ import annotations

from hashlib import blake2b

import pyautogui
from PIL import Image

from .observer import (
    Observation,
    ScreenObserver,
    capture_window_client_bitblt,
    frame_is_usable,
    user32,
)


REPEAT_BEFORE_ALTERNATE_PROBE = 8
ALTERNATE_PROBE_INTERVAL = 4
_INSTALLED = False
_ORIGINAL_OBSERVE = None
_ORIGINAL_DIAGNOSTICS = None


def _fingerprint(frame: Image.Image) -> bytes:
    sample = frame.convert("RGB").resize((48, 36), Image.Resampling.NEAREST)
    return blake2b(sample.tobytes(), digest_size=16).digest()


def _counter(observer: ScreenObserver, name: str) -> int:
    return int(getattr(observer, name, 0) or 0)


def _observe_adaptive(self: ScreenObserver, step: int) -> Observation:
    assert _ORIGINAL_OBSERVE is not None
    observation = _ORIGINAL_OBSERVE(self, step)
    if self.window_hwnd is None or not observation.visual_valid:
        return observation

    fingerprint = _fingerprint(observation.frame)
    previous = getattr(self, "_adaptive_last_fingerprint", None)
    if previous == fingerprint:
        repeats = _counter(self, "_adaptive_repeat_frames") + 1
    else:
        repeats = 0
    self._adaptive_last_fingerprint = fingerprint
    self._adaptive_repeat_frames = repeats

    if repeats < REPEAT_BEFORE_ALTERNATE_PROBE:
        return observation

    last_probe = int(getattr(self, "_adaptive_last_probe_step", -10_000))
    if step - last_probe < ALTERNATE_PROBE_INTERVAL:
        return observation
    self._adaptive_last_probe_step = step
    self._adaptive_capture_probes = _counter(self, "_adaptive_capture_probes") + 1

    try:
        if user32.GetForegroundWindow() == self.window_hwnd:
            alternate = pyautogui.screenshot(region=self.region)
            method = "desktop"
        else:
            alternate = capture_window_client_bitblt(self.window_hwnd)
            method = "bitblt"
    except (OSError, pyautogui.PyAutoGUIException):
        self._adaptive_capture_errors = _counter(
            self,
            "_adaptive_capture_errors",
        ) + 1
        return observation

    if not frame_is_usable(alternate):
        self._adaptive_capture_unusable = _counter(
            self,
            "_adaptive_capture_unusable",
        ) + 1
        return observation

    alternate_fingerprint = _fingerprint(alternate)
    if alternate_fingerprint == fingerprint:
        self._adaptive_same_frame_probes = _counter(
            self,
            "_adaptive_same_frame_probes",
        ) + 1
        return observation

    # This replaces the current observation rather than creating an extra one,
    # so the normal valid-frame total remains one frame for this step.
    self._last_window_frame = alternate
    self._adaptive_capture_recoveries = _counter(
        self,
        "_adaptive_capture_recoveries",
    ) + 1
    self._adaptive_last_fingerprint = alternate_fingerprint
    self._adaptive_repeat_frames = 0
    self._adaptive_last_recovery_method = method
    return Observation(frame=alternate, step=step, visual_valid=True)


def _diagnostics_adaptive(self: ScreenObserver) -> dict[str, object]:
    assert _ORIGINAL_DIAGNOSTICS is not None
    result = dict(_ORIGINAL_DIAGNOSTICS(self))
    result.update(
        {
            "adaptive_capture_enabled": True,
            "adaptive_capture_probes": _counter(self, "_adaptive_capture_probes"),
            "adaptive_capture_recoveries": _counter(
                self,
                "_adaptive_capture_recoveries",
            ),
            "adaptive_capture_errors": _counter(self, "_adaptive_capture_errors"),
            "adaptive_capture_unusable": _counter(
                self,
                "_adaptive_capture_unusable",
            ),
            "adaptive_same_frame_probes": _counter(
                self,
                "_adaptive_same_frame_probes",
            ),
            "adaptive_last_recovery_method": getattr(
                self,
                "_adaptive_last_recovery_method",
                None,
            ),
        }
    )
    return result


def install_adaptive_capture_recovery() -> None:
    global _INSTALLED, _ORIGINAL_OBSERVE, _ORIGINAL_DIAGNOSTICS
    if _INSTALLED:
        return
    _ORIGINAL_OBSERVE = ScreenObserver.observe
    _ORIGINAL_DIAGNOSTICS = ScreenObserver.diagnostics
    ScreenObserver.observe = _observe_adaptive  # type: ignore[method-assign]
    ScreenObserver.diagnostics = _diagnostics_adaptive  # type: ignore[method-assign]
    _INSTALLED = True


__all__ = ["install_adaptive_capture_recovery"]
