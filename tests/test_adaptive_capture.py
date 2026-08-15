from __future__ import annotations

from types import SimpleNamespace

from PIL import Image

import deltarune_agent.adaptive_capture as adaptive
from deltarune_agent.observer import Observation


def test_repeated_usable_primary_frame_can_recover_from_alternate_backend(monkeypatch) -> None:
    primary = Image.new("RGB", (64, 48), (20, 30, 40))
    alternate = Image.new("RGB", (64, 48), (80, 90, 100))
    observer = SimpleNamespace(
        window_hwnd=123,
        region=(0, 0, 64, 48),
        _last_window_frame=primary,
    )

    monkeypatch.setattr(
        adaptive,
        "_ORIGINAL_OBSERVE",
        lambda self, step: Observation(primary.copy(), step, True),
    )
    monkeypatch.setattr(
        adaptive,
        "user32",
        SimpleNamespace(GetForegroundWindow=lambda: 999),
    )
    monkeypatch.setattr(
        adaptive,
        "capture_window_client_bitblt",
        lambda hwnd: alternate.copy(),
    )
    monkeypatch.setattr(adaptive, "frame_is_usable", lambda frame: True)

    result = None
    for step in range(adaptive.REPEAT_BEFORE_ALTERNATE_PROBE + 1):
        result = adaptive._observe_adaptive(observer, step)

    assert result is not None
    assert result.frame.getpixel((0, 0)) == (80, 90, 100)
    assert observer._adaptive_capture_recoveries == 1
    assert observer._adaptive_last_recovery_method == "bitblt"


def test_static_repeat_does_not_claim_recovery_when_alternate_matches(monkeypatch) -> None:
    primary = Image.new("RGB", (64, 48), (20, 30, 40))
    observer = SimpleNamespace(
        window_hwnd=123,
        region=(0, 0, 64, 48),
        _last_window_frame=primary,
    )

    monkeypatch.setattr(
        adaptive,
        "_ORIGINAL_OBSERVE",
        lambda self, step: Observation(primary.copy(), step, True),
    )
    monkeypatch.setattr(
        adaptive,
        "user32",
        SimpleNamespace(GetForegroundWindow=lambda: 999),
    )
    monkeypatch.setattr(
        adaptive,
        "capture_window_client_bitblt",
        lambda hwnd: primary.copy(),
    )
    monkeypatch.setattr(adaptive, "frame_is_usable", lambda frame: True)

    for step in range(adaptive.REPEAT_BEFORE_ALTERNATE_PROBE + 1):
        result = adaptive._observe_adaptive(observer, step)

    assert result.frame.getpixel((0, 0)) == (20, 30, 40)
    assert getattr(observer, "_adaptive_capture_recoveries", 0) == 0
    assert observer._adaptive_same_frame_probes == 1
