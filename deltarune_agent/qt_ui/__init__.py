"""Optional PySide6 operator console.

The package deliberately keeps its public import cheap. Projects that only use
the command-line runner must continue to work when the sizeable Qt dependency is
not installed.
"""

from __future__ import annotations

from importlib.util import find_spec


QT_AVAILABLE = find_spec("PySide6") is not None


def launch_qt_gui() -> int:
    """Launch the Qt operator console, or explain how to enable it."""

    if not QT_AVAILABLE:
        raise RuntimeError(
            "The Qt GUI needs PySide6. Install the project requirements and "
            "try again, or run `python -m deltarune_agent gui --legacy`."
        )

    # Install the Runs-page extension before app.py imports page classes. v1.0
    # retains the v0.5 UI while routing analysis through the trusted engine.
    from .run_doctor_v1_extension import install_runs_page_extension

    install_runs_page_extension()

    from .app import launch_qt_gui as _launch

    return _launch()


__all__ = ["QT_AVAILABLE", "launch_qt_gui"]
