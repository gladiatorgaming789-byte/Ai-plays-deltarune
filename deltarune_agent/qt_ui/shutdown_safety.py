"""Failure-safe Qt shutdown behavior for the operator console.

The base GUI historically forced QProcess.kill() five seconds after a close
request.  That is too short for population-training supervisors and can bypass
keyboard release, run-artifact finalization, memory persistence, and child game
cleanup.  This layer keeps ordinary closing cooperative and makes the destructive
path an explicit operator choice.
"""

from __future__ import annotations

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtWidgets import QMessageBox


SAFE_STOP_FIRST_GRACE_MS = 12_000
SAFE_STOP_RECHECK_MS = 10_000


def install_shutdown_safety(operator_window_cls, run_controller_cls) -> None:
    """Install idempotent cooperative-close behavior on the production Qt types."""

    if getattr(operator_window_cls, "_shutdown_safety_installed", False):
        return

    original_close_event = operator_window_cls.closeEvent
    original_process_error = run_controller_cls._process_error

    def offer_force_stop(window) -> None:
        if not window._closing_after_stop or not window.controller.running:
            return
        answer = QMessageBox.warning(
            window,
            "AI is still stopping",
            "The controller is still completing shutdown and cleanup.\n\n"
            "Force-stopping now may interrupt keyboard release, saved run artifacts, "
            "training child-process cleanup, or memory persistence.\n\n"
            "Force stop anyway? Choose No to keep waiting safely.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            window.logs_page.append(
                "Runtime",
                "Emergency force stop requested by operator; normal cleanup may be incomplete.",
                "warning",
            )
            window.controller.force_stop()
            return
        QTimer.singleShot(SAFE_STOP_RECHECK_MS, window._safe_shutdown_check)

    def safe_shutdown_check(window) -> None:
        if not window._closing_after_stop:
            return
        if not window.controller.running:
            QTimer.singleShot(0, window.close)
            return
        offer_force_stop(window)

    def close_event(window, event) -> None:
        if not window.controller.running:
            original_close_event(window, event)
            return

        if not window._closing_after_stop:
            answer = QMessageBox.question(
                window,
                "Stop the AI?",
                "The AI is still running. Request a safe stop and close only after "
                "the controller finishes cleanup?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            window._closing_after_stop = True
            window.controller.request_stop()
            QTimer.singleShot(SAFE_STOP_FIRST_GRACE_MS, window._safe_shutdown_check)
            event.ignore()
            return

        # A second close while shutdown is still in progress must never slip
        # through to QMainWindow destruction.  Treat it as an explicit request
        # to consider the emergency path instead.
        offer_force_stop(window)
        event.ignore()

    def process_error(controller, error) -> None:
        original_process_error(controller, error)
        if error != QProcess.ProcessError.FailedToStart:
            return
        if controller.stop_file is not None:
            controller.stop_file.unlink(missing_ok=True)
            controller.stop_file = None
        controller.stateChanged.emit("error")
        # QProcess does not reliably emit finished() for FailedToStart.  Emit
        # the controller's public completion signal so the GUI cannot remain
        # permanently stuck in STARTING.
        controller.finished.emit(-1)

    operator_window_cls._safe_shutdown_check = safe_shutdown_check
    operator_window_cls.closeEvent = close_event
    operator_window_cls._shutdown_safety_installed = True
    run_controller_cls._process_error = process_error


__all__ = [
    "SAFE_STOP_FIRST_GRACE_MS",
    "SAFE_STOP_RECHECK_MS",
    "install_shutdown_safety",
]
