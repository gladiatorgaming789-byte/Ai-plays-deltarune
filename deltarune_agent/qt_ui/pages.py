"""Operator-console pages used by :mod:`deltarune_agent.qt_ui.app`."""

from __future__ import annotations

from collections import deque
from html import escape
import json
from pathlib import Path
import shutil
from typing import Callable, Mapping

from PySide6.QtCore import QObject, QPointF, QRect, QRunnable, QSize, QThreadPool, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QListView,
    QMessageBox,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..build_status import BuildStatus
from ..gui import (
    WallMapModel,
    decision_parts,
    format_ai_decision,
    format_speed_status,
    format_telemetry_event,
)
from ..map_guesses import VisualGuessEntry
from ..reinforcement import (
    CUSTOM_PRESET,
    PRESETS,
    REINFORCEMENT_MEMORY_FILENAME,
    REINFORCEMENT_SETTINGS_FILENAME,
    REWARD_FIELD_SPECS,
    RewardSettings,
    load_reward_settings,
    save_reward_settings,
)
from ..run19_profiles import Profile, ProfileStore
from ..training_workspace import promote_training_run
from ..world_model import EXPLORATION_REGION_CELLS
from .artifacts import (
    AutonomyWorkbenchSummary,
    RunSummary,
    scan_runs,
    summarize_autonomy_predictions,
    tail_jsonl,
)
from .map_view import RoomMapView
from .themes import BackgroundSettings, Theme, load_theme_file


def _layout_margins(layout, amount: int = 16) -> None:
    layout.setContentsMargins(amount, amount, amount, amount)
    layout.setSpacing(10)


class Card(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("card")


class WrappingListWidget(QListWidget):
    resized = Signal()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self.resized.emit()


class PageHeader(QWidget):
    def __init__(self, title: str, subtitle: str, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 0, 2, 6)
        layout.setSpacing(2)
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)
        detail = QLabel(subtitle)
        detail.setObjectName("muted")
        detail.setWordWrap(True)
        layout.addWidget(detail)


