"""Qt process bridge for the existing line-oriented controller protocol."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from uuid import uuid4

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from ..gui import EVENT_PREFIX
from ..strategy import (
    DEFAULT_POPULATION_SIZE,
    validate_population_size,
)
from ..speed import MAX_MULTIPLIER, parse_requested_speed
from ..window import find_window, post_window_key, remember_window


SPEED_KEY_HOLD_MS = 55
SPEED_KEY_GAP_MS = 70


class RunController(QObject):
    eventReceived = Signal(object)
    outputReceived = Signal(str)
    stateChanged = Signal(str)
    finished = Signal(int)

    def __init__(self, project_root: Path, parent=None) -> None:
        super().__init__(parent)
        self.project_root = project_root.resolve()
        self.window_memory = self.project_root / "memory" / "window_titles.json"
        self.stop_file: Path | None = None
        self._buffer = ""
        self._speed_sequence_id = 0
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(self.project_root))
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.started.connect(lambda: self.stateChanged.emit("running"))
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)

    @property
    def running(self) -> bool:
        return self.process.state() != QProcess.ProcessState.NotRunning

    def start_run(
        self,
        *,
        steps: int,
        game_window: str,
        speed: str,
        live: bool,
        training: bool = False,
        population_size: int = DEFAULT_POPULATION_SIZE,
    ) -> None:
        if self.running:
            return
        # Runtime control files must never dirty profile memory, especially
        # while a population run is holding a baseline fingerprint.
        stop_directory = Path(tempfile.gettempdir()) / "DeltaruneAgent"
        stop_directory.mkdir(parents=True, exist_ok=True)
        self.stop_file = stop_directory / f"qt-gui-stop-{uuid4().hex}.flag"
        self.stop_file.unlink(missing_ok=True)
        self._buffer = ""
        arguments = [
            "-u",
            "-m",
            "deltarune_agent",
            "run",
            "--steps",
            str(max(1, steps)),
            "--game-window",
            game_window.strip() or "deltarune",
            "--event-stream",
            "--stop-file",
            str(self.stop_file),
            "--window-memory",
            str(self.window_memory),
            "--speed",
            speed.casefold().removesuffix("x"),
        ]
        if live:
            arguments.append("--live")
        if training:
            arguments.extend(
                (
                    "--training",
                    "--population-size",
                    str(validate_population_size(population_size)),
                )
            )
        self.stateChanged.emit("starting")
        self.process.start(sys.executable, arguments)

    def request_stop(self) -> None:
        if not self.running:
            return
        if self.stop_file is not None:
            self.stop_file.write_text("stop\n", encoding="utf-8")
        self.stateChanged.emit("stopping")

    def force_stop(self) -> None:
        if self.running:
            self.process.kill()

    def send_speed_key(self, key: str, game_window: str) -> None:
        self._speed_sequence_id += 1
        window = find_window(game_window.strip() or "deltarune", self.window_memory)
        if window is None:
            raise RuntimeError("No Deltarune window is running. Launch a chapter first.")
        remember_window(self.window_memory, window)
        post_window_key(window.hwnd, key, True)

        def release() -> None:
            try:
                post_window_key(window.hwnd, key, False)
            except (OSError, ValueError) as exc:
                self.outputReceived.emit(f"Speed key release warning: {exc}")

        QTimer.singleShot(80, release)
        self.outputReceived.emit(f"Sent {key.upper()} to {window.title}.")

    def apply_game_speed(self, speed: str, game_window: str) -> bool:
        """Drive the speed mod's hotkeys to a known manual multiplier."""

        requested = parse_requested_speed(speed)
        if requested == "auto":
            self.outputReceived.emit(
                "Auto speed leaves the game multiplier unchanged and follows "
                "DRSPEED telemetry."
            )
            return False
        window = find_window(game_window.strip() or "deltarune", self.window_memory)
        if window is None:
            raise RuntimeError("No Deltarune window is running. Launch a chapter first.")
        remember_window(self.window_memory, window)
        target = int(requested)
        keys = ["f9"] * (MAX_MULTIPLIER - 1) + ["f10"] * (target - 1)
        self._speed_sequence_id += 1
        sequence_id = self._speed_sequence_id
        self.outputReceived.emit(
            f"Applying {target}x to {window.title}; waiting for DRSPEED verification."
        )

        def press(index: int) -> None:
            if sequence_id != self._speed_sequence_id:
                return
            if index >= len(keys):
                self.outputReceived.emit(
                    f"Requested {target}x game speed. The status must confirm "
                    f"Game: {target}x."
                )
                return
            key = keys[index]
            try:
                post_window_key(window.hwnd, key, True)
            except (OSError, ValueError) as exc:
                self.outputReceived.emit(f"Game speed key warning: {exc}")
                return

            def release() -> None:
                if sequence_id != self._speed_sequence_id:
                    return
                try:
                    post_window_key(window.hwnd, key, False)
                except (OSError, ValueError) as exc:
                    self.outputReceived.emit(f"Game speed key release warning: {exc}")
                    return
                QTimer.singleShot(SPEED_KEY_GAP_MS, lambda: press(index + 1))

            QTimer.singleShot(SPEED_KEY_HOLD_MS, release)

        press(0)
        return True

    def _read_output(self) -> None:
        self._buffer += bytes(self.process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        lines = self._buffer.split("\n")
        self._buffer = lines.pop()
        for line in lines:
            self._handle_line(line.rstrip("\r"))

    def _handle_line(self, line: str) -> None:
        if line.startswith(EVENT_PREFIX):
            try:
                payload = json.loads(line[len(EVENT_PREFIX) :])
            except json.JSONDecodeError:
                self.outputReceived.emit(f"Malformed GUI event: {line}")
                return
            if isinstance(payload, dict):
                self.eventReceived.emit(payload)
            return
        self.outputReceived.emit(line)

    def _finished(self, exit_code: int, _status) -> None:
        if self._buffer:
            self._handle_line(self._buffer.rstrip("\r"))
            self._buffer = ""
        if self.stop_file is not None:
            self.stop_file.unlink(missing_ok=True)
            self.stop_file = None
        self.stateChanged.emit("stopped" if exit_code == 0 else "error")
        self.finished.emit(exit_code)

    def _process_error(self, _error) -> None:
        self.outputReceived.emit(self.process.errorString())
