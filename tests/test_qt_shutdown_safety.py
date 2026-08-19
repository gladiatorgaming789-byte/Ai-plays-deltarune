from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from deltarune_agent.qt_ui import QT_AVAILABLE

pytestmark = pytest.mark.skipif(not QT_AVAILABLE, reason="PySide6 is optional")

if QT_AVAILABLE:
    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QMessageBox

    from deltarune_agent.qt_ui.shutdown_safety import install_shutdown_safety


class _Signal:
    def __init__(self) -> None:
        self.values: list[object] = []

    def emit(self, value: object) -> None:
        self.values.append(value)


class _Event:
    def __init__(self) -> None:
        self.ignored = 0
        self.accepted = 0

    def ignore(self) -> None:
        self.ignored += 1

    def accept(self) -> None:
        self.accepted += 1


class _Logs:
    def __init__(self) -> None:
        self.rows: list[tuple[object, ...]] = []

    def append(self, *values: object) -> None:
        self.rows.append(values)


def _types(tmp_path: Path):
    class Controller:
        def __init__(self) -> None:
            self.running = True
            self.stop_file = tmp_path / "stop.flag"
            self.stop_file.write_text("stop\n", encoding="utf-8")
            self.requested = 0
            self.forced = 0
            self.stateChanged = _Signal()
            self.finished = _Signal()
            self.outputReceived = _Signal()

        def request_stop(self) -> None:
            self.requested += 1

        def force_stop(self) -> None:
            self.forced += 1

        def _process_error(self, _error) -> None:
            self.outputReceived.emit("original error handler")

    class Window:
        def __init__(self) -> None:
            self.controller = Controller()
            self._closing_after_stop = False
            self.logs_page = _Logs()
            self.original_close_calls = 0

        def closeEvent(self, event) -> None:
            self.original_close_calls += 1
            event.accept()

        def close(self) -> None:
            pass

    return Window, Controller


def test_close_requests_safe_stop_without_automatic_kill(tmp_path: Path) -> None:
    Window, Controller = _types(tmp_path)
    install_shutdown_safety(Window, Controller)
    window = Window()
    event = _Event()
    timers: list[object] = []

    with patch(
        "deltarune_agent.qt_ui.shutdown_safety.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    ), patch(
        "deltarune_agent.qt_ui.shutdown_safety.QMessageBox.warning",
        return_value=QMessageBox.StandardButton.No,
    ), patch(
        "deltarune_agent.qt_ui.shutdown_safety.QTimer.singleShot",
        side_effect=lambda _delay, callback: timers.append(callback),
    ):
        window.closeEvent(event)
        assert window.controller.requested == 1
        assert window.controller.forced == 0
        assert window._closing_after_stop is True
        assert event.ignored == 1
        assert window.original_close_calls == 0
        assert len(timers) == 1

        # Expiring the grace period offers an emergency choice; declining it
        # keeps waiting and must not kill the controller.
        timers.pop(0)()
        assert window.controller.forced == 0
        assert len(timers) == 1


def test_second_close_requires_explicit_force_confirmation(tmp_path: Path) -> None:
    Window, Controller = _types(tmp_path)
    install_shutdown_safety(Window, Controller)
    window = Window()
    window._closing_after_stop = True
    event = _Event()

    with patch(
        "deltarune_agent.qt_ui.shutdown_safety.QMessageBox.warning",
        return_value=QMessageBox.StandardButton.Yes,
    ):
        window.closeEvent(event)

    assert window.controller.forced == 1
    assert event.ignored == 1
    assert window.original_close_calls == 0


def test_failed_to_start_transitions_controller_out_of_starting(tmp_path: Path) -> None:
    Window, Controller = _types(tmp_path)
    install_shutdown_safety(Window, Controller)
    controller = Controller()

    controller._process_error(QProcess.ProcessError.FailedToStart)

    assert controller.stateChanged.values[-1] == "error"
    assert controller.finished.values[-1] == -1
    assert controller.stop_file is None