class GoalRoutePreview(QWidget):
    """Compact saved-route overlay for the Autonomy Workbench."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(118)
        self.setAccessibleName("Saved goal route overlay")
        self._route: list[tuple[int, int]] = []
        self._current: tuple[int, int] | None = None
        self._target: tuple[int, int] | None = None
        self._direction = ""

    @staticmethod
    def _cell(value: object) -> tuple[int, int] | None:
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            return None
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None

    def set_coherence(self, coherence: Mapping[str, object]) -> None:
        contract_value = coherence.get("goal_contract")
        contract = contract_value if isinstance(contract_value, Mapping) else {}
        route_value = contract.get("route_preview")
        self._route = (
            [cell for value in route_value if (cell := self._cell(value)) is not None]
            if isinstance(route_value, list)
            else []
        )
        self._current = self._cell(contract.get("current_cell"))
        self._target = self._cell(contract.get("target_cell"))
        self._direction = str(contract.get("planned_direction") or "")
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(4, 9, 17, 190))
        painter.setPen(QPen(QColor("#71819b"), 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

        points = list(self._route)
        if self._current is not None and self._current not in points:
            points.append(self._current)
        if self._target is not None and self._target not in points:
            points.append(self._target)
        if not points:
            painter.setPen(QColor("#9eabc0"))
            painter.drawText(
                self.rect().adjusted(14, 10, -14, -10),
                Qt.AlignmentFlag.AlignCenter,
                "No active positional goal. The next saved route will appear here.",
            )
            return

        min_x = min(point[0] for point in points)
        max_x = max(point[0] for point in points)
        min_y = min(point[1] for point in points)
        max_y = max(point[1] for point in points)
        usable_width = max(1.0, self.width() - 100.0)
        usable_height = max(1.0, self.height() - 34.0)
        scale = min(
            30.0,
            usable_width / max(1, max_x - min_x),
            usable_height / max(1, max_y - min_y),
        )
        offset_x = 24.0 + (usable_width - (max_x - min_x) * scale) / 2.0
        offset_y = 12.0 + (usable_height - (max_y - min_y) * scale) / 2.0

        def screen(cell: tuple[int, int]) -> QPointF:
            return QPointF(
                offset_x + (cell[0] - min_x) * scale,
                offset_y + (cell[1] - min_y) * scale,
            )

        if len(self._route) >= 2:
            painter.setPen(QPen(QColor("#46d4c6"), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for start, end in zip(self._route, self._route[1:]):
                painter.drawLine(screen(start), screen(end))
        for route_cell in self._route:
            center = screen(route_cell)
            painter.setPen(QPen(QColor("#9af4e9"), 1))
            painter.setBrush(QBrush(QColor("#163d42")))
            painter.drawEllipse(center, 3.5, 3.5)

        if self._target is not None:
            center = screen(self._target)
            painter.setPen(QPen(QColor("#ff85c8"), 2))
            painter.setBrush(QBrush(QColor("#7d285f")))
            painter.drawRect(
                int(center.x() - 6),
                int(center.y() - 6),
                12,
                12,
            )
        if self._current is not None:
            center = screen(self._current)
            painter.setPen(QPen(QColor("#9fc6ff"), 2))
            painter.setBrush(QBrush(QColor("#377dff")))
            painter.drawEllipse(center, 6.5, 6.5)
            vectors = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
            if self._direction in vectors:
                dx, dy = vectors[self._direction]
                painter.drawLine(
                    center,
                    QPointF(center.x() + dx * 15, center.y() + dy * 15),
                )

        painter.setPen(QColor("#c8d4e8"))
        painter.drawText(
            self.rect().adjusted(12, 0, -12, -7),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
            "Current ●   learned route ━   target ■",
        )


class LiveMapPage(QWidget):
    decisionLogged = Signal(str)
    telemetryLogged = Signal(str)
    runtimeLogged = Signal(str)

    def __init__(
        self,
        project_root: Path,
        parent=None,
        *,
        can_modify_memory: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__(parent)
        self.project_root = project_root
        self.can_modify_memory = can_modify_memory or (lambda: True)
        self.model = WallMapModel()
        self.follow_current = True
        self._guess_by_id: dict[str, VisualGuessEntry] = {}
        self._last_decision_signature: tuple[str, str, str] | None = None
        self._repeat_count = 0
        self._guess_signature: tuple[object, ...] | None = None
        self._known_rooms: tuple[str, ...] = ()
        self._map_refresh_timer = QTimer(self)
        self._map_refresh_timer.setSingleShot(True)
        self._map_refresh_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._map_refresh_timer.setInterval(34)
        self._map_refresh_timer.timeout.connect(self._flush_map_refresh)
        self._build()
        self.reload_memory()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        _layout_margins(root, 14)
        root.addWidget(
            PageHeader(
                "Live Map",
                "What the AI remembers, what it can currently see, and why it chose its next action.",
            )
        )
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Room"))
        self.room_combo = QComboBox()
        self.room_combo.setMinimumWidth(230)
        self.room_combo.currentTextChanged.connect(self._room_selected)
        toolbar.addWidget(self.room_combo)
        self.follow_box = QCheckBox("Follow current room")
        self.follow_box.setChecked(True)
        self.follow_box.toggled.connect(self._set_follow)
        toolbar.addWidget(self.follow_box)
        fit = QPushButton("Fit room")
        fit.clicked.connect(self.map_fit)
        toolbar.addWidget(fit)
        self.layers_button = QToolButton()
        self.layers_button.setText("Layers")
        self.layers_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._build_layer_menu()
        toolbar.addWidget(self.layers_button)
        self.map_data_button = QToolButton()
        self.map_data_button.setText("Map data")
        self.map_data_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        data_menu = QMenu(self.map_data_button)
        rebuild = data_menu.addAction("Rebuild remembered scene images")
        rebuild.triggered.connect(self._clear_room_views)
        clear_map = data_menu.addAction("Clear learned map and scene memory")
        clear_map.triggered.connect(self._clear_learned_map)
        self.map_data_button.setMenu(data_menu)
        toolbar.addWidget(self.map_data_button)
        toolbar.addStretch(1)
        self.map_stats = QLabel("Waiting for map data")
        self.map_stats.setObjectName("meta")
        toolbar.addWidget(self.map_stats)
        root.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.map_view = RoomMapView()
        self.map_view.itemSelected.connect(self._map_item_selected)
        self.map_view.guessesChanged.connect(self._guesses_changed)
        splitter.addWidget(self.map_view)

        inspector = QWidget()
        inspector.setMinimumWidth(330)
        inspector.setMaximumWidth(520)
        side = QVBoxLayout(inspector)
        side.setContentsMargins(8, 0, 0, 0)
        side.setSpacing(10)

        decision_card = Card()
        decision_layout = QVBoxLayout(decision_card)
        _layout_margins(decision_layout, 14)
        self.decision_category = QLabel("WAITING TO START")
        self.decision_category.setObjectName("sectionTitle")
        decision_layout.addWidget(self.decision_category)
        self.decision_action = QLabel("Start the AI to see its next action.")
        self.decision_action.setObjectName("success")
        decision_layout.addWidget(self.decision_action)
        self.decision_reason = QLabel(
            "The map remains usable while stopped, using the active profile's remembered scene."
        )
        self.decision_reason.setWordWrap(True)
        decision_layout.addWidget(self.decision_reason)
        self.decision_meta = QLabel("Room and position not reported")
        self.decision_meta.setObjectName("meta")
        self.decision_meta.setWordWrap(True)
        decision_layout.addWidget(self.decision_meta)
        side.addWidget(decision_card)

        self.inspector_tabs = QTabWidget()
        self.lead_list = WrappingListWidget()
        self.lead_list.setWordWrap(True)
        self.lead_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.lead_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lead_list.setSpacing(5)
        self.lead_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.lead_list.currentItemChanged.connect(self._guess_selected)
        self.lead_list.resized.connect(self._resize_guess_items)
        self.selection_view = QTextBrowser()
        self.selection_view.setOpenExternalLinks(False)
        self.selection_view.setHtml(
            "<h3>Nothing selected</h3><p>Click a lead, exit, interaction, wall, player, or map marker.</p>"
        )
        self.room_view = QTextBrowser()
        self.legend_view = QTextBrowser()
        self.inspector_tabs.addTab(self.lead_list, "AI leads")
        self.inspector_tabs.addTab(self.selection_view, "Selection")
        self.inspector_tabs.addTab(self.room_view, "Room")
        self.inspector_tabs.addTab(self.legend_view, "Map key")
        side.addWidget(self.inspector_tabs, 1)
        splitter.addWidget(inspector)
        splitter.setStretchFactor(0, 7)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([900, 370])
        root.addWidget(splitter, 1)

    def _build_layer_menu(self) -> None:
        menu = self.layers_button.menu()
        if menu is None:
            menu = QMenu(self.layers_button)
            self.layers_button.setMenu(menu)
        labels = {
            "scene": "Remembered scene",
            "navigation": "Walked paths and walls",
            "visits": "Visit heat",
            "grid": "8 px detail grid",
            "objects": "Learned interactions and exits",
            "guesses": "AI leads",
            "camera": "Current camera",
        }
        defaults = {
            "scene": True,
            "navigation": True,
            "visits": False,
            "grid": False,
            "objects": True,
            "guesses": True,
            "camera": True,
        }
        for key, label in labels.items():
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(defaults[key])
            action.toggled.connect(lambda checked, key=key: self.map_view.set_layer(key, checked))

    def map_fit(self) -> None:
        self.map_view.fit_to_room()

    def set_map_palette(self, palette: Mapping[str, str]) -> None:
        self.map_view.set_palette(palette)
        self._update_legend(palette)

    def _update_legend(self, palette: Mapping[str, str]) -> None:
        def swatch(key: str) -> str:
            color = palette.get(key, "#ffffff")
            return f'<span style="color:{color}; font-size:16px">■</span>'

        rows = [
            (f"{swatch('guess_exit')} E", "Unconfirmed exit or passage lead"),
            (f"{swatch('guess_character')} C", "Possible character-sized obstacle"),
            (f"{swatch('guess_object')} O", "Possible object to inspect"),
            (f"{swatch('interactable')} I", "Confirmed interaction"),
            (f"{swatch('path')} ━", "Walked path"),
            (f"{swatch('wall')} ━", "Learned wall"),
            (f"{swatch('camera')} □", "Current visible camera area"),
            (f"{swatch('player')} ●", "Kris and facing direction"),
            (f"{swatch('warp_progression')} P", "Exit with progression evidence"),
            (f"{swatch('warp_new_area')} N", "Exit to a newly observed area"),
            (f"{swatch('warp_optional')} O", "Likely optional exit"),
            (f"{swatch('warp_return')} R", "Observed return or backtrack"),
            (f"{swatch('warp_loop')} L", "Loop-suppressed exit"),
            (f"{swatch('warp')} ?", "Exit role not learned yet"),
        ]
        self.legend_view.setHtml(
            "<h3>Map key</h3><p>Every overlay uses the remembered scene's world coordinates.</p>"
            + "<table cellspacing='5'>"
            + "".join(f"<tr><td><b>{marker}</b></td><td>{meaning}</td></tr>" for marker, meaning in rows)
            + "</table>"
        )

    def reload_memory(self) -> None:
        self.model = WallMapModel()
        self.model.load_memory(self.project_root / "memory" / "navigation.json")
        self.model.load_room_views(self.project_root / "memory" / "room_views" / "index.json")
        self.map_view.set_model(self.model)
        # A profile/map reload may reuse the same lead IDs while changing their
        # bounds or evidence, so force the responsive lead list to repopulate.
        self._guess_signature = None
        self._known_rooms = ()
        self._refresh_rooms(select_current=False)
        self.map_view.set_room(self.room_combo.currentText(), fit=True)
        if self.room_combo.currentText():
            self._update_room_summary(self.room_combo.currentText())

    def _clear_learned_map(self) -> None:
        if not self.can_modify_memory():
            QMessageBox.information(self, "Stop AI first", "Stop the AI before clearing persistent map memory.")
            return
        if QMessageBox.question(
            self,
            "Clear learned map",
            "Delete all remembered room images, cells, paths, walls, interactions, AI leads, and exits for the active profile?",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            navigation = self.project_root / "memory" / "navigation.json"
            navigation.unlink(missing_ok=True)
            navigation.with_suffix(navigation.suffix + ".tmp").unlink(missing_ok=True)
            room_views = self.project_root / "memory" / "room_views"
            if room_views.exists():
                shutil.rmtree(room_views)
            self.map_view.map_scene._pixmap_cache.clear()
            self.reload_memory()
            self.runtimeLogged.emit("Learned map and remembered room views cleared for the active profile.")
        except OSError as exc:
            QMessageBox.critical(self, "Could not clear learned map", str(exc))

    def _clear_room_views(self) -> None:
        if not self.can_modify_memory():
            QMessageBox.information(self, "Stop AI first", "Stop the AI before rebuilding remembered scene images.")
            return
        if QMessageBox.question(
            self,
            "Rebuild scene images",
            "Delete only remembered room pictures? Learned paths, walls, interactions, leads, and exits will be kept.",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            room_views = self.project_root / "memory" / "room_views"
            if room_views.exists():
                shutil.rmtree(room_views)
            for room in self.model.rooms.values():
                room.view_tiles.clear()
            self.map_view.map_scene._pixmap_cache.clear()
            self.map_view.refresh(preserve_view=True)
            self.runtimeLogged.emit("Remembered scene images cleared; learned navigation was kept.")
        except OSError as exc:
            QMessageBox.critical(self, "Could not rebuild scene images", str(exc))

    def _refresh_rooms(self, *, select_current: bool) -> None:
        current = self.room_combo.currentText()
        rooms = tuple(sorted(self.model.rooms))
        preferred = (
            self.model.current_room
            if select_current and self.model.current_room in rooms
            else current if current in rooms else "room_krisroom" if "room_krisroom" in rooms else rooms[0] if rooms else ""
        )
        if rooms == self._known_rooms:
            if preferred and preferred != current:
                self.room_combo.blockSignals(True)
                self.room_combo.setCurrentText(preferred)
                self.room_combo.blockSignals(False)
            return
        self._known_rooms = rooms
        self.room_combo.blockSignals(True)
        self.room_combo.clear()
        self.room_combo.addItems(rooms)
        self.room_combo.setCurrentText(preferred or "")
        self.room_combo.blockSignals(False)

    def _set_follow(self, checked: bool) -> None:
        self.follow_current = checked
        if checked and self.model.current_room:
            self.room_combo.setCurrentText(self.model.current_room)

    def _room_selected(self, room: str) -> None:
        if room:
            self._schedule_map_refresh()

    def _schedule_map_refresh(self) -> None:
        # A 10x controller can publish several events in one paint interval.
        # Keep decision text live while collapsing expensive scene rebuilds.
        self._map_refresh_timer.start()

    def _flush_map_refresh(self) -> None:
        room = self.room_combo.currentText()
        if room:
            self.map_view.set_room(room)
            self._update_room_summary(room)

    def handle_event(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        if payload.get("kind") == "runtime_status":
            message = str(payload.get("message") or payload.get("status") or "Runtime status")
            self.runtimeLogged.emit(message)
            return
        self.model.update(payload)
        self._refresh_rooms(select_current=self.follow_current)
        self._schedule_map_refresh()
        self._show_decision(payload)
        decision = format_ai_decision(payload)
        telemetry = format_telemetry_event(payload)
        if decision:
            self.decisionLogged.emit(decision)
        if telemetry:
            self.telemetryLogged.emit(telemetry)

    def _show_decision(self, payload: dict[str, object]) -> None:
        category, action, explanation = decision_parts(payload)
        signature = category, action, explanation
        if signature == self._last_decision_signature:
            self._repeat_count += 1
        else:
            self._last_decision_signature = signature
            self._repeat_count = 1
        step = int(payload.get("step") or 0)
        repeat = f" · continuing {self._repeat_count} steps" if self._repeat_count > 1 else ""
        self.decision_category.setText(f"STEP {step:04d} · {category}{repeat}")
        self.decision_action.setText(action)
        self.decision_reason.setText(explanation)
        telemetry = payload.get("telemetry")
        if isinstance(telemetry, Mapping):
            room = str(telemetry.get("room_name") or telemetry.get("room_id") or self.model.current_room or "transition")
            x = telemetry.get("player_foot_x", telemetry.get("x"))
            y = telemetry.get("player_foot_y", telemetry.get("y"))
            direction = telemetry.get("player_facing_direction", telemetry.get("facing_direction"))
            self.decision_meta.setText(
                f"{room} · Kris {x if x is not None else '?'} , {y if y is not None else '?'} · facing {direction or '?'}"
            )
        else:
            self.decision_meta.setText(f"{self.model.current_room or 'Room not reported'} · telemetry unavailable")

    def _guesses_changed(self, guesses: object) -> None:
        entries = list(guesses) if isinstance(guesses, list) else []
        signature = tuple(
            (
                guess.stable_id,
                guess.marker,
                guess.label,
                round(guess.confidence, 4),
                guess.status,
                guess.evidence,
                guess.feature_box_world,
            )
            for guess in entries
        )
        if signature == self._guess_signature:
            return
        self._guess_signature = signature
        selected_id = ""
        selected = self.lead_list.currentItem()
        if selected is not None:
            selected_id = str(selected.data(Qt.ItemDataRole.UserRole) or "")
        self._guess_by_id = {guess.stable_id: guess for guess in entries}
        self.lead_list.blockSignals(True)
        self.lead_list.clear()
        for guess in entries:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, guess.stable_id)
            item.setText(
                f"{guess.marker}  {guess.label}\n"
                f"{guess.confidence:.0%} confidence · {guess.status}\n"
                f"{guess.evidence}"
            )
            item.setToolTip(
                f"Map anchor ({guess.anchor_cell[0]:g}, {guess.anchor_cell[1]:g})"
            )
            self.lead_list.addItem(item)
            if guess.stable_id == selected_id:
                self.lead_list.setCurrentItem(item)
        if not entries:
            empty = QListWidgetItem("No active leads in this room.")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.lead_list.addItem(empty)
        self.lead_list.blockSignals(False)
        self._resize_guess_items()

    def _resize_guess_items(self) -> None:
        available_width = max(180, self.lead_list.viewport().width() - 18)
        for row in range(self.lead_list.count()):
            item = self.lead_list.item(row)
            if not item.flags():
                continue
            text_rect = self.lead_list.fontMetrics().boundingRect(
                QRect(0, 0, available_width - 12, 10_000),
                Qt.TextFlag.TextWordWrap,
                item.text(),
            )
            item.setSizeHint(QSize(available_width, max(68, text_rect.height() + 18)))

    def _guess_selected(self, current: QListWidgetItem | None, _previous) -> None:
        if current is None:
            return
        stable_id = str(current.data(Qt.ItemDataRole.UserRole) or "")
        guess = self._guess_by_id.get(stable_id)
        if guess is None:
            return
        self.map_view.focus_guess(stable_id)
        extent = (
            "not localized"
            if guess.feature_box_world is None
            else " × ".join(f"{value:g}" for value in guess.feature_box_world)
        )
        self.selection_view.setHtml(
            f"<h3>{guess.marker} · {guess.label}</h3>"
            f"<p><b>What it will test:</b> {guess.status}</p>"
            f"<p><b>Why it exists:</b> {guess.evidence}</p>"
            f"<p><b>Confidence:</b> {guess.confidence:.0%}<br>"
            f"<b>Route anchor:</b> ({guess.anchor_cell[0]:g}, {guess.anchor_cell[1]:g})<br>"
            f"<b>Exact visual extent:</b> {extent}<br>"
            f"<b>Evidence kind:</b> {guess.evidence_kind}</p>"
        )
        self.inspector_tabs.setCurrentWidget(self.selection_view)

    def _map_item_selected(self, payload: object) -> None:
        if not isinstance(payload, Mapping):
            self.selection_view.setHtml("<h3>Nothing selected</h3><p>Select a mapped item for its evidence.</p>")
            return
        if payload.get("kind") == "guess" and payload.get("id"):
            stable_id = str(payload["id"])
            for row in range(self.lead_list.count()):
                item = self.lead_list.item(row)
                if str(item.data(Qt.ItemDataRole.UserRole) or "") == stable_id:
                    self.lead_list.setCurrentItem(item)
                    return
        rows = []
        for key, value in payload.items():
            if key in {"record", "tooltip"}:
                continue
            rows.append(f"<tr><td><b>{key.replace('_', ' ').title()}</b></td><td>{value}</td></tr>")
        record = payload.get("record")
        if isinstance(record, Mapping):
            for key in ("role_basis", "last_outcome", "outcome_counts", "approaches", "crossings"):
                if key in record:
                    rows.append(f"<tr><td><b>{key.replace('_', ' ').title()}</b></td><td>{record[key]}</td></tr>")
        self.selection_view.setHtml(
            f"<h3>{payload.get('label') or payload.get('kind') or 'Map item'}</h3><table>{''.join(rows)}</table>"
        )
        self.inspector_tabs.setCurrentWidget(self.selection_view)

    def _update_room_summary(self, room_name: str) -> None:
        room = self.model.rooms.get(room_name)
        if room is None:
            self.map_stats.setText("No remembered data")
            self.room_view.setPlainText("No remembered data for this room.")
            return
        regions = {
            (
                x // EXPLORATION_REGION_CELLS,
                y // EXPLORATION_REGION_CELLS,
            )
            for x, y in room.cells
        }
        self.map_stats.setText(
            f"{len(room.cells)} cells · {len(regions)} regions · {len(room.view_tiles)} scene tiles"
        )
        role_counts: dict[str, int] = {}
        for record in room.warps.values():
            role = str(record.get("role") or "unknown")
            role_counts[role] = role_counts.get(role, 0) + 1
        self.room_view.setHtml(
            f"<h3>{room_name}</h3>"
            f"<p><b>Walked cells:</b> {len(room.cells)}<br>"
            f"<b>Remembered scene tiles:</b> {len(room.view_tiles)}<br>"
            f"<b>Learned walls:</b> {len(room.blocked_edges)}<br>"
            f"<b>Confirmed interactions:</b> {len(room.interactables)}<br>"
            f"<b>Confirmed exits:</b> {len(room.warps)}</p>"
            f"<p><b>Exit roles:</b> {role_counts or 'none'}</p>"
        )


class _ArtifactSignals(QObject):
    loaded = Signal(str, object, object)


class _ArtifactTask(QRunnable):
    def __init__(self, directory: Path) -> None:
        super().__init__()
        self.directory = directory
        self.signals = _ArtifactSignals()

    def run(self) -> None:
        events = tail_jsonl(self.directory / "events.jsonl", limit=180)
        predictions = tail_jsonl(self.directory / "predictions.jsonl", limit=120)
        self.signals.loaded.emit(str(self.directory), events, predictions)


class RunsPage(QWidget):
    def __init__(self, runs_root: Path, parent=None) -> None:
        super().__init__(parent)
        self.runs_root = runs_root
        self._runs: dict[str, RunSummary] = {}
        self._selected_path = ""
        self._build()
        self.reload()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        _layout_margins(root, 14)
        heading_row = QHBoxLayout()
        heading_row.addWidget(PageHeader("Runs", "Inspect outcomes, decisions, predictions, maps, and diagnostics without loading entire logs."), 1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.reload)
        heading_row.addWidget(refresh)
        root.addLayout(heading_row)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.run_list = QListWidget()
        self.run_list.setMinimumWidth(315)
        self.run_list.setWordWrap(True)
        self.run_list.currentItemChanged.connect(self._selected)
        splitter.addWidget(self.run_list)
        self.tabs = QTabWidget()
        self.overview = QTextBrowser()
        self.timeline = QPlainTextEdit()
        self.timeline.setReadOnly(True)
        self.predictions = QPlainTextEdit()
        self.predictions.setReadOnly(True)
        self.autonomy_panel = QWidget()
        autonomy_layout = QVBoxLayout(self.autonomy_panel)
        _layout_margins(autonomy_layout, 12)
        autonomy_note = QLabel(
            "Read-only view of the AI's persistent goal contract, route progress, "
            "ranked alternatives, cycle protection, and shadow-policy consistency."
        )
        autonomy_note.setWordWrap(True)
        autonomy_layout.addWidget(autonomy_note)
        self.autonomy_summary = QTextBrowser()
        self.autonomy_summary.setMinimumHeight(190)
        autonomy_layout.addWidget(self.autonomy_summary, 2)
        self.autonomy_route = GoalRoutePreview()
        autonomy_layout.addWidget(self.autonomy_route)
        self.autonomy_options = QTableWidget(0, 8)
        self.autonomy_options.setHorizontalHeaderLabels(
            ("Rank", "Choice", "Option", "Kind", "Score", "Evidence", "Cost / risk", "Budget")
        )
        self.autonomy_options.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.autonomy_options.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.autonomy_options.setAlternatingRowColors(True)
        header = self.autonomy_options.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        autonomy_layout.addWidget(self.autonomy_options, 3)
        self.map_artifacts = QListWidget()
        self.map_artifacts.itemDoubleClicked.connect(self._open_artifact)
        self.diagnostics = QTextBrowser()
        self.tabs.addTab(self.overview, "Overview")
        self.tabs.addTab(self.timeline, "Timeline")
        self.tabs.addTab(self.predictions, "Predictions")
        self.tabs.addTab(self.autonomy_panel, "Autonomy")
        self.tabs.addTab(self.map_artifacts, "Maps & frames")
        self.tabs.addTab(self.diagnostics, "Diagnostics")
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 5)
        root.addWidget(splitter, 1)

    def set_runs_root(self, path: Path) -> None:
        self.runs_root = path
        self.reload()

    def reload(self) -> None:
        values = scan_runs(self.runs_root)
        self._runs = {str(run.directory): run for run in values}
        self.run_list.clear()
        for run in values:
            duration = "?" if run.duration_seconds is None else f"{run.duration_seconds:.1f}s"
            story = "?" if run.story_progress is None else str(run.story_progress)
            reward = "?" if run.total_reward is None else f"{run.total_reward:+.2f}"
            item = QListWidgetItem(
                f"{run.name}\n{run.status.upper()} · {duration} · step {run.last_step or 0}\n"
                f"Story {story} · reward {reward} · {run.warning_count} warning(s)"
                + (
                    f"\nTraining {run.training_status.replace('_', ' ')}"
                    + (f" · winner {run.recommended_winner}" if run.recommended_winner else "")
                    if run.training_status
                    else ""
                )
            )
            item.setData(Qt.ItemDataRole.UserRole, str(run.directory))
            item.setToolTip(run.error or run.stop_reason)
            self.run_list.addItem(item)
        if values:
            self.run_list.setCurrentRow(0)
        else:
            item = QListWidgetItem("No runs recorded for this profile yet.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.run_list.addItem(item)
            self.overview.setHtml("<h3>No runs yet</h3><p>Start the AI to create an auditable run folder.</p>")

    def _selected(self, item: QListWidgetItem | None, _previous) -> None:
        if item is None:
            return
        path_text = str(item.data(Qt.ItemDataRole.UserRole) or "")
        run = self._runs.get(path_text)
        if run is None:
            return
        self._selected_path = path_text
        self._show_overview(run)
        self.timeline.setPlainText("Loading the latest decisions in the background…")
        self.predictions.setPlainText("Loading the latest predictions in the background…")
        self.autonomy_summary.setHtml("<h2>Autonomy</h2><p>Loading saved decision snapshots…</p>")
        self.autonomy_route.set_coherence({})
        self.autonomy_options.setRowCount(0)
        task = _ArtifactTask(run.directory)
        task.signals.loaded.connect(self._artifact_loaded)
        QThreadPool.globalInstance().start(task)

    def _show_overview(self, run: RunSummary) -> None:
        self.overview.setHtml(
            f"<h2>{run.name}</h2><p><b>Status:</b> {run.status}<br>"
            f"<b>Started:</b> {run.started_at or 'not recorded'}<br>"
            f"<b>Duration:</b> {run.duration_seconds if run.duration_seconds is not None else '?'} seconds<br>"
            f"<b>Stop reason:</b> {run.stop_reason or 'not recorded'}<br>"
            f"<b>Last step:</b> {run.last_step if run.last_step is not None else '?'}<br>"
            f"<b>Events:</b> {run.events} · <b>Predictions:</b> {run.predictions} · "
            f"<b>Navigation updates:</b> {run.navigation_updates}<br>"
            f"<b>Story progress:</b> {run.story_progress if run.story_progress is not None else '?'} · "
            f"<b>Reward:</b> {run.total_reward if run.total_reward is not None else '?'}"
            + (
                f"<br><b>Training:</b> {escape(run.training_status)}"
                + (f" · recommended {escape(run.recommended_winner)}" if run.recommended_winner else "")
                if run.training_status
                else ""
            )
            + "</p>"
            + (f"<p><b>Read warning:</b> {run.error}</p>" if run.error else "")
        )
        self.map_artifacts.clear()
        for pattern in ("navigation_maps/*.png", "frame-*.png", "room_views/**/*.png"):
            for path in sorted(run.directory.glob(pattern))[:500]:
                entry = QListWidgetItem(str(path.relative_to(run.directory)))
                entry.setData(Qt.ItemDataRole.UserRole, str(path))
                self.map_artifacts.addItem(entry)
        try:
            report = json.loads((run.directory / "run_report.json").read_text(encoding="utf-8"))
            self.diagnostics.setPlainText(json.dumps(report, indent=2, ensure_ascii=False))
        except (OSError, json.JSONDecodeError):
            self.diagnostics.setPlainText("No run report was recorded.")

    def _artifact_loaded(self, directory: str, events: object, predictions: object) -> None:
        if directory != self._selected_path:
            return
        event_lines = []
        for _line, event in events if isinstance(events, list) else []:
            if isinstance(event, dict):
                event_lines.append(format_ai_decision(event))
        prediction_lines = []
        for line, value in predictions if isinstance(predictions, list) else []:
            if not isinstance(value, Mapping):
                continue
            context = value.get("decision_context")
            snapshot = value.get("prediction_snapshot")
            prediction_lines.append(
                f"Line {line + 1} · step {value.get('step', '?')} · {value.get('selected_action', '?')}\n"
                f"Reason: {value.get('reason', '')}\nContext: {context}\nCandidates: {snapshot}\n"
            )
        self.timeline.setPlainText("\n\n".join(event_lines) or "No readable decision events.")
        self.predictions.setPlainText("\n".join(prediction_lines) or "No readable prediction records.")
        prediction_rows = predictions if isinstance(predictions, list) else []
        records = [
            value
            for _line, value in prediction_rows
            if isinstance(value, Mapping)
        ]
        self._show_autonomy(summarize_autonomy_predictions(records))

    @staticmethod
    def _metric(value: float | None) -> str:
        return "—" if value is None else f"{value:.2f}"

    def _show_autonomy(self, summary: AutonomyWorkbenchSummary) -> None:
        self.autonomy_options.setRowCount(0)
        self.autonomy_route.set_coherence(summary.coherence)
        if not summary.available:
            self.autonomy_summary.setHtml(
                "<h2>Autonomy</h2><p>No Autonomy snapshots were found in the loaded "
                "prediction window. This is expected for runs created before Autonomy v1.</p>"
            )
            return

        shadow = summary.shadow
        disagreements = int(shadow.get("selection_disagreements") or 0)
        explained = int(shadow.get("commitment_explained_disagreements") or 0)
        unexplained = int(shadow.get("unexplained_selection_disagreements") or 0)
        overruns = int(shadow.get("budget_overrun_decisions") or 0)
        consistency = (
            "consistent"
            if unexplained == 0 and overruns == 0
            else "needs review"
        )
        goal = summary.active_goal_id or "none"
        selected = summary.selected_option_id or "none"
        commitment = "held the active goal" if summary.commitment_hold else "selected the current leader"
        budget = summary.active_budget
        budget_text = "none"
        if budget:
            budget_text = (
                f"{escape(str(budget.get('spent', 0)))} / {escape(str(budget.get('limit', 0)))} spent"
                f" ({escape(str(budget.get('remaining', 0)))} remaining)"
            )
        coherence = summary.coherence
        contract_value = coherence.get("goal_contract")
        contract = contract_value if isinstance(contract_value, Mapping) else {}
        target = contract.get("target_cell")
        if isinstance(target, (list, tuple)) and len(target) == 2:
            target_text = f"({escape(str(target[0]))}, {escape(str(target[1]))})"
        else:
            target_text = "not positional"
        target_room = str(contract.get("target_room") or "")
        if target_room:
            target_text += f" → {escape(target_room)}"
        route_distance = contract.get("current_route_distance")
        best_distance = contract.get("best_route_distance")
        route_text = (
            "not measured"
            if route_distance is None
            else f"{escape(str(route_distance))} cells remaining · best {escape(str(best_distance))}"
        )
        action_budget = (
            f"{escape(str(contract.get('actions_spent', 0)))} / "
            f"{escape(str(contract.get('action_budget', 0)))} actions"
            if contract
            else "no active contract"
        )
        expected = str(contract.get("expected_outcome") or "not recorded")
        triggers_value = contract.get("replan_triggers")
        triggers = (
            " · ".join(escape(str(value)) for value in triggers_value)
            if isinstance(triggers_value, list)
            else "not recorded"
        )
        recent_value = coherence.get("recent_rooms")
        recent_rooms = (
            " → ".join(escape(str(value)) for value in recent_value)
            if isinstance(recent_value, list) and recent_value
            else "none recorded"
        )
        lease_value = coherence.get("arrival_lease")
        lease = lease_value if isinstance(lease_value, Mapping) else {}
        lease_text = (
            f"{escape(str(lease.get('from_room') or '?'))} → "
            f"{escape(str(lease.get('room') or '?'))}, "
            f"{escape(str(lease.get('remaining', 0)))} steps remaining"
        )
        coherence_title = (
            f" · Navigation Coherence v{escape(str(coherence.get('version') or '?'))}"
            if coherence
            else ""
        )
        coherence_html = (
            f"<p><b>Contract target:</b> {target_text}<br>"
            f"<b>Expected outcome:</b> {escape(expected)}<br>"
            f"<b>Route progress:</b> {route_text} · "
            f"no-progress {escape(str(contract.get('no_progress_ticks', 0)))} ticks<br>"
            f"<b>Contract budget:</b> {action_budget}<br>"
            f"<b>Last replan:</b> {escape(str(coherence.get('last_replan_reason') or 'not recorded'))}</p>"
            f"<p><b>Replan triggers:</b> {triggers}<br>"
            f"<b>Recent room trajectory:</b> {recent_rooms}<br>"
            f"<b>Arrival lease:</b> {lease_text} · "
            f"reset cooldown {escape(str(coherence.get('broad_reset_cooldown_remaining', 0)))}<br>"
            f"<b>Learned choices:</b> {escape(str(coherence.get('frontier_clusters', 0)))} frontier cluster(s) · "
            f"{escape(str(coherence.get('portal_apertures', 0)))} portal aperture(s) from "
            f"{escape(str(coherence.get('portal_samples', 0)))} sample(s)</p>"
            if coherence
            else (
                "<p><b>Navigation Coherence:</b> not recorded for this older run. "
                "Recovery and ranked-option evidence above remains available.</p>"
            )
        )
        self.autonomy_summary.setHtml(
            f"<h2>Autonomy v{summary.version or '?'}{coherence_title} · "
            f"{escape(summary.recovery_level)}</h2>"
            f"<p><b>Latest snapshot:</b> step {summary.latest_step if summary.latest_step is not None else '?'}"
            f" · room {escape(summary.latest_room or 'unknown')}<br>"
            f"<b>Recovery reason:</b> {escape(summary.recovery_reason or 'not recorded')} "
            f"(level age {summary.recovery_level_age}, story stall {summary.story_stall_steps})</p>"
            f"<p><b>Active goal:</b> {escape(goal)}"
            f" ({escape(summary.active_goal_kind or 'no kind')}, age {summary.active_goal_age})<br>"
            f"<b>Selected:</b> {escape(selected)} · {escape(commitment)}<br>"
            f"<b>Active uncertainty budget:</b> {budget_text}</p>"
            f"{coherence_html}"
            f"<p><b>Shadow check:</b> {escape(consistency)} across "
            f"{int(shadow.get('decision_count') or 0)} loaded Autonomy decisions. "
            f"{disagreements} selection difference(s), {explained} explained by commitment, "
            f"{unexplained} unexplained, {overruns} budget overrun(s).</p>"
        )

        self.autonomy_options.setRowCount(len(summary.options))
        for row, option in enumerate(summary.options):
            evidence = (
                f"conf {self._metric(option.confidence)} · info {self._metric(option.information_value)}"
                f" · novel {self._metric(option.novelty)}"
            )
            cost = (
                f"distance {self._metric(option.distance)} · loop {self._metric(option.loop_risk)}"
                f" · fail {self._metric(option.failure_cost)}"
            )
            budget_value = (
                "not bounded"
                if option.budget_limit <= 0
                else f"{option.budget_spent}/{option.budget_limit} · {option.budget_remaining} left"
            )
            values = (
                str(row + 1),
                "Selected" if option.selected else "",
                option.option_id or "unnamed",
                f"{option.kind} · {option.required_level}",
                self._metric(option.score),
                evidence,
                cost,
                budget_value,
            )
            tooltip = json.dumps(option.metadata, indent=2, ensure_ascii=False, default=str)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(tooltip)
                self.autonomy_options.setItem(row, column, item)

    def _open_artifact(self, item: QListWidgetItem) -> None:
        path = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))


class ProfilesPage(QWidget):
    profileActivated = Signal(object)

    def __init__(
        self,
        project_root: Path,
        store: ProfileStore,
        can_switch: Callable[[], bool],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.project_root = project_root
        self.store = store
        self.can_switch = can_switch
        self._profiles: dict[str, Profile] = {}
        self._build()
        self.reload()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        _layout_margins(root, 18)
        root.addWidget(PageHeader("Profiles", "Keep learned maps, reinforcement memory, and run history isolated between experiments."))
        self.build_card = Card()
        build_layout = QHBoxLayout(self.build_card)
        _layout_margins(build_layout, 14)
        build_text = QVBoxLayout()
        title = QLabel("Build safety")
        title.setObjectName("sectionTitle")
        build_text.addWidget(title)
        self.build_label = QLabel("Checking local branch…")
        self.build_label.setWordWrap(True)
        build_text.addWidget(self.build_label)
        build_layout.addLayout(build_text, 1)
        root.addWidget(self.build_card)

        row = QHBoxLayout()
        title = QLabel("Save profiles")
        title.setObjectName("sectionTitle")
        row.addWidget(title)
        row.addStretch(1)
        for label, callback in (
            ("New", self._create),
            ("Duplicate", self._duplicate),
            ("Rename", self._rename),
            ("Delete", self._delete),
            ("Open folder", self._open_folder),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            if label == "Delete":
                button.setObjectName("dangerButton")
            row.addWidget(button)
        root.addLayout(row)
        self.profile_list = QListWidget()
        self.profile_list.setWordWrap(True)
        self.profile_list.itemDoubleClicked.connect(lambda _item: self._activate())
        root.addWidget(self.profile_list, 1)
        action_row = QHBoxLayout()
        self.activate_button = QPushButton("Activate selected profile")
        self.activate_button.setObjectName("primary")
        self.activate_button.clicked.connect(self._activate)
        action_row.addWidget(self.activate_button)
        action_row.addStretch(1)
        self.profile_hint = QLabel("Double-click a profile to activate it.")
        self.profile_hint.setObjectName("meta")
        action_row.addWidget(self.profile_hint)
        root.addLayout(action_row)

    def set_build_status(self, status: BuildStatus) -> None:
        self.build_label.setText(f"{status.label}\n{status.detail or 'Local repository inspected.'}")
        self.build_label.setObjectName("success" if status.safe_for_testing else "warning")
        self.build_label.style().unpolish(self.build_label)
        self.build_label.style().polish(self.build_label)

    def reload(self) -> None:
        selected = self._selected_id()
        profiles = self.store.profiles()
        active = self.store.active()
        self._profiles = {profile.id: profile for profile in profiles}
        self.profile_list.clear()
        for profile in profiles:
            memory_count, run_count = self.store.profile_file_counts(profile.id)
            active_text = "ACTIVE" if profile.id == active.id else "Available"
            item = QListWidgetItem(
                f"{profile.name}   ·   {active_text}\n"
                f"{memory_count} memory files · {run_count} runs · last used {profile.last_used_at}"
            )
            item.setData(Qt.ItemDataRole.UserRole, profile.id)
            self.profile_list.addItem(item)
            if profile.id == (selected or active.id):
                self.profile_list.setCurrentItem(item)

    def _selected_id(self) -> str:
        item = self.profile_list.currentItem() if hasattr(self, "profile_list") else None
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item is not None else ""

    def _selected_profile(self) -> Profile | None:
        return self._profiles.get(self._selected_id())

    def _ask_name(self, title: str, initial: str = "") -> str | None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        layout = QVBoxLayout(dialog)
        entry = QLineEdit(initial)
        entry.selectAll()
        layout.addWidget(QLabel("Profile name"))
        layout.addWidget(entry)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        value = entry.text().strip()
        return value or None

    def _error(self, title: str, exc: Exception) -> None:
        QMessageBox.critical(self, title, str(exc))

    def _create(self) -> None:
        name = self._ask_name("New profile", "Experiment")
        if name is None:
            return
        try:
            profile = self.store.create(name)
            self.reload()
            self._select_id(profile.id)
        except (OSError, ValueError) as exc:
            self._error("Could not create profile", exc)

    def _duplicate(self) -> None:
        source = self._selected_profile()
        if source is None:
            return
        name = self._ask_name("Duplicate profile", f"{source.name} copy")
        if name is None:
            return
        try:
            profile = self.store.create(name, source_profile_id=source.id, include_runs=False)
            self.reload()
            self._select_id(profile.id)
        except (OSError, ValueError) as exc:
            self._error("Could not duplicate profile", exc)

    def _rename(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            return
        name = self._ask_name("Rename profile", profile.name)
        if name is None:
            return
        try:
            self.store.rename(profile.id, name)
            self.reload()
        except (OSError, ValueError) as exc:
            self._error("Could not rename profile", exc)

    def _delete(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            return
        if not self.can_switch():
            QMessageBox.warning(self, "AI is running", "Stop the current run before deleting a profile.")
            return
        if QMessageBox.question(
            self,
            "Delete profile",
            f'Delete "{profile.name}" and all of its memory and run history?',
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.delete(profile.id)
            active = self.store.active()
            self.store.activate(self.project_root, active.id)
            self.reload()
            self.profileActivated.emit(active)
        except (OSError, ValueError) as exc:
            self._error("Could not delete profile", exc)

    def _activate(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            return
        if not self.can_switch():
            QMessageBox.warning(self, "AI is running", "Stop the current run before changing profiles.")
            return
        try:
            active = self.store.activate(self.project_root, profile.id)
            self.reload()
            self.profileActivated.emit(active)
        except OSError as exc:
            self._error("Could not activate profile", exc)

    def _select_id(self, profile_id: str) -> None:
        for row in range(self.profile_list.count()):
            item = self.profile_list.item(row)
            if str(item.data(Qt.ItemDataRole.UserRole) or "") == profile_id:
                self.profile_list.setCurrentItem(item)
                break

    def _open_folder(self) -> None:
        profile = self._selected_profile()
        if profile is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.profile_directory(profile.id))))


class TrainingPage(QWidget):
    promotionCompleted = Signal(object)

    def __init__(
        self,
        runs_root: Path,
        memory_path: Callable[[], Path],
        can_promote: Callable[[], bool],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.runs_root = Path(runs_root)
        self.memory_path = memory_path
        self.can_promote = can_promote
        self._run_paths: list[Path] = []
        self._selected_run: Path | None = None
        self._build()
        self.reload()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        _layout_margins(root, 16)
        heading = QHBoxLayout()
        heading.addWidget(
            PageHeader(
                "Population Training",
                "A configurable population of isolated strategy heads shares "
                "observed world evidence; one candidate owns each complete "
                "causal segment.",
            ),
            1,
        )
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.reload)
        heading.addWidget(refresh)
        root.addLayout(heading)

        selector = QHBoxLayout()
        selector.addWidget(QLabel("Training run"))
        self.run_combo = QComboBox()
        self.run_combo.currentIndexChanged.connect(self._run_selected)
        selector.addWidget(self.run_combo, 1)
        self.status = QLabel("No training session")
        self.status.setObjectName("meta")
        selector.addWidget(self.status)
        root.addLayout(selector)

        situation = Card()
        situation_layout = QVBoxLayout(situation)
        self.active_label = QLabel("Waiting for a population run")
        self.active_label.setObjectName("pageTitle")
        situation_layout.addWidget(self.active_label)
        self.segment_label = QLabel(
            "Start Population training with live input and telemetry to compare candidates."
        )
        self.segment_label.setWordWrap(True)
        situation_layout.addWidget(self.segment_label)
        root.addWidget(situation)

        candidate_host = QGroupBox("All AIs · live rank and recommendation")
        candidate_layout = QVBoxLayout(candidate_host)
        candidate_layout.setContentsMargins(9, 8, 9, 9)
        candidate_layout.setSpacing(6)
        self.leader_summary = QLabel("Waiting for candidate scores")
        self.leader_summary.setObjectName("muted")
        self.leader_summary.setWordWrap(True)
        candidate_layout.addWidget(self.leader_summary)
        self.candidate_grid = QGridLayout()
        self.candidate_grid.setContentsMargins(0, 0, 0, 0)
        self.candidate_grid.setHorizontalSpacing(7)
        self.candidate_grid.setVerticalSpacing(7)
        candidate_layout.addLayout(self.candidate_grid, 1)
        self.candidate_cards: dict[str, QFrame] = {}
        root.addWidget(candidate_host, 1)

        review = Card()
        review_layout = QHBoxLayout(review)
        self.winner_explanation = QLabel(
            "A winner is recommended only after every exposure, telemetry, speed, cleanup, and Run Doctor gate passes."
        )
        self.winner_explanation.setWordWrap(True)
        review_layout.addWidget(self.winner_explanation, 1)
        self.promote_button = QPushButton("Review and promote winner")
        self.promote_button.setObjectName("primary")
        self.promote_button.setEnabled(False)
        self.promote_button.clicked.connect(self._promote)
        review_layout.addWidget(self.promote_button)
        root.addWidget(review)

    def set_runs_root(self, path: Path) -> None:
        self.runs_root = Path(path)
        self.reload()

    def reload(self) -> None:
        manifests = sorted(
            self.runs_root.glob("*/training_manifest.json"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        ) if self.runs_root.is_dir() else []
        previous = str(self._selected_run or "")
        self._run_paths = [path.parent for path in manifests]
        self.run_combo.blockSignals(True)
        self.run_combo.clear()
        for path in self._run_paths:
            self.run_combo.addItem(path.name, str(path))
        if previous:
            index = self.run_combo.findData(previous)
            if index >= 0:
                self.run_combo.setCurrentIndex(index)
        self.run_combo.blockSignals(False)
        self._run_selected(self.run_combo.currentIndex())

    def _run_selected(self, index: int) -> None:
        if not 0 <= index < len(self._run_paths):
            self._selected_run = None
            self._render({}, historical=True)
            return
        self._selected_run = self._run_paths[index]
        try:
            payload = json.loads(
                (self._selected_run / "training_manifest.json").read_text(encoding="utf-8")
            )
            eligibility = payload.get("eligibility") if isinstance(payload, Mapping) else {}
            self._render(eligibility if isinstance(eligibility, Mapping) else {}, historical=True, manifest=payload)
        except (OSError, UnicodeError, json.JSONDecodeError):
            self._render({}, historical=True)

    def handle_event(self, payload: object) -> None:
        if not isinstance(payload, Mapping):
            return
        training = payload.get("training")
        if isinstance(training, Mapping):
            self._render(training, historical=False)

    @staticmethod
    def _candidate_number(candidate: Mapping[str, object], key: str) -> float:
        try:
            return float(candidate.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _rank_candidates(
        cls,
        candidates: list[Mapping[str, object]],
    ) -> list[Mapping[str, object]]:
        """Mirror PopulationCoordinator.ranked_candidates for a truthful display."""

        return sorted(
            candidates,
            key=lambda candidate: (
                bool(candidate.get("disqualified")),
                not bool(candidate.get("minimum_exposure_met")),
                -cls._candidate_number(candidate, "normalized_score"),
                -cls._candidate_number(candidate, "story_progress"),
                cls._candidate_number(candidate, "safety_penalties"),
                str(candidate.get("id") or ""),
            ),
        )

    def _clear_candidate_grid(self) -> None:
        while self.candidate_grid.count():
            item = self.candidate_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.candidate_cards.clear()

    def _candidate_card(
        self,
        candidate: Mapping[str, object],
        *,
        rank: int,
        active_id: str,
        leader_id: str,
        winner_id: str,
        shadow: Mapping[str, object],
    ) -> QFrame:
        candidate_id = str(candidate.get("id") or f"candidate-{rank}")
        label = str(candidate.get("label") or candidate_id)
        disqualified = bool(candidate.get("disqualified"))
        exposed = bool(candidate.get("minimum_exposure_met"))
        is_winner = candidate_id == winner_id
        is_leader = candidate_id == leader_id and exposed and not disqualified and not is_winner
        is_provisional = candidate_id == leader_id and not exposed and not disqualified
        is_active = candidate_id == active_id

        card = QFrame()
        card.setObjectName("candidateCard")
        card.setProperty("candidate_id", candidate_id)
        card.setProperty("winner", is_winner)
        card.setProperty("leader", is_leader)
        card.setProperty("provisional", is_provisional)
        card.setProperty("disqualified", disqualified)
        card.setProperty("active", is_active)
        card.setMinimumHeight(92)
        card.setMaximumHeight(118)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(1)
        header = QHBoxLayout()
        header.setSpacing(4)
        name = QLabel(f"#{rank}  {label}")
        name.setObjectName("sectionTitle")
        name.setToolTip(candidate_id)
        header.addWidget(name, 1)
        badges: list[str] = []
        if is_winner:
            badges.append("WINNER")
        elif is_leader:
            badges.append("LEADER")
        elif is_provisional:
            badges.append("PROVISIONAL")
        if is_active:
            badges.append("ACTIVE")
        if disqualified:
            badges.append("DQ")
        badge = QLabel(" · ".join(badges))
        badge.setObjectName(
            "danger" if disqualified else "warning" if is_provisional else "success" if (is_winner or is_leader) else "meta"
        )
        header.addWidget(badge)
        layout.addLayout(header)

        score = self._candidate_number(candidate, "normalized_score")
        score_label = QLabel(f"Score {score:+.3f}")
        score_label.setObjectName("candidateScore")
        layout.addWidget(score_label)

        segments = int(self._candidate_number(candidate, "segments_completed"))
        decisions = int(self._candidate_number(candidate, "active_decisions"))
        points = self._candidate_number(candidate, "total_points")
        story = int(self._candidate_number(candidate, "story_progress"))
        stats = QLabel(
            f"{segments} seg · {decisions} decisions · {points:+.2f} pts · story {story}"
        )
        stats.setObjectName("meta")
        layout.addWidget(stats)

        reasons_value = candidate.get("disqualification_reasons")
        reasons = [str(value) for value in reasons_value] if isinstance(reasons_value, list) else []
        if disqualified:
            safety_text = "Disqualified" + (f": {'; '.join(reasons)}" if reasons else "")
            safety_name = "danger"
        elif exposed:
            safety_text = "Eligible exposure · safety clear"
            safety_name = "success"
        else:
            safety_text = f"Gathering exposure · {segments}/2 seg · {decisions}/64 decisions"
            safety_name = "warning"
        safety = QLabel(safety_text)
        safety.setObjectName(safety_name)
        safety.setToolTip(safety_text)
        layout.addWidget(safety)

        option_id = str(shadow.get("id") or "No legal recommendation")
        option_kind = str(shadow.get("kind") or "shared")
        option_score = shadow.get("score")
        option_score_text = ""
        if option_score is not None:
            try:
                option_score_text = f" · {float(option_score):.3f}"
            except (TypeError, ValueError):
                option_score_text = ""
        concise_id = option_id if len(option_id) <= 42 else option_id[:39] + "…"
        recommendation_text = f"Next: {concise_id} · {option_kind}{option_score_text}"
        recommendation = QLabel(recommendation_text)
        recommendation.setObjectName("meta")
        recommendation.setToolTip(f"Next: {option_id} · {option_kind}{option_score_text}")
        layout.addWidget(recommendation)
        card.setProperty("top_recommendation", option_id)
        card.setToolTip(
            f"{label} ({candidate_id})\nRank {rank}\nNormalized score {score:+.3f}\n"
            f"{segments} completed segments, {decisions} active decisions\n{safety_text}\n"
            f"Next recommendation: {option_id} ({option_kind}){option_score_text}"
        )
        return card

    def _render(
        self,
        training: Mapping[str, object],
        *,
        historical: bool,
        manifest: Mapping[str, object] | None = None,
    ) -> None:
        candidates_value = training.get("candidates")
        candidates = [
            candidate
            for candidate in candidates_value
            if isinstance(candidate, Mapping)
        ] if isinstance(candidates_value, list) else []
        active = str(training.get("active_candidate") or "")
        segment_value = training.get("segment")
        segment = segment_value if isinstance(segment_value, Mapping) else {}
        if historical and manifest:
            status = str(manifest.get("status") or "finished")
            active = str(training.get("recommended_winner") or "")
        else:
            status = "live" if candidates else "waiting"
        self.status.setText(status.replace("_", " ").upper())
        active_candidate = next(
            (candidate for candidate in candidates if str(candidate.get("id")) == active),
            None,
        )
        active_name = str(active_candidate.get("label") or active) if active_candidate else active
        eligible = bool(training.get("eligible_for_promotion"))
        if historical and eligible and active:
            active_text = f"Recommended winner: {active_name or active}"
        elif historical:
            active_text = "Training completed"
        else:
            active_text = f"Active candidate: {active_name or 'not available'}"
        self.active_label.setText(active_text + (f" · {len(candidates)} AIs" if candidates else ""))
        if segment:
            self.segment_label.setText(
                f"Segment {segment.get('index', '?')} · {segment.get('start_reason', 'unknown reason')} · "
                f"age {segment.get('age_steps', 0)} steps · {segment.get('active_decisions', 0)} active decisions"
                + (f" · pending {segment.get('pending_end_reason')}" if segment.get("pending_end_reason") else "")
            )
        elif historical:
            checks = training.get("global_checks")
            passed = sum(bool(value) for value in checks.values()) if isinstance(checks, Mapping) else 0
            total = len(checks) if isinstance(checks, Mapping) else 0
            self.segment_label.setText(f"Completed training review · {passed}/{total} global safety gates passed")
        else:
            self.segment_label.setText("No live population event has been received.")

        shadow_value = training.get("shadow_rankings")
        shadows = shadow_value if isinstance(shadow_value, Mapping) else {
            str(candidate.get("id") or ""): candidate.get("shadow_ranking", [])
            for candidate in candidates
        }
        ranked = self._rank_candidates(candidates)
        safe_ranked = [candidate for candidate in ranked if not bool(candidate.get("disqualified"))]
        winner_id = str(training.get("recommended_winner") or "") if eligible else ""
        leader_id = winner_id or (
            str(safe_ranked[0].get("id") or "") if safe_ranked else ""
        )
        leader = next(
            (candidate for candidate in ranked if str(candidate.get("id") or "") == leader_id),
            None,
        )
        contenders = [
            candidate
            for candidate in safe_ranked
            if bool(candidate.get("minimum_exposure_met"))
        ]
        margin_text = ""
        if leader is not None and len(contenders) > 1 and leader in contenders:
            runner_up = next((candidate for candidate in contenders if candidate is not leader), None)
            if runner_up is not None:
                margin = self._candidate_number(leader, "normalized_score") - self._candidate_number(
                    runner_up, "normalized_score"
                )
                margin_text = f" · lead {margin:+.3f} over #{ranked.index(runner_up) + 1}"
        if leader is None:
            self.leader_summary.setText("Waiting for candidate scores")
            self.leader_summary.setObjectName("muted")
        else:
            leader_name = str(leader.get("label") or leader_id)
            score = self._candidate_number(leader, "normalized_score")
            if winner_id:
                prefix = "Recommended winner"
                object_name = "success"
            elif historical:
                prefix = "Top score only — no promotable winner"
                object_name = "warning"
            elif bool(leader.get("minimum_exposure_met")):
                prefix = "Current leader"
                object_name = "success"
            else:
                prefix = "Provisional leader — minimum exposure not met"
                object_name = "warning"
            self.leader_summary.setText(f"{prefix}: {leader_name} · score {score:+.3f}{margin_text}")
            self.leader_summary.setObjectName(object_name)

        self._clear_candidate_grid()
        column_count = min(4, max(2, len(ranked)))
        for index, candidate in enumerate(ranked):
            candidate_id = str(candidate.get("id") or "")
            ranking_value = shadows.get(candidate_id, [])
            ranking = ranking_value if isinstance(ranking_value, list) else []
            top = ranking[0] if ranking and isinstance(ranking[0], Mapping) else {}
            card = self._candidate_card(
                candidate,
                rank=index + 1,
                active_id=active,
                leader_id=leader_id,
                winner_id=winner_id,
                shadow=top,
            )
            self.candidate_cards[candidate_id] = card
            self.candidate_grid.addWidget(card, index // column_count, index % column_count)
        for column in range(4):
            self.candidate_grid.setColumnStretch(column, 1 if column < column_count else 0)
        row_count = (len(ranked) + column_count - 1) // column_count if ranked else 0
        for row in range(4):
            self.candidate_grid.setRowStretch(row, 1 if row < row_count else 0)

        explanation = str(training.get("winner_explanation") or "")
        self.winner_explanation.setText(
            explanation
            or "A winner is recommended only after every exposure, telemetry, speed, cleanup, and Run Doctor gate passes."
        )
        already_promoted = bool(manifest and manifest.get("status") == "promoted")
        self.promote_button.setEnabled(historical and eligible and not already_promoted and self.can_promote())

    def _promote(self) -> None:
        if self._selected_run is None or not self.can_promote():
            return
        try:
            manifest = json.loads(
                (self._selected_run / "training_manifest.json").read_text(encoding="utf-8")
            )
            eligibility = manifest.get("eligibility") if isinstance(manifest, Mapping) else {}
            winner = str(eligibility.get("recommended_winner") or "") if isinstance(eligibility, Mapping) else ""
            explanation = str(eligibility.get("winner_explanation") or "") if isinstance(eligibility, Mapping) else ""
            answer = QMessageBox.question(
                self,
                "Promote training winner?",
                f"Promote {winner} into the active profile?\n\n{explanation}\n\n"
                "Verified shared map/visual evidence and only this candidate's strategy and reinforcement memory will be applied. A backup is retained.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            audit = promote_training_run(self._selected_run, self.memory_path())
            QMessageBox.information(
                self,
                "Training winner promoted",
                f"Promoted {audit.get('winner')} successfully.\nBackup: {audit.get('backup_directory')}",
            )
            self.promotionCompleted.emit(audit)
            self.reload()
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            QMessageBox.critical(self, "Could not promote training winner", str(exc))


class LearningPage(QWidget):
    def __init__(
        self,
        project_root: Path,
        parent=None,
        *,
        can_modify_memory: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__(parent)
        self.project_root = project_root
        self.can_modify_memory = can_modify_memory or (lambda: True)
        self.reward_fields: dict[str, QDoubleSpinBox] = {}
        self._build()
        self.reload()

    @property
    def settings_path(self) -> Path:
        return self.project_root / "memory" / REINFORCEMENT_SETTINGS_FILENAME

    @property
    def memory_path(self) -> Path:
        return self.project_root / "memory" / REINFORCEMENT_MEMORY_FILENAME

    def _build(self) -> None:
        root = QVBoxLayout(self)
        _layout_margins(root, 18)
        root.addWidget(PageHeader("Learning", "Tune how strongly the AI explores and how it credits actions after progress."))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        _layout_margins(body_layout, 2)

        behavior = QGroupBox("Learning behavior")
        behavior_form = QFormLayout(behavior)
        self.enabled = QCheckBox("Enable reinforcement learning")
        behavior_form.addRow(self.enabled)
        self.preset = QComboBox()
        self.preset.addItems([*PRESETS, CUSTOM_PRESET])
        self.preset.currentTextChanged.connect(self._preset_selected)
        behavior_form.addRow("Preset", self.preset)
        self.exploration = QDoubleSpinBox()
        self.exploration.setRange(0, 10)
        self.exploration.setDecimals(3)
        self.exploration.setToolTip("Higher values test uncertain actions more often.")
        behavior_form.addRow("Exploration constant", self.exploration)
        self.decay = QDoubleSpinBox()
        self.decay.setRange(0, 1)
        self.decay.setDecimals(3)
        self.decay.setToolTip("How much delayed reward reaches earlier decisions.")
        behavior_form.addRow("Eligibility decay", self.decay)
        self.trace = QSpinBox()
        self.trace.setRange(1, 32)
        behavior_form.addRow("Trace length", self.trace)
        self.repeat_steps = QSpinBox()
        self.repeat_steps.setRange(1, 240)
        behavior_form.addRow("Decision repeat steps", self.repeat_steps)
        body_layout.addWidget(behavior)

        rewards = QGroupBox("Reward values")
        reward_grid = QGridLayout(rewards)
        for index, (key, label, help_text) in enumerate(REWARD_FIELD_SPECS):
            field = QDoubleSpinBox()
            field.setRange(-1000, 1000)
            field.setDecimals(4)
            field.setToolTip(help_text)
            self.reward_fields[key] = field
            row = index // 2
            column = (index % 2) * 2
            reward_grid.addWidget(QLabel(label), row, column)
            reward_grid.addWidget(field, row, column + 1)
        body_layout.addWidget(rewards)

        learned = QGroupBox("Learned outcomes")
        learned_layout = QVBoxLayout(learned)
        self.learning_summary = QTextBrowser()
        self.learning_summary.setMaximumHeight(170)
        learned_layout.addWidget(self.learning_summary)
        body_layout.addWidget(learned)
        body_layout.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        actions = QHBoxLayout()
        save = QPushButton("Save settings")
        save.setObjectName("primary")
        save.clicked.connect(self.save)
        actions.addWidget(save)
        reload_button = QPushButton("Reload")
        reload_button.clicked.connect(self.reload)
        actions.addWidget(reload_button)
        reset = QPushButton("Reset learned rewards")
        reset.setObjectName("dangerButton")
        reset.clicked.connect(self._reset_memory)
        actions.addWidget(reset)
        actions.addStretch(1)
        self.status = QLabel("")
        self.status.setObjectName("meta")
        actions.addWidget(self.status)
        root.addLayout(actions)

    def _preset_selected(self, name: str) -> None:
        if name in PRESETS:
            self._apply(RewardSettings.for_preset(name))

    def _apply(self, settings: RewardSettings) -> None:
        widgets = [self.preset, *self.reward_fields.values()]
        for widget in widgets:
            widget.blockSignals(True)
        self.enabled.setChecked(settings.enabled)
        self.preset.setCurrentText(settings.detect_preset())
        self.exploration.setValue(settings.exploration_constant)
        self.decay.setValue(settings.eligibility_decay)
        self.trace.setValue(settings.trace_length)
        self.repeat_steps.setValue(settings.decision_repeat_steps)
        for key, field in self.reward_fields.items():
            field.setValue(settings.reward(key))
        for widget in widgets:
            widget.blockSignals(False)

    def _from_form(self) -> RewardSettings:
        settings = RewardSettings(
            enabled=self.enabled.isChecked(),
            preset=self.preset.currentText(),
            exploration_constant=self.exploration.value(),
            eligibility_decay=self.decay.value(),
            trace_length=self.trace.value(),
            decision_repeat_steps=self.repeat_steps.value(),
            rewards={key: field.value() for key, field in self.reward_fields.items()},
        )
        settings.validate()
        return settings

    def reload(self) -> None:
        settings = load_reward_settings(self.settings_path)
        self._apply(settings)
        self.status.setText(f"Loaded {settings.detect_preset()} for the active profile.")
        try:
            data = json.loads(self.memory_path.read_text(encoding="utf-8"))
            records = data.get("records", {}) if isinstance(data, Mapping) else {}
            ranked = sorted(
                (record for record in records.values() if isinstance(record, Mapping)),
                key=lambda record: float(record.get("total_reward", 0) or 0),
                reverse=True,
            )[:8]
            lines = [
                f"{record.get('kind', 'action')}: {float(record.get('total_reward', 0) or 0):+.2f} over {record.get('attempts', 0)} attempts"
                for record in ranked
            ]
            self.learning_summary.setPlainText("\n".join(lines) or "No learned actions yet.")
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            self.learning_summary.setPlainText("No learned actions yet.")

    def save(self) -> None:
        if not self.can_modify_memory():
            QMessageBox.warning(
                self,
                "Learning memory is in use",
                "Stop the active run before changing reinforcement settings.",
            )
            return
        try:
            settings = self._from_form()
            save_reward_settings(self.settings_path, settings)
            self._apply(settings)
            self.status.setText(f"Saved {settings.detect_preset()}.")
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not save learning settings", str(exc))

    def _reset_memory(self) -> None:
        if not self.can_modify_memory():
            QMessageBox.warning(
                self,
                "Learning memory is in use",
                "Stop the active run before resetting learned rewards.",
            )
            return
        if QMessageBox.question(
            self,
            "Reset learned rewards",
            "Delete the active profile's reinforcement outcomes? Maps and runs will not be deleted.",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.memory_path.unlink(missing_ok=True)
            self.reload()
        except OSError as exc:
            QMessageBox.critical(self, "Could not reset learning", str(exc))


class LogsPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.records: deque[tuple[str, str, str]] = deque(maxlen=5000)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        _layout_margins(root, 14)
        root.addWidget(PageHeader("Logs", "Readable decisions, raw controller output, telemetry, and runtime notices."))
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Source"))
        self.source = QComboBox()
        self.source.addItems(["All", "Decisions", "Telemetry", "Runtime", "Build"])
        self.source.currentTextChanged.connect(self._render)
        toolbar.addWidget(self.source)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search visible log…")
        self.search.textChanged.connect(self._render)
        toolbar.addWidget(self.search, 1)
        self.follow = QCheckBox("Follow latest")
        self.follow.setChecked(True)
        toolbar.addWidget(self.follow)
        clear = QPushButton("Clear view")
        clear.clicked.connect(self.clear)
        toolbar.addWidget(clear)
        root.addLayout(toolbar)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        root.addWidget(self.output, 1)
        self.count = QLabel("0 buffered messages")
        self.count.setObjectName("meta")
        root.addWidget(self.count)

    def append(self, source: str, text: str, severity: str = "info") -> None:
        if not text:
            return
        self.records.append((source, severity, text))
        chosen = self.source.currentText()
        query = self.search.text().casefold()
        if (chosen == "All" or chosen == source) and (not query or query in text.casefold()):
            self.output.appendPlainText(f"[{source}] {text}\n")
            if self.follow.isChecked():
                self.output.verticalScrollBar().setValue(self.output.verticalScrollBar().maximum())
        self.count.setText(f"{len(self.records)} buffered messages · capped at 5,000")

    def _render(self) -> None:
        chosen = self.source.currentText()
        query = self.search.text().casefold()
        lines = [
            f"[{source}] {text}"
            for source, _severity, text in self.records
            if (chosen == "All" or chosen == source) and (not query or query in text.casefold())
        ]
        self.output.setPlainText("\n\n".join(lines))
        if self.follow.isChecked():
            self.output.verticalScrollBar().setValue(self.output.verticalScrollBar().maximum())

    def clear(self) -> None:
        self.records.clear()
        self.output.clear()
        self.count.setText("0 buffered messages")


class SettingsPage(QWidget):
    appearanceChanged = Signal(str, object)
    themeImported = Signal(object)

    def __init__(
        self,
        themes: Mapping[str, Theme],
        theme_id: str,
        background: BackgroundSettings,
        themes_directory: Path | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.themes = dict(themes)
        self.themes_directory = themes_directory
        self._build()
        self._load(theme_id, background)

    def _build(self) -> None:
        root = QVBoxLayout(self)
        _layout_margins(root, 18)
        root.addWidget(PageHeader("Settings", "Choose an original interface theme, control animated backgrounds, and reduce visual motion."))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        _layout_margins(body_layout, 2)
        appearance = QGroupBox("Appearance")
        form = QFormLayout(appearance)
        self.theme_combo = QComboBox()
        for identifier, theme in self.themes.items():
            self.theme_combo.addItem(theme.name, identifier)
        self.theme_combo.currentIndexChanged.connect(self._changed)
        form.addRow("Theme", self.theme_combo)
        self.theme_description = QLabel("")
        self.theme_description.setObjectName("meta")
        self.theme_description.setWordWrap(True)
        form.addRow("", self.theme_description)
        theme_actions = QWidget()
        theme_actions_layout = QHBoxLayout(theme_actions)
        theme_actions_layout.setContentsMargins(0, 0, 0, 0)
        import_theme = QPushButton("Import theme JSON…")
        import_theme.clicked.connect(self._import_theme)
        theme_actions_layout.addWidget(import_theme)
        open_themes = QPushButton("Open themes folder")
        open_themes.clicked.connect(self._open_themes_folder)
        theme_actions_layout.addWidget(open_themes)
        theme_actions_layout.addStretch(1)
        form.addRow("Custom themes", theme_actions)
        path_row = QWidget()
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        self.background_path = QLineEdit()
        self.background_path.setPlaceholderText("Use theme background")
        self.background_path.editingFinished.connect(self._changed)
        path_layout.addWidget(self.background_path, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        path_layout.addWidget(browse)
        clear = QPushButton("Theme default")
        clear.clicked.connect(self._clear_background)
        path_layout.addWidget(clear)
        form.addRow("Background", path_row)
        self.mode = QComboBox()
        self.mode.addItems(["Cover", "Contain", "Stretch"])
        self.mode.currentTextChanged.connect(self._changed)
        form.addRow("Image fit", self.mode)
        self.dim = QSlider(Qt.Orientation.Horizontal)
        self.dim.setRange(0, 95)
        self.dim.valueChanged.connect(self._changed)
        form.addRow("Background dim", self.dim)
        self.parallax = QCheckBox("Subtle pointer parallax")
        self.parallax.toggled.connect(self._changed)
        form.addRow(self.parallax)
        self.animation = QCheckBox("Animate GIF and video backgrounds")
        self.animation.toggled.connect(self._changed)
        form.addRow(self.animation)
        self.reduce_motion = QCheckBox("Reduce motion")
        self.reduce_motion.toggled.connect(self._changed)
        form.addRow(self.reduce_motion)
        body_layout.addWidget(appearance)

        behavior = QGroupBox("Operator defaults")
        behavior_form = QFormLayout(behavior)
        behavior_form.addRow("Map startup", QLabel("Fit active room; remember zoom and pan per room"))
        behavior_form.addRow("Run logs", QLabel("5,000 live messages; complete history remains in run artifacts"))
        behavior_form.addRow("Motion while unfocused", QLabel("Animated backgrounds pause automatically"))
        body_layout.addWidget(behavior)

        credits = QGroupBox("Background credits")
        credits_layout = QVBoxLayout(credits)
        note = QLabel(
            "Bundled wallpapers were supplied by the project owner. Their file-level credits are documented in THIRD_PARTY_ASSETS.md. "
            "Custom files remain outside profile memory and do not affect AI decisions."
        )
        note.setWordWrap(True)
        credits_layout.addWidget(note)
        body_layout.addWidget(credits)
        body_layout.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        self.warning = QLabel("")
        self.warning.setObjectName("warning")
        root.addWidget(self.warning)

    def _load(self, theme_id: str, background: BackgroundSettings) -> None:
        index = self.theme_combo.findData(theme_id)
        self.theme_combo.setCurrentIndex(max(0, index))
        self.background_path.setText(background.path)
        self.mode.setCurrentText(background.mode.title())
        self.dim.setValue(round(background.dim * 100))
        self.parallax.setChecked(background.parallax)
        self.animation.setChecked(background.animation)
        self.reduce_motion.setChecked(background.reduce_motion)
        self._update_description()

    def current(self) -> tuple[str, BackgroundSettings]:
        identifier = str(self.theme_combo.currentData() or "operator")
        return identifier, BackgroundSettings(
            path=self.background_path.text().strip(),
            mode=self.mode.currentText().casefold(),
            dim=self.dim.value() / 100,
            tint=self.themes.get(identifier, next(iter(self.themes.values()))).colors["base"],
            parallax=self.parallax.isChecked(),
            animation=self.animation.isChecked(),
            reduce_motion=self.reduce_motion.isChecked(),
        )

    def _changed(self, *_args) -> None:
        self._update_description()
        identifier, background = self.current()
        self.appearanceChanged.emit(identifier, background)

    def _update_description(self) -> None:
        theme = self.themes.get(str(self.theme_combo.currentData() or ""))
        if theme is not None:
            self.theme_description.setText(f"{theme.description}  ·  {theme.attribution}")

    def _browse(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Choose a background",
            self.background_path.text() or str(Path.home()),
            "Backgrounds (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.mp4 *.webm *.mkv)",
        )
        if path:
            self.background_path.setText(path)
            self._changed()

    def _clear_background(self) -> None:
        self.background_path.clear()
        self._changed()

    def _import_theme(self) -> None:
        source_text, _filter = QFileDialog.getOpenFileName(
            self,
            "Import theme manifest",
            str(Path.home()),
            "Theme manifests (*.json)",
        )
        if not source_text:
            return
        source = Path(source_text)
        try:
            theme = load_theme_file(source)
            destination = (
                self.themes_directory / f"{theme.id}.json"
                if self.themes_directory is not None
                else None
            )
            if destination is not None and destination.exists() and QMessageBox.question(
                self,
                "Replace custom theme?",
                f'A theme named "{theme.id}" is already installed. Replace it?',
            ) != QMessageBox.StandardButton.Yes:
                return
            self.install_theme(source)
            self.warning.setText(f'Imported "{theme.name}".')
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            QMessageBox.critical(self, "Could not import theme", str(exc))

    def install_theme(self, source: Path) -> Theme:
        """Validate, persist, and activate a custom manifest."""

        theme = load_theme_file(source)
        destination_root = self.themes_directory
        if destination_root is None:
            raise OSError("The custom theme folder is unavailable.")
        destination_root.mkdir(parents=True, exist_ok=True)
        destination = destination_root / f"{theme.id}.json"
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)
        self.themes[theme.id] = theme
        self.themeImported.emit(theme)
        index = self.theme_combo.findData(theme.id)
        if index < 0:
            self.theme_combo.addItem(theme.name, theme.id)
            index = self.theme_combo.count() - 1
        else:
            self.theme_combo.setItemText(index, theme.name)
        self.theme_combo.setCurrentIndex(index)
        return theme

    def _open_themes_folder(self) -> None:
        if self.themes_directory is None:
            QMessageBox.warning(self, "Themes folder unavailable", "The custom theme folder is unavailable.")
            return
        try:
            self.themes_directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "Could not open themes folder", str(exc))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.themes_directory)))
