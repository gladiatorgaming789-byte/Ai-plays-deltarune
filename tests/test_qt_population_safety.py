from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from deltarune_agent.qt_ui import QT_AVAILABLE

pytestmark = pytest.mark.skipif(not QT_AVAILABLE, reason="PySide6 is optional")

if QT_AVAILABLE:
    from PySide6.QtWidgets import QApplication, QMessageBox

    from deltarune_agent.qt_ui.pages import TrainingPage
    from deltarune_agent.qt_ui.population_safety import install_population_safety


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _TextMode:
    def __init__(self, text: str) -> None:
        self.text = text

    def currentText(self) -> str:
        return self.text


class _Sink:
    def __init__(self) -> None:
        self.values: list[object] = []

    def handle_event(self, value: object) -> None:
        self.values.append(value)

    def append(self, *value: object) -> None:
        self.values.append(value)

    def setText(self, value: str) -> None:
        self.values.append(value)


class _TrainingStub:
    def _build(self) -> None:
        pass


class _OperatorStub:
    def __init__(self) -> None:
        self.run_mode = _TextMode("Population training")
        self.controller = SimpleNamespace(running=True)
        self.live_page = _Sink()
        self.training_page = _Sink()
        self.logs_page = _Sink()
        self.speed_status = _Sink()
        self.original_events: list[object] = []
        self.speed_calls: list[object] = []

    def _controller_event(self, payload: object) -> None:
        self.original_events.append(payload)
        self.live_page.handle_event(payload)
        self.training_page.handle_event(payload)

    def _controller_state(self, _state: str) -> None:
        pass

    def send_speed_key(self, key: str) -> None:
        self.speed_calls.append(key)

    def apply_selected_game_speed(self) -> None:
        self.speed_calls.append("apply")

    def findChildren(self, _kind):
        return []


def test_candidate_event_never_enters_shared_live_map() -> None:
    class Operator(_OperatorStub):
        pass

    class Training(_TrainingStub):
        pass

    install_population_safety(Operator, Training)
    window = Operator()
    payload = {
        "instance": {"id": "ai-1", "label": "AI 1"},
        "action": "right",
        "reason": "explore",
    }
    window._controller_event(payload)

    assert window.original_events == []
    assert window.live_page.values == []
    assert window.training_page.values == [payload]


def test_population_speed_hotkey_is_blocked_while_running() -> None:
    class Operator(_OperatorStub):
        pass

    class Training(_TrainingStub):
        pass

    install_population_safety(Operator, Training)
    window = Operator()
    with patch(
        "deltarune_agent.qt_ui.population_safety.QMessageBox.information",
        return_value=QMessageBox.StandardButton.Ok,
    ) as info:
        window.send_speed_key("f10")
        window.apply_selected_game_speed()

    assert window.speed_calls == []
    assert info.call_count == 2


def test_training_candidate_grid_is_scrollable(qapp, tmp_path: Path) -> None:
    class Training(TrainingPage):
        pass

    class Operator(_OperatorStub):
        pass

    install_population_safety(Operator, Training)
    page = Training(
        tmp_path / "runs",
        memory_path=lambda: tmp_path / "memory",
        can_promote=lambda: True,
    )
    qapp.processEvents()

    assert hasattr(page, "candidate_scroll")
    assert page.candidate_scroll.widgetResizable() is True
    assert page.candidate_grid.parentWidget() is page.candidate_scroll.widget()
