from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from deltarune_agent.qt_ui import QT_AVAILABLE

pytestmark = pytest.mark.skipif(not QT_AVAILABLE, reason="PySide6 is optional")

if QT_AVAILABLE:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap
    from PySide6.QtTest import QSignalSpy, QTest
    from PySide6.QtWidgets import QApplication, QGraphicsPixmapItem, QMessageBox

    from deltarune_agent.gui import WallMapModel
    from deltarune_agent.qt_ui.app import OperatorWindow
    from deltarune_agent.qt_ui.background import BackgroundLayer, bundled_background
    from deltarune_agent.qt_ui.controller import RunController
    from deltarune_agent.qt_ui.map_view import CELL_PIXELS, RoomMapView
    from deltarune_agent.qt_ui.pages import LiveMapPage, RunsPage
    from deltarune_agent.qt_ui.themes import BUILTIN_THEMES, BackgroundSettings
    from deltarune_agent.run19_profiles import ProfileStore
    from deltarune_agent.world_model import CELL_SIZE, EXPLORATION_REGION_CELLS


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_qt_controller_preserves_gui_event_protocol(qapp, tmp_path: Path) -> None:
    controller = RunController(tmp_path)
    events: list[object] = []
    output: list[str] = []
    controller.eventReceived.connect(events.append)
    controller.outputReceived.connect(output.append)
    controller._handle_line('AI_GUI_EVENT\t{"step":12,"state":"overworld"}')
    controller._handle_line("plain output")
    assert events == [{"step": 12, "state": "overworld"}]
    assert output == ["plain output"]


def test_map_scene_uses_same_scale_for_tile_guess_and_player(qapp, tmp_path: Path) -> None:
    tile_path = tmp_path / "tile.png"
    pixmap = QPixmap(160, 160)
    pixmap.fill(Qt.GlobalColor.darkYellow)
    assert pixmap.save(str(tile_path))

    model = WallMapModel()
    room = model.room("room_test")
    room.cells.update({(0, 0), (20, 20)})
    room.view_tiles[(0, 0)] = {"path": str(tile_path), "mtime_ns": 1}
    room.screen_regions[(0, 0)] = {
        "hypothesis": "possible_exit",
        "guess_state": "proposed",
        "guess_label": "Possible opening at right edge",
        "guess_confidence": 0.7,
        "interest": 0.7,
        "views": 2,
        "edge_hint": "right",
        "feature_box_world": [80, 48, 96, 80],
        "anchor_cell": [11, 8],
    }
    room.warps[(20, 10, "room_next")] = {"role": "progression", "count": 1}
    model.current_room = "room_test"
    model.current_display_position = (CELL_SIZE * 5.0, CELL_SIZE * 6.0)
    model.current_camera = ("room_test", 0, 0, 320, 240)

    view = RoomMapView()
    view.resize(700, 500)
    view.set_model(model)
    view.set_palette(BUILTIN_THEMES["operator"].map_colors)
    view.set_room("room_test", fit=True)
    qapp.processEvents()

    tile = next(item for item in view.scene().items() if isinstance(item, QGraphicsPixmapItem))
    expected = EXPLORATION_REGION_CELLS * CELL_PIXELS
    assert tile.sceneBoundingRect().width() == pytest.approx(expected)
    guess = view.map_scene.guess_items[next(iter(view.map_scene.guess_items))][0]
    expected_left = 80 / CELL_SIZE * CELL_PIXELS
    assert guess.sceneBoundingRect().left() == pytest.approx(expected_left, abs=1)
    player = next(item for item in view.scene().items() if item.data(0) and item.data(0).get("kind") == "player")
    assert player.sceneBoundingRect().center().x() == pytest.approx(5 * CELL_PIXELS, abs=1)
    assert len(view.map_scene._pixmap_cache) == 1
    view.refresh(preserve_view=True)
    assert len(view.map_scene._pixmap_cache) == 1


def test_middle_and_right_buttons_enter_manual_pan_mode(qapp) -> None:
    view = RoomMapView()
    view.resize(300, 200)
    view.show()
    qapp.processEvents()
    for button in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
        QTest.mousePress(view.viewport(), button, pos=view.viewport().rect().center())
        assert view._pan_active is True
        QTest.mouseRelease(view.viewport(), button, pos=view.viewport().rect().center())
        assert view._pan_active is False


def test_background_finds_bundled_user_supplied_assets(qapp) -> None:
    assert bundled_background("epic_wallpaper.mp4") is not None
    assert bundled_background("second_wallpaper.gif") is not None
    layer = BackgroundLayer()
    layer.set_theme(BUILTIN_THEMES["hometown_sunset"])
    layer.set_settings(BackgroundSettings(animation=False, reduce_motion=True))
    assert layer.source_path is not None
    assert layer.source_path.name == "second_wallpaper.gif"
    assert layer._movie is not None


