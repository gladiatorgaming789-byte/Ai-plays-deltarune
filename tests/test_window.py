from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from deltarune_agent import window as window_module
from deltarune_agent.window import WindowInfo, load_known_windows, remember_window


def test_detected_window_title_and_executable_are_persisted():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "window_titles.json"
        window = WindowInfo(123, "DELTARUNE Chapter 1", "DELTARUNE.exe")

        remember_window(path, window)
        remember_window(path, window)
        records = load_known_windows(path)

    assert len(records) == 1
    assert records[0]["title"] == "DELTARUNE Chapter 1"
    assert records[0]["executable"] == "DELTARUNE.exe"
    assert records[0]["seen_count"] == 2


def test_malformed_window_memory_is_ignored():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "window_titles.json"
        path.write_text("not json", encoding="utf-8")

        assert load_known_windows(path) == []


def test_game_executable_beats_controller_gui_title_match():
    controller = WindowInfo(100, "Deltarune AI Controller", "python.exe")
    game = WindowInfo(200, "SURVEY_PROGRAM", "DELTARUNE.exe")

    with patch.object(window_module, "visible_windows", return_value=[controller, game]):
        match = window_module.find_window("deltarune")

    assert match == game


def test_known_old_title_finds_same_executable_after_title_changes():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "window_titles.json"
        remember_window(path, WindowInfo(100, "OLD_SURVEY_TITLE", "DELTARUNE.exe"))
        current = WindowInfo(200, "NEW_CHAPTER_TITLE", "DELTARUNE.exe")

        with patch.object(window_module, "visible_windows", return_value=[current]):
            match = window_module.find_window("OLD_SURVEY_TITLE", path)

    assert match == current
