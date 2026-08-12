"""PySide6 map-first operator console."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from PySide6.QtCore import QEvent, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QIcon, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedLayout,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..build_status import BuildStatus, inspect_build
from ..gui import format_speed_status
from ..run19_profiles import MigrationResult, Profile, ProfileStore
from ..version import AGENT_REVISION
from .background import BackgroundLayer
from .controller import RunController
from .pages import (
    LearningPage,
    LiveMapPage,
    LogsPage,
    ProfilesPage,
    RunsPage,
    SettingsPage,
)
from .themes import (
    BUILTIN_THEMES,
    BackgroundSettings,
    Theme,
    discover_themes,
    stylesheet,
)


PAGE_DEFINITIONS = (
    ("map", "◈  Live Map"),
    ("runs", "▤  Runs"),
    ("profiles", "♙  Profiles"),
    ("learning", "⌁  Learning"),
    ("logs", "≡  Logs"),
    ("settings", "⚙  Settings"),
)


class TitleBar(QFrame):
    def __init__(self, window: "OperatorWindow") -> None:
        super().__init__(window)
        self.window_ref = window
        self.setObjectName("topBar")
        self.setFixedHeight(46)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 4, 5, 4)
        layout.setSpacing(6)
        mark = QLabel("◆")
        mark.setObjectName("success")
        layout.addWidget(mark)
        title = QLabel("Deltarune AI Controller")
        title.setObjectName("title")
        layout.addWidget(title)
        self.context = QLabel("")
        self.context.setObjectName("meta")
        layout.addWidget(self.context)
        layout.addStretch(1)
        for label, callback, name in (
            ("—", window.showMinimized, "minimize"),
            ("□", window.toggle_maximized, "maximize"),
            ("×", window.close, "close"),
        ):
            button = QPushButton(label)
            button.setObjectName("dangerButton" if name == "close" else "")
            button.setFixedSize(40, 31)
            button.clicked.connect(callback)
            layout.addWidget(button)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window_ref.windowHandle()
            if handle is not None:
                handle.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.window_ref.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class OperatorWindow(QMainWindow):
    """Custom-framed, map-first home for all controller operations."""

    def __init__(
        self,
        *,
        project_root: Path,
        store: ProfileStore,
        migration_result: MigrationResult | None = None,
    ) -> None:
        super().__init__()
        self.project_root = project_root.resolve()
        self.store = store
        self.migration_result = migration_result or MigrationResult()
        self.active_profile = store.active()
        self.build_status = inspect_build(self.project_root, AGENT_REVISION, fetch_remote=False)
        self.settings = QSettings("GladiatorGaming", "DeltaruneAIController")
        self.themes, self.theme_warnings = discover_themes(self.store.root / "themes")
        self.theme_id = str(self.settings.value("appearance/theme", "castle_town"))
        if self.theme_id not in self.themes:
            self.theme_id = "operator"
        self.background_settings = self._load_background_settings()
        self._closing_after_stop = False
        self._resizing = False
        self.nav_buttons: dict[str, QPushButton] = {}
        self.page_indexes: dict[str, int] = {}
        self.controller = RunController(self.project_root, self)
        self._build_window()
        self._connect()
        self.apply_appearance(self.theme_id, self.background_settings, persist=False)
        self._restore_window_state()
        QApplication.instance().installEventFilter(self)
        self._show_build_status()

    def _build_window(self) -> None:
        self.setWindowTitle("Deltarune AI Controller")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.setMinimumSize(1120, 700)
        self.resize(1480, 900)

        host = QWidget()
        host.setObjectName("backdrop")
        layered = QStackedLayout(host)
        layered.setContentsMargins(0, 0, 0, 0)
        layered.setStackingMode(QStackedLayout.StackingMode.StackAll)
        self.background = BackgroundLayer(host)
        layered.addWidget(self.background)
        chrome = QWidget(host)
        chrome.setObjectName("chrome")
        layered.addWidget(chrome)
        layered.setCurrentWidget(chrome)
        self.setCentralWidget(host)

        outer = QVBoxLayout(chrome)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)
        self.title_bar = TitleBar(self)
        outer.addWidget(self.title_bar)

        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        outer.addLayout(content, 1)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(205)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 18, 12, 12)
        side.setSpacing(5)
        label = QLabel("OPERATOR CONSOLE")
        label.setObjectName("meta")
        side.addWidget(label)
        for page_id, page_label in PAGE_DEFINITIONS:
            button = QPushButton(page_label)
            button.setObjectName("nav")
            button.setCheckable(True)
            button.setMinimumHeight(42)
            button.clicked.connect(lambda checked=False, page_id=page_id: self.select_page(page_id))
            self.nav_buttons[page_id] = button
            side.addWidget(button)
        side.addStretch(1)
        self.sidebar_profile = QLabel("")
        self.sidebar_profile.setWordWrap(True)
        side.addWidget(self.sidebar_profile)
        self.sidebar_build = QLabel("")
        self.sidebar_build.setObjectName("meta")
        self.sidebar_build.setWordWrap(True)
        side.addWidget(self.sidebar_build)
        content.addWidget(sidebar)

        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(10, 8, 10, 10)
        main_layout.setSpacing(8)
        main_layout.addWidget(self._build_run_bar())
        self.pages = QStackedWidget()
        self.live_page = LiveMapPage(
            self.project_root,
            can_modify_memory=lambda: not self.controller.running,
        )
        self.runs_page = RunsPage(self.store.runs_directory(self.active_profile.id))
        self.profiles_page = ProfilesPage(
            self.project_root,
            self.store,
            can_switch=lambda: not self.controller.running,
        )
        self.learning_page = LearningPage(self.project_root)
        self.logs_page = LogsPage()
        self.settings_page = SettingsPage(
            self.themes,
            self.theme_id,
            self.background_settings,
            themes_directory=self.store.root / "themes",
        )
        for identifier, page in (
            ("map", self.live_page),
            ("runs", self.runs_page),
            ("profiles", self.profiles_page),
            ("learning", self.learning_page),
            ("logs", self.logs_page),
            ("settings", self.settings_page),
        ):
            self.page_indexes[identifier] = self.pages.addWidget(page)
        main_layout.addWidget(self.pages, 1)
        content.addWidget(main, 1)
        self.select_page(str(self.settings.value("ui/page", "map")))
        self._update_profile_labels()

    def _build_run_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("topBar")
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(5)
        primary = QHBoxLayout()
        secondary = QHBoxLayout()
        primary.setSpacing(7)
        secondary.setSpacing(7)
        self.live_input = QCheckBox("Live input")
        primary.addWidget(self.live_input)
        self.steps = QSpinBox()
        self.steps.setRange(1, 10_000_000)
        self.steps.setValue(int(self.settings.value("run/steps", 2000)))
        self.steps.setPrefix("Steps  ")
        self.steps.setMinimumWidth(125)
        primary.addWidget(self.steps)
        self.game_window = QLineEdit(str(self.settings.value("run/game_window", "deltarune")))
        self.game_window.setPlaceholderText("Deltarune window")
        self.game_window.setMaximumWidth(190)
        primary.addWidget(self.game_window)
        self.start_button = QPushButton("Start AI")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start_run)
        primary.addWidget(self.start_button)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.controller.request_stop)
        primary.addWidget(self.stop_button)
        primary.addStretch(1)
        self.run_status = QLabel("STOPPED")
        self.run_status.setObjectName("warning")
        primary.addWidget(self.run_status)
        secondary.addWidget(QLabel("Game / AI speed"))
        self.speed = QComboBox()
        self.speed.addItems(["Auto", *(f"{value}x" for value in range(1, 11))])
        self.speed.setCurrentText(str(self.settings.value("run/speed", "Auto")))
        secondary.addWidget(self.speed)
        for key, label in (("f8", "F8 toggle"), ("f9", "F9 −"), ("f10", "F10 +")):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, key=key: self.send_speed_key(key))
            secondary.addWidget(button)
        secondary.addStretch(1)
        self.speed_status = QLabel("Game unknown · AI 1x")
        self.speed_status.setObjectName("meta")
        self.speed_status.setMinimumWidth(205)
        secondary.addWidget(self.speed_status)
        layout.addLayout(primary)
        layout.addLayout(secondary)
        return bar

    def _connect(self) -> None:
        self.controller.eventReceived.connect(self._controller_event)
        self.controller.outputReceived.connect(lambda text: self.logs_page.append("Runtime", text))
        self.controller.stateChanged.connect(self._controller_state)
        self.controller.finished.connect(self._controller_finished)
        self.live_page.decisionLogged.connect(lambda text: self.logs_page.append("Decisions", text))
        self.live_page.telemetryLogged.connect(lambda text: self.logs_page.append("Telemetry", text))
        self.live_page.runtimeLogged.connect(lambda text: self.logs_page.append("Runtime", text))
        self.profiles_page.profileActivated.connect(self._profile_activated)
        self.settings_page.appearanceChanged.connect(self.apply_appearance)
        self.settings_page.themeImported.connect(self._theme_imported)

    def _theme_imported(self, theme: object) -> None:
        if isinstance(theme, Theme):
            self.themes[theme.id] = theme

    def _load_background_settings(self) -> BackgroundSettings:
        raw = self.settings.value("appearance/background", "")
        try:
            value = json.loads(str(raw)) if raw else {}
        except json.JSONDecodeError:
            value = {}
        return BackgroundSettings.from_mapping(value if isinstance(value, dict) else {})

    def _restore_window_state(self) -> None:
        geometry = self.settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    def select_page(self, identifier: str) -> None:
        if identifier not in self.page_indexes:
            identifier = "map"
        self.pages.setCurrentIndex(self.page_indexes[identifier])
        for page_id, button in self.nav_buttons.items():
            button.setChecked(page_id == identifier)
        self.settings.setValue("ui/page", identifier)

    def start_run(self) -> None:
        if self.controller.running:
            return
        if not self.build_status.safe_for_testing:
            answer = QMessageBox.warning(
                self,
                "Build safety warning",
                f"{self.build_status.label}\n\n{self.build_status.detail or 'This checkout is not verified current.'}\n\nStart anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.select_page("profiles")
                return
        self.settings.setValue("run/steps", self.steps.value())
        self.settings.setValue("run/game_window", self.game_window.text())
        self.settings.setValue("run/speed", self.speed.currentText())
        self.controller.start_run(
            steps=self.steps.value(),
            game_window=self.game_window.text(),
            speed=self.speed.currentText(),
            live=self.live_input.isChecked(),
        )

    def send_speed_key(self, key: str) -> None:
        try:
            self.controller.send_speed_key(key, self.game_window.text())
        except (OSError, RuntimeError, ValueError) as exc:
            QMessageBox.critical(self, "Could not change game speed", str(exc))

    def _controller_event(self, payload: object) -> None:
        self.live_page.handle_event(payload)
        if isinstance(payload, dict):
            if payload.get("kind") == "runtime_status":
                status = str(payload.get("status") or "running")
                self.run_status.setText(status.upper())
            else:
                self.speed_status.setText(format_speed_status(payload.get("speed")))

    def _controller_state(self, state: str) -> None:
        running = state in {"starting", "running", "stopping"}
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(state in {"starting", "running"})
        labels = {
            "starting": "STARTING",
            "running": "RUNNING LIVE" if self.live_input.isChecked() else "RUNNING DRY",
            "stopping": "STOPPING SAFELY",
            "stopped": "STOPPED",
            "error": "STOPPED · ERROR",
        }
        self.run_status.setText(labels.get(state, state.upper()))
        self.run_status.setObjectName("success" if state == "running" else "danger" if state == "error" else "warning")
        self.run_status.style().unpolish(self.run_status)
        self.run_status.style().polish(self.run_status)

    def _controller_finished(self, exit_code: int) -> None:
        self.logs_page.append("Runtime", f"AI exited with code {exit_code}.", "error" if exit_code else "info")
        self.speed_status.setText("Game unknown · AI stopped")
        self.runs_page.reload()
        if self._closing_after_stop:
            self._closing_after_stop = False
            QTimer.singleShot(0, self.close)

    def _profile_activated(self, profile: Profile) -> None:
        self.active_profile = profile
        self.live_page.reload_memory()
        self.learning_page.reload()
        self.runs_page.set_runs_root(self.store.runs_directory(profile.id))
        self._update_profile_labels()
        self.logs_page.append("Runtime", f'Activated profile "{profile.name}".')

    def _update_profile_labels(self) -> None:
        self.sidebar_profile.setText(f"◆  {self.active_profile.name}")
        self.title_bar.context.setText(f"  ·  {self.active_profile.name}")

    def _show_build_status(self) -> None:
        self.profiles_page.set_build_status(self.build_status)
        self.sidebar_build.setText(f"{self.build_status.label}\n{AGENT_REVISION}")
        self.logs_page.append("Build", self.build_status.label)
        for warning in self.theme_warnings:
            self.logs_page.append("Runtime", f"Theme warning: {warning}", "warning")

    def apply_appearance(
        self,
        theme_id: str,
        background: BackgroundSettings,
        *,
        persist: bool = True,
    ) -> None:
        theme = self.themes.get(theme_id, BUILTIN_THEMES["operator"])
        self.theme_id = theme.id
        self.background_settings = background
        QApplication.instance().setStyleSheet(stylesheet(theme))
        self.background.set_theme(theme)
        self.background.set_settings(background)
        self.live_page.set_map_palette(theme.map_colors)
        if hasattr(self, "settings_page"):
            self.settings_page.warning.setText(self.background.last_warning)
        if persist:
            self.settings.setValue("appearance/theme", theme.id)
            self.settings.setValue(
                "appearance/background",
                json.dumps(background.to_mapping(), separators=(",", ":")),
            )

    def toggle_maximized(self) -> None:
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if isinstance(event, QMouseEvent) and event.type() in {
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonPress,
        }:
            widget = watched if isinstance(watched, QWidget) else None
            if widget is not None and widget.window() is self:
                local = self.mapFromGlobal(event.globalPosition().toPoint())
                nx = local.x() / max(1, self.width()) * 2 - 1
                ny = local.y() / max(1, self.height()) * 2 - 1
                self.background.set_parallax_position(nx, ny)
                if not self.isMaximized() and event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                    edges = self._resize_edges(local.x(), local.y())
                    if edges and self.windowHandle() is not None:
                        self.windowHandle().startSystemResize(edges)
                        return True
        return super().eventFilter(watched, event)

    def _resize_edges(self, x: int, y: int) -> Qt.Edge:
        margin = 7
        edges = Qt.Edge(0)
        if x <= margin:
            edges |= Qt.Edge.LeftEdge
        elif x >= self.width() - margin:
            edges |= Qt.Edge.RightEdge
        if y <= margin:
            edges |= Qt.Edge.TopEdge
        elif y >= self.height() - margin:
            edges |= Qt.Edge.BottomEdge
        return edges

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() in {QEvent.Type.WindowStateChange, QEvent.Type.ActivationChange}:
            active = self.isActiveWindow() and not self.isMinimized()
            self.background.set_motion_active(active)

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        if self.controller.running and not self._closing_after_stop:
            answer = QMessageBox.question(
                self,
                "Stop the AI?",
                "The AI is still running. Request a safe stop and close when it exits?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._closing_after_stop = True
            self.controller.request_stop()
            QTimer.singleShot(5000, self._force_close_after_timeout)
            event.ignore()
            return
        self.settings.setValue("window/geometry", self.saveGeometry())
        QApplication.instance().removeEventFilter(self)
        event.accept()

    def _force_close_after_timeout(self) -> None:
        if self._closing_after_stop and self.controller.running:
            self.controller.force_stop()


def launch_qt_gui() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Deltarune AI Controller")
    app.setOrganizationName("GladiatorGaming")
    project_root = Path(__file__).resolve().parents[2]
    store = ProfileStore()
    try:
        migration = store.migrate_legacy_data(project_root)
        store.activate(project_root, store.active().id)
    except OSError as exc:
        QMessageBox.critical(None, "Profile setup failed", str(exc))
        return 1
    window = OperatorWindow(project_root=project_root, store=store, migration_result=migration)
    window.show()
    return app.exec()


__all__ = ["OperatorWindow", "launch_qt_gui"]
