from unittest.mock import patch

from PIL import Image

from deltarune_agent.observer import ScreenObserver, frame_is_usable


def test_window_capture_is_used_instead_of_visible_desktop_pixels():
    expected = Image.new("RGB", (320, 240), (20, 40, 60))
    observer = ScreenObserver((10, 20, 320, 240))
    observer.set_window(456, (10, 20, 320, 240))

    with patch(
        "deltarune_agent.observer.capture_window_client",
        return_value=expected,
    ), patch("deltarune_agent.observer.pyautogui.screenshot") as desktop_capture:
        observation = observer.observe(7)

    assert observation.frame is expected
    assert observation.step == 7
    assert observation.visual_valid
    desktop_capture.assert_not_called()
    diagnostics = observer.diagnostics()
    assert diagnostics["print_window_successes"] == 1
    assert diagnostics["valid_frames"] == 1


def test_failed_background_printwindow_uses_bitblt_before_stale_frame():
    previous = Image.new("RGB", (320, 240), (20, 40, 60))
    recovered = Image.new("RGB", (320, 240), (80, 100, 120))
    observer = ScreenObserver((10, 20, 320, 240))
    observer.set_window(456, (10, 20, 320, 240))
    observer._last_window_frame = previous

    with patch(
        "deltarune_agent.observer.capture_window_client",
        side_effect=OSError("capture failed"),
    ), patch(
        "deltarune_agent.observer.capture_window_client_bitblt",
        return_value=recovered,
    ) as bitblt_capture, patch(
        "deltarune_agent.observer.user32.GetForegroundWindow",
        return_value=999,
    ), patch("deltarune_agent.observer.pyautogui.screenshot") as desktop_capture:
        observation = observer.observe(8)

    assert observation.frame is recovered
    assert observation.visual_valid
    bitblt_capture.assert_called_once_with(456)
    desktop_capture.assert_not_called()
    diagnostics = observer.diagnostics()
    assert diagnostics["print_window_errors"] == 1
    assert diagnostics["bitblt_successes"] == 1
    assert diagnostics["stale_frame_reuses"] == 0


def test_failed_background_capture_reuses_last_clean_game_frame_after_both_methods_fail():
    expected = Image.new("RGB", (320, 240), (20, 40, 60))
    observer = ScreenObserver((10, 20, 320, 240))
    observer.set_window(456, (10, 20, 320, 240))
    observer._last_window_frame = expected

    with patch(
        "deltarune_agent.observer.capture_window_client",
        side_effect=OSError("capture failed"),
    ), patch(
        "deltarune_agent.observer.capture_window_client_bitblt",
        side_effect=OSError("bitblt failed"),
    ), patch(
        "deltarune_agent.observer.user32.GetForegroundWindow",
        return_value=999,
    ), patch("deltarune_agent.observer.pyautogui.screenshot") as desktop_capture:
        observation = observer.observe(8)

    assert observation.frame is not expected
    assert observation.frame.getpixel((0, 0)) == (20, 40, 60)
    assert not observation.visual_valid
    desktop_capture.assert_not_called()
    diagnostics = observer.diagnostics()
    assert diagnostics["print_window_errors"] == 1
    assert diagnostics["bitblt_errors"] == 1
    assert diagnostics["stale_frame_reuses"] == 1
    assert diagnostics["invalid_frames"] == 1


def test_blank_printwindow_frame_falls_back_to_visible_game_capture():
    blank = Image.new("RGB", (320, 240), "white")
    visible = Image.new("RGB", (320, 240), (20, 40, 60))
    observer = ScreenObserver((10, 20, 320, 240))
    observer.set_window(456, (10, 20, 320, 240))

    with patch(
        "deltarune_agent.observer.capture_window_client",
        return_value=blank,
    ), patch(
        "deltarune_agent.observer.user32.GetForegroundWindow",
        return_value=456,
    ), patch(
        "deltarune_agent.observer.pyautogui.screenshot",
        return_value=visible,
    ):
        observation = observer.observe(9)

    assert observation.visual_valid
    assert observation.frame is visible
    assert not frame_is_usable(blank)
    diagnostics = observer.diagnostics()
    assert diagnostics["print_window_unusable"] == 1
    assert diagnostics["desktop_successes"] == 1


def test_blank_background_capture_never_becomes_map_evidence_when_bitblt_is_blank():
    blank = Image.new("RGB", (320, 240), "white")
    observer = ScreenObserver((10, 20, 320, 240))
    observer.set_window(456, (10, 20, 320, 240))

    with patch(
        "deltarune_agent.observer.capture_window_client",
        return_value=blank,
    ), patch(
        "deltarune_agent.observer.capture_window_client_bitblt",
        return_value=blank,
    ), patch(
        "deltarune_agent.observer.user32.GetForegroundWindow",
        return_value=999,
    ):
        observation = observer.observe(10)

    assert not observation.visual_valid
    assert observation.frame.getpixel((0, 0)) == (0, 0, 0)
    diagnostics = observer.diagnostics()
    assert diagnostics["print_window_unusable"] == 1
    assert diagnostics["bitblt_unusable"] == 1
    assert diagnostics["blank_fallbacks"] == 1


def test_black_printwindow_frame_reuses_last_clean_game_frame_if_bitblt_is_unusable():
    blank = Image.new("RGB", (320, 240), "black")
    previous = Image.new("RGB", (320, 240), (20, 40, 60))
    observer = ScreenObserver((10, 20, 320, 240))
    observer.set_window(456, (10, 20, 320, 240))
    observer._last_window_frame = previous

    with patch(
        "deltarune_agent.observer.capture_window_client",
        return_value=blank,
    ), patch(
        "deltarune_agent.observer.capture_window_client_bitblt",
        return_value=blank,
    ), patch(
        "deltarune_agent.observer.user32.GetForegroundWindow",
        return_value=999,
    ):
        observation = observer.observe(11)

    assert not frame_is_usable(blank)
    assert not observation.visual_valid
    assert observation.frame.getpixel((0, 0)) == (20, 40, 60)
    diagnostics = observer.diagnostics()
    assert diagnostics["stale_frame_reuses"] == 1
    assert diagnostics["visual_valid_ratio"] == 0.0
