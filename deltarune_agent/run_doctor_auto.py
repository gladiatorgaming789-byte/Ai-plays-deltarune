"""Failure-isolated automatic post-run execution for Run Doctor v1.0."""

from __future__ import annotations

from functools import wraps
import json
from pathlib import Path
from typing import Any, Type

from . import run_doctor_release


def _write_failure(directory: Path, exc: BaseException) -> None:
    """Best-effort diagnostic only; never replace the original run result."""
    try:
        (directory / "run_doctor_error.json").write_text(
            json.dumps(
                {
                    "doctor_version": run_doctor_release.RUN_DOCTOR_VERSION,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "run_preserved": True,
                },
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    except (OSError, UnicodeError, TypeError, ValueError):
        pass


def analyze_finished_run(directory: Path) -> None:
    """Create the trusted report for one already-finalized run directory."""
    run_doctor_release.analyze_and_write(Path(directory))


def _tracker_directory(tracker: Any, result: Any) -> Path | None:
    value = getattr(tracker, "directory", None)
    if value is not None:
        try:
            return Path(value)
        except TypeError:
            return None
    if result is None:
        return None
    try:
        return Path(result).parent
    except TypeError:
        return None


def install_post_run_hook(tracker_class: Type[Any] | None = None) -> Type[Any]:
    """Install exactly one failure-isolated ``finish`` wrapper.

    ``tracker_class`` exists to make lifecycle behavior directly testable. The
    production path omits it and patches :class:`progress.EpisodeTracker`.
    """
    if tracker_class is None:
        from .progress import EpisodeTracker

        tracker_class = EpisodeTracker

    if getattr(tracker_class, "_automatic_run_doctor_v1_installed", False):
        return tracker_class

    original_finish = tracker_class.finish

    @wraps(original_finish)
    def finish_with_doctor(self, *args, **kwargs):
        result = original_finish(self, *args, **kwargs)
        if getattr(self, "_automatic_run_doctor_v1_complete", False):
            return result

        # Resolve the directory after normal finalization, and never let a
        # missing/unusual return value become a new post-run failure.
        directory = _tracker_directory(self, result)
        if directory is None:
            return result

        try:
            analyze_finished_run(directory)
        except BaseException as exc:  # Doctor must never invalidate run cleanup.
            _write_failure(directory, exc)
        else:
            setattr(self, "_automatic_run_doctor_v1_complete", True)
            try:
                (directory / "run_doctor_error.json").unlink(missing_ok=True)
            except OSError:
                pass
        return result

    tracker_class.finish = finish_with_doctor
    tracker_class._automatic_run_doctor_v1_installed = True
    return tracker_class


__all__ = [
    "analyze_finished_run",
    "install_post_run_hook",
]
