"""Tk GUI for the numerical neural shadow agent.

This window is deliberately read-only with respect to Deltarune. It receives the
same telemetry stream as the gameplay controller, converts it into the fixed
numerical observation vector, and displays the neural policy/value output.

It never sends keyboard input and never asks the neural model to consume a
screenshot. Vision remains disabled by default.
"""

from __future__ import annotations

import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .neural_shadow import NeuralShadowAgent, NeuralShadowWriter
from .telemetry import TelemetryReceiver


class NeuralShadowGUI:
    POLL_MS = 50

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Deltarune AI — Neural Shadow")
        self.root.geometry("920x700")
        self.root.minsize(760, 560)

        self.receiver: TelemetryReceiver | None = None
        self.agent: NeuralShadowAgent | None = None
        self.writer: NeuralShadowWriter | None = None
        self.running = False
        self.step = 0
        self.last_sequence: int | None = None

        self.status_var = tk.StringVar(value="Stopped")
        self.device_var = tk.StringVar(value="Device: —")
        self.step_var = tk.StringVar(value="Step: 0")
        self.room_var = tk.StringVar(value="Room: —")
        self.position_var = tk.StringVar(value="Position: —")
        self.action_var = tk.StringVar(value="Predicted action: —")
        self.confidence_var = tk.StringVar(value="Confidence: —")
        self.value_var = tk.StringVar(value="Value: —")
        self.source_var = tk.StringVar(value="Telemetry: waiting")
        self.feature_var = tk.StringVar(value="Features: —")
        self.recording_var = tk.StringVar(value="Recording: off")

        self.probability_vars = {
            name: tk.StringVar(value="—")
            for name in (
                "wait",
                "confirm",
                "cancel",
                "menu",
                "up",
                "down",
                "left",
                "right",
            )
        }

        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(self.POLL_MS, self._poll)

    def _build(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(
            header,
            text="Neural Shadow",
            font=("Segoe UI Semibold", 18),
        ).pack(side="left")
        ttk.Label(
            header,
            text="Numerical telemetry only • no input • vision OFF",
        ).pack(side="left", padx=(12, 0))
        ttk.Label(
            header,
            textvariable=self.status_var,
            font=("Segoe UI Semibold", 10),
        ).pack(side="right")

        controls = ttk.LabelFrame(outer, text="Controls", padding=8)
        controls.pack(fill="x", pady=(10, 8))

        ttk.Label(controls, text="Telemetry port:").pack(side="left")
        self.port_var = tk.StringVar(value="42069")
        ttk.Entry(controls, textvariable=self.port_var, width=8).pack(
            side="left", padx=(4, 10)
        )

        ttk.Label(controls, text="Checkpoint:").pack(side="left")
        self.checkpoint_var = tk.StringVar()
        ttk.Entry(controls, textvariable=self.checkpoint_var, width=34).pack(
            side="left", padx=(4, 4)
        )
        ttk.Button(
            controls,
            text="Browse",
            command=self._browse_checkpoint,
        ).pack(side="left", padx=(0, 8))

        ttk.Button(
            controls,
            text="Start shadow",
            command=self.start,
        ).pack(side="left", padx=3)
        ttk.Button(
            controls,
            text="Stop",
            command=self.stop,
        ).pack(side="left", padx=3)

        ttk.Label(
            controls,
            textvariable=self.device_var,
        ).pack(side="right", padx=(10, 0))

        status = ttk.LabelFrame(outer, text="Current prediction", padding=10)
        status.pack(fill="x", pady=(0, 8))

        ttk.Label(
            status,
            textvariable=self.action_var,
            font=("Segoe UI Semibold", 16),
        ).pack(anchor="w")
        ttk.Label(status, textvariable=self.confidence_var).pack(anchor="w")
        ttk.Label(status, textvariable=self.value_var).pack(anchor="w")
        ttk.Label(status, textvariable=self.source_var).pack(anchor="w", pady=(4, 0))

        meta = ttk.Frame(status)
        meta.pack(fill="x", pady=(8, 0))
        ttk.Label(meta, textvariable=self.step_var).pack(side="left")
        ttk.Label(meta, textvariable=self.room_var).pack(side="left", padx=(18, 0))
        ttk.Label(meta, textvariable=self.position_var).pack(side="left", padx=(18, 0))
        ttk.Label(meta, textvariable=self.feature_var).pack(side="right")

        body = ttk.Panedwindow(outer, orient="horizontal")
        body.pack(fill="both", expand=True)

        probability_frame = ttk.LabelFrame(body, text="Policy probabilities", padding=8)
        feature_frame = ttk.LabelFrame(body, text="Numerical observation", padding=8)
        body.add(probability_frame, weight=1)
        body.add(feature_frame, weight=2)

        for name, variable in self.probability_vars.items():
            row = ttk.Frame(probability_frame)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=name, width=10).pack(side="left")
            ttk.Label(row, textvariable=variable).pack(side="right")

        self.feature_text = tk.Text(
            feature_frame,
            wrap="none",
            font=("Consolas", 9),
            state="disabled",
        )
        feature_scroll = ttk.Scrollbar(
            feature_frame,
            orient="vertical",
            command=self.feature_text.yview,
        )
        self.feature_text.configure(yscrollcommand=feature_scroll.set)
        feature_scroll.pack(side="right", fill="y")
        self.feature_text.pack(side="left", fill="both", expand=True)

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(8, 0))
        ttk.Label(
            footer,
            textvariable=self.recording_var,
        ).pack(side="left")
        ttk.Label(
            footer,
            text="Shadow mode never calls KeyboardController.",
        ).pack(side="right")

    def _browse_checkpoint(self) -> None:
        path = filedialog.askopenfilename(
            title="Select neural checkpoint",
            filetypes=(
                ("PyTorch checkpoints", "*.pt *.pth"),
                ("All files", "*.*"),
            ),
        )
        if path:
            self.checkpoint_var.set(path)

    def start(self) -> None:
        if self.running:
            return
        try:
            import torch  # noqa: F401
        except ImportError as exc:
            messagebox.showerror(
                "PyTorch required",
                "Neural Shadow requires PyTorch. Install the project's "
                "optional neural-training dependency before starting it.",
            )
            return

        try:
            port = int(self.port_var.get())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid port", "Telemetry port must be 1–65535.")
            return

        checkpoint_text = self.checkpoint_var.get().strip()
        checkpoint = Path(checkpoint_text) if checkpoint_text else None
        if checkpoint is not None and not checkpoint.is_file():
            messagebox.showerror(
                "Checkpoint not found",
                f"No checkpoint exists at:\n{checkpoint}",
            )
            return

        try:
            self.agent = NeuralShadowAgent.create(checkpoint=checkpoint)
            self.receiver = TelemetryReceiver(port)
        except Exception as exc:
            self.agent = None
            if self.receiver is not None:
                self.receiver.close()
            self.receiver = None
            messagebox.showerror(
                "Could not start Neural Shadow",
                f"{type(exc).__name__}: {exc}",
            )
            return

        output_dir = Path("runs") / "neural-shadow"
        self.writer = NeuralShadowWriter(output_dir)
        self.step = 0
        self.last_sequence = None
        self.running = True
        self.status_var.set("Running")
        self.device_var.set(f"Device: {self.agent.device}")
        self.recording_var.set(
            f"Recording: {self.writer.predictions_path}"
        )
        self._clear_features()

    def stop(self) -> None:
        self.running = False
        if self.receiver is not None:
            self.receiver.close()
        self.receiver = None
        self.agent = None
        self.status_var.set("Stopped")

    def _poll(self) -> None:
        if self.running and self.receiver is not None and self.agent is not None:
            try:
                sample = self.receiver.poll()
                if sample is not None and sample.packet_sequence != self.last_sequence:
                    self.last_sequence = sample.packet_sequence
                    observation = self.agent.observe(sample)
                    prediction = self.agent.predict(observation)
                    self.step += 1
                    self._display(sample, observation, prediction)
                    if self.writer is not None:
                        self.writer.write_prediction(
                            step=self.step,
                            prediction={
                                **prediction,
                                "observation": observation.as_dict(),
                            },
                        )
            except Exception as exc:
                self.status_var.set(f"Error: {type(exc).__name__}")
                self._append_feature_error(exc)
                self.stop()

        self.root.after(self.POLL_MS, self._poll)

    def _display(self, sample, observation, prediction: dict[str, object]) -> None:
        action = str(prediction["action"])
        confidence = float(prediction["confidence"])
        value = float(prediction["value"])
        self.action_var.set(f"Predicted action: {action}")
        self.confidence_var.set(f"Confidence: {confidence:.1%}")
        self.value_var.set(f"Value estimate: {value:.5f}")
        self.step_var.set(f"Step: {self.step}")
        self.room_var.set(
            f"Room: {sample.room_name or sample.room_id}"
        )

        x = sample.player_foot_x
        y = sample.player_foot_y
        if x is None or y is None:
            x, y = sample.player_x, sample.player_y
        if x is None or y is None:
            self.position_var.set("Position: unavailable")
        else:
            self.position_var.set(f"Position: ({x:.1f}, {y:.1f})")

        self.source_var.set(
            f"Telemetry: mode={sample.mode or 'unknown'} "
            f"| sequence={sample.packet_sequence or '—'}"
        )
        self.feature_var.set(
            f"Features: {observation.width} + {observation.width} availability"
        )

        probabilities = prediction["probabilities"]
        for name, variable in self.probability_vars.items():
            variable.set(f"{float(probabilities[name]):.2%}")

        lines = []
        for name, value, available in zip(
            observation.feature_names,
            observation.values,
            observation.available,
        ):
            lines.append(f"{name:<30} {value:>10.5f}  available={int(available)}")
        self._set_features("\n".join(lines))

    def _set_features(self, text: str) -> None:
        self.feature_text.configure(state="normal")
        self.feature_text.delete("1.0", "end")
        self.feature_text.insert("1.0", text)
        self.feature_text.configure(state="disabled")

    def _clear_features(self) -> None:
        self._set_features("Waiting for telemetry...")

    def _append_feature_error(self, exc: Exception) -> None:
        self._set_features(
            f"Neural Shadow error: {type(exc).__name__}: {exc}\n"
            "The shadow agent was stopped without sending game input."
        )

    def close(self) -> None:
        self.stop()
        self.root.destroy()


def launch_neural_shadow_gui() -> None:
    root = tk.Tk()
    NeuralShadowGUI(root)
    root.mainloop()


if __name__ == "__main__":
    launch_neural_shadow_gui()
