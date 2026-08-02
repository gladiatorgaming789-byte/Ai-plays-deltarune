from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from deltarune_agent.qt_ui import QT_AVAILABLE

pytestmark = pytest.mark.skipif(not QT_AVAILABLE, reason="PySide6 is optional")

if QT_AVAILABLE:
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QApplication

    from deltarune_agent.qt_ui.background import (
        BACKGROUND_MAX_FPS,
        PARALLAX_INTERVAL_MS,
        BackgroundLayer,
    )
    from deltarune_agent.qt_ui.themes import BUILTIN_THEMES, BackgroundSettings


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_background_frame_rate_is_deliberately_bounded() -> None:
    assert 1 <= BACKGROUND_MAX_FPS <= 20


def test_visual_only_setting_change_does_not_reload_media(qapp) -> None:
    layer = BackgroundLayer()
    layer.set_theme(BUILTIN_THEMES["hometown_sunset"])
    movie = layer._movie
    assert movie is not None

    layer.set_settings(
        BackgroundSettings(
            animation=False,
            reduce_motion=True,
            dim=0.75,
            mode="contain",
        )
    )

    assert layer._movie is movie


def test_changing_source_reloads_media(qapp, tmp_path: Path) -> None:
    image = tmp_path / "background.png"
    from PySide6.QtGui import QPixmap

    pixmap = QPixmap(16, 16)
    assert pixmap.save(str(image))

    layer = BackgroundLayer()
    layer.set_theme(BUILTIN_THEMES["hometown_sunset"])
    movie = layer._movie
    assert movie is not None

    layer.set_settings(
        BackgroundSettings(path=str(image), animation=False, reduce_motion=True)
    )

    assert layer._movie is None
    assert layer._loaded_source == image
    assert not layer._pixmap.isNull()


def test_parallax_updates_are_coalesced(qapp) -> None:
    layer = BackgroundLayer()
    layer.set_settings(BackgroundSettings(parallax=True, reduce_motion=False))

    layer.set_parallax_position(0.2, 0.2)
    first_pending = QPointF(layer._pending_parallax)
    layer.set_parallax_position(0.8, -0.5)

    assert layer._parallax == QPointF()
    assert layer._pending_parallax != first_pending
    assert layer._parallax_timer.interval() == PARALLAX_INTERVAL_MS
    assert layer._parallax_timer.isActive()

    layer._apply_pending_parallax()
    assert layer._parallax == layer._pending_parallax
