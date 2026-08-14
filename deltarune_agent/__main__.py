from __future__ import annotations

import argparse
from pathlib import Path
import sys


def _parse_gui_args(argv: list[str]) -> argparse.Namespace:
    """Parse the GUI-only options without importing the gameplay runtime.

    The gameplay runner imports PyAutoGUI, which sets a Windows DPI-awareness
    mode during import. Loading it before QApplication prevents Qt from choosing
    its default Per-Monitor V2 context and produces an ACCESS_DENIED warning.
    Keeping the GUI route lightweight lets Qt initialize first; the runtime is
    still loaded later by the GUI's child process when an agent run starts.
    """

    parser = argparse.ArgumentParser(
        prog=f"{sys.executable} -m deltarune_agent gui",
        description="Open the Deltarune AI desktop controller.",
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="launch the transitional Tk interface instead of the Qt console",
    )
    return parser.parse_args(argv)


def _auto_update_gui(relaunch_args: list[str]) -> None:
    """Update a clean Git checkout before importing either GUI toolkit."""

    from .auto_update import maybe_auto_update

    project_root = Path(__file__).resolve().parents[1]
    maybe_auto_update(project_root, relaunch_args)


def _launch_gui(argv: list[str]) -> None:
    args = _parse_gui_args(argv)
    if args.legacy:
        from .integrated_gui import launch_integrated_gui

        launch_integrated_gui()
    else:
        from .qt_ui import launch_qt_gui

        raise SystemExit(launch_qt_gui())


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "gui":
        _auto_update_gui(argv)
        _launch_gui(argv[1:])
        return
    if argv and argv[0] == "run-doctor":
        # Trusted v1.0 stays on the lightweight, read-only artifact path.
        from .run_doctor_release import cli

        raise SystemExit(cli(argv[1:]))

    # Non-GUI gameplay commands may load the full runtime, including PyAutoGUI.
    from .runner import build_parser

    args = build_parser().parse_args(argv)
    if args.command == "run":
        # Standard gameplay runs install the failure-isolated automatic Doctor
        # hook before EpisodeTracker is created.
        from .run_doctor_runner import run

        run(args)
    elif args.command == "telemetry":
        from .runner import listen

        listen(args)
    elif args.command == "replay":
        from .replay import print_replay, replay_run

        print_replay(
            replay_run(
                args.run_directory,
                args.visual_memory,
                save_report=not args.no_save,
            )
        )


if __name__ == "__main__":
    main()