def test_operator_window_has_all_pages_and_switches_theme(qapp, tmp_path: Path) -> None:
    store = ProfileStore(tmp_path / "profile-data")
    window = OperatorWindow(project_root=tmp_path, store=store)
    window.show()
    qapp.processEvents()
    assert window.pages.count() == 6
    assert set(window.page_indexes) == {"map", "runs", "profiles", "learning", "logs", "settings"}
    window.select_page("settings")
    assert window.pages.currentIndex() == window.page_indexes["settings"]
    window.apply_appearance("cyber_city", BackgroundSettings(reduce_motion=True), persist=False)
    assert window.theme_id == "cyber_city"
    window.close()


def test_custom_theme_import_is_immediately_available_to_window(qapp, tmp_path: Path) -> None:
    store = ProfileStore(tmp_path / "profile-data")
    window = OperatorWindow(project_root=tmp_path, store=store)
    payload = BUILTIN_THEMES["operator"].to_manifest()
    payload["id"] = "imported_theme"
    payload["name"] = "Imported Theme"
    manifest = tmp_path / "incoming.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    theme = window.settings_page.install_theme(manifest)
    qapp.processEvents()
    assert theme.id in window.themes
    assert window.theme_id == theme.id
    assert (store.root / "themes" / "imported_theme.json").is_file()
    window.close()


def test_room_summary_uses_shared_exploration_region_size(qapp, tmp_path: Path) -> None:
    page = LiveMapPage(tmp_path)
    page.model = WallMapModel()
    room = page.model.room("room_regions")
    room.cells.update({(0, 0), (EXPLORATION_REGION_CELLS, 0)})
    page._update_room_summary("room_regions")
    assert "2 regions" in page.map_stats.text()


def test_live_map_retains_legend_and_separate_memory_actions(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    memory = tmp_path / "memory"
    views = memory / "room_views"
    views.mkdir(parents=True)
    navigation = memory / "navigation.json"
    navigation.write_text(
        json.dumps({"version": 3, "cell_size": 8, "cells": [], "screen_regions": []}),
        encoding="utf-8",
    )
    (views / "index.json").write_text(
        json.dumps({"version": 3, "rooms": {}}), encoding="utf-8"
    )
    page = LiveMapPage(tmp_path)
    page.set_map_palette(BUILTIN_THEMES["operator"].map_colors)
    assert page.inspector_tabs.count() == 4
    assert "progression evidence" in page.legend_view.toPlainText()
    assert len(page.map_data_button.menu().actions()) == 2

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    page._clear_room_views()
    assert navigation.is_file()
    assert not views.exists()
    page._clear_learned_map()
    assert not navigation.exists()


def test_live_map_coalesces_high_rate_scene_rebuilds(qapp, tmp_path: Path) -> None:
    page = LiveMapPage(tmp_path)
    page.model.room("room_test").cells.add((0, 0))
    calls: list[str] = []
    original = page.map_view.set_room

    def counted(room: str, *, fit: bool = False) -> None:
        calls.append(room)
        original(room, fit=fit)

    page.map_view.set_room = counted  # type: ignore[method-assign]
    refreshes = QSignalSpy(page._map_refresh_timer.timeout)
    for step in range(20):
        page.handle_event({"step": step, "state": "unknown", "reason": "unknown state", "action": "wait"})
    assert calls == []
    assert refreshes.wait(250)
    assert calls == ["room_test"]
    assert refreshes.count() == 1
    assert not page._map_refresh_timer.isActive()


def test_runs_page_autonomy_workbench_shows_selected_goal(qapp, tmp_path: Path) -> None:
    page = RunsPage(tmp_path)
    tabs = [page.tabs.tabText(index) for index in range(page.tabs.count())]
    assert "Autonomy" in tabs
    page._selected_path = str(tmp_path / "run")
    prediction = {
        "step": 9,
        "prediction_snapshot": {
            "room": "room_test",
            "autonomy": {
                "version": 1,
                "recovery_level": "evidence",
                "recovery_reason": "story progress stalled",
                "active_goal_id": "entity:E1",
                "active_goal_kind": "semantic_entity",
                "active_goal_age": 2,
                "selected_option_id": "entity:E1",
                "commitment_hold": True,
                "ranked_options": [
                    {
                        "id": "entity:E1",
                        "kind": "semantic_entity",
                        "required_level": "evidence",
                        "base_score": 6.0,
                        "score": 7.5,
                        "confidence": 0.7,
                        "information_value": 0.5,
                        "novelty": 0.4,
                        "distance": 2,
                        "loop_risk": 0.0,
                        "failure_cost": 0.1,
                        "budget_spent": 1,
                        "budget_limit": 4,
                        "budget_remaining": 3,
                        "selected": True,
                    }
                ],
            },
        },
    }

    page._artifact_loaded(str(tmp_path / "run"), [], [(0, prediction)])

    assert "entity:E1" in page.autonomy_summary.toPlainText()
    assert "held the active goal" in page.autonomy_summary.toPlainText()
    assert page.autonomy_options.rowCount() == 1
    assert page.autonomy_options.item(0, 1).text() == "Selected"
    assert page.autonomy_options.item(0, 3).text().startswith("semantic_entity")
