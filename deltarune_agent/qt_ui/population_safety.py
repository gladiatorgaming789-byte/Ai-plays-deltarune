"""Qt safeguards for Independent Population Training v2.1."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QGroupBox, QMessageBox, QPushButton, QScrollArea, QWidget


_SPEED_BUTTON_TEXTS = {"F8 toggle", "F9 −", "F10 +"}


def install_population_safety(operator_window_cls, training_page_cls) -> None:
    """Keep independent-AI events isolated and make the 16-AI view usable."""

    if getattr(operator_window_cls, "_population_safety_installed", False):
        return

    original_event = operator_window_cls._controller_event
    original_state = operator_window_cls._controller_state
    original_send_speed = operator_window_cls.send_speed_key
    original_apply_speed = operator_window_cls.apply_selected_game_speed
    original_training_build = training_page_cls._build

    def training_build(page) -> None:
        original_training_build(page)
        candidate_host = next(
            (
                group
                for group in page.findChildren(QGroupBox)
                if group.title().startswith("All independent AIs")
            ),
            None,
        )
        if candidate_host is None or candidate_host.layout() is None:
            return
        layout = candidate_host.layout()
        # Original order: leader summary, candidate grid. The grid is empty at
        # construction time, so it is safe to replace it with a scroll-hosted
        # grid before TrainingPage.reload() creates any cards.
        if layout.count() < 2:
            return
        item = layout.takeAt(1)
        old_grid = item.layout()
        if old_grid is None:
            return
        old_grid.deleteLater()

        scroll = QScrollArea(candidate_host)
        scroll.setObjectName("trainingCandidateScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        grid_host = QWidget(scroll)
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(7)
        grid.setVerticalSpacing(7)
        scroll.setWidget(grid_host)
        layout.insertWidget(1, scroll, 1)
        page.candidate_grid = grid
        page.candidate_scroll = scroll

    def controller_event(window, payload: object) -> None:
        # Independent candidate events describe different game processes. Never
        # merge them into the single-profile Live Map model. Population status
        # still feeds the Training page, and runtime status remains visible.
        if (
            isinstance(payload, dict)
            and payload.get("instance")
            and window.run_mode.currentText() == "Population training"
        ):
            window.training_page.handle_event(payload)
            decision = payload.get("action")
            instance = payload.get("instance")
            if isinstance(instance, dict) and decision:
                label = str(instance.get("label") or instance.get("id") or "AI")
                window.logs_page.append(
                    "Training",
                    f"{label}: {decision} · {payload.get('reason', '')}",
                )
            return
        original_event(window, payload)
        if (
            isinstance(payload, dict)
            and window.run_mode.currentText() == "Population training"
            and payload.get("training")
        ):
            window.speed_status.setText("Per-instance speed verification · see Training")

    def controller_state(window, state: str) -> None:
        original_state(window, state)
        running = state in {"starting", "running", "stopping"}
        population = window.run_mode.currentText() == "Population training"
        for button in window.findChildren(QPushButton):
            if button.text() in _SPEED_BUTTON_TEXTS:
                button.setEnabled(not (running and population))
                button.setToolTip(
                    "Per-instance speed controls are locked during Population Training "
                    "so one candidate cannot be changed independently."
                    if running and population
                    else ""
                )
        # Settings displayed in the run bar describe the already-running child
        # process configuration and must not imply they can mutate it live.
        window.live_input.setEnabled(not running)
        window.steps.setEnabled(not running)
        window.game_window.setEnabled(not running)

    def send_speed(window, key: str) -> None:
        if window.controller.running and window.run_mode.currentText() == "Population training":
            QMessageBox.information(
                window,
                "Population speed is locked",
                "Speed hotkeys are disabled while independent AIs are being compared. "
                "Stop training and start a new population run with the desired speed.",
            )
            return
        original_send_speed(window, key)

    def apply_speed(window) -> None:
        if window.controller.running and window.run_mode.currentText() == "Population training":
            QMessageBox.information(
                window,
                "Population speed is locked",
                "Changing only one Deltarune process would invalidate the comparison. "
                "Stop training and choose the speed before the next run.",
            )
            return
        original_apply_speed(window)

    training_page_cls._build = training_build
    operator_window_cls._controller_event = controller_event
    operator_window_cls._controller_state = controller_state
    operator_window_cls.send_speed_key = send_speed
    operator_window_cls.apply_selected_game_speed = apply_speed
    operator_window_cls._population_safety_installed = True


__all__ = ["install_population_safety"]
