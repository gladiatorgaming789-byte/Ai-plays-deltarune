"""Upgrade the v0.5 Runs-page extension to the trusted v1.0 engine."""

from __future__ import annotations

from pathlib import Path

from .. import run_doctor_release
from . import run_doctor_extension as v05


RUN_DOCTOR_GUI_VERSION = "1.0.0"


def _trusted_task_run(self) -> None:
    try:
        payload, _paths = run_doctor_release.analyze_and_write(
            Path(self.candidate),
            baseline_path=Path(self.baseline) if self.baseline is not None else None,
        )
        self.signals.loaded.emit(str(self.candidate), payload, "")
    except Exception as exc:
        self.signals.loaded.emit(
            str(self.candidate),
            {},
            f"{type(exc).__name__}: {exc}",
        )


def install_runs_page_extension() -> None:
    """Install v0.5 UI controls, then route tasks through trusted v1.0."""
    v05.install_runs_page_extension()
    if getattr(v05._DoctorTask, "_trusted_v1_installed", False):
        return
    v05._DoctorTask.run = _trusted_task_run
    v05._DoctorTask._trusted_v1_installed = True


__all__ = ["RUN_DOCTOR_GUI_VERSION", "install_runs_page_extension"]
