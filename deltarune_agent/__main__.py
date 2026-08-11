from pathlib import Path
import sys


def _auto_update_gui(relaunch_args: list[str]) -> None:
    from .auto_update import maybe_auto_update

    project_root = Path(__file__).resolve().parents[1]
    maybe_auto_update(project_root, relaunch_args)


if len(sys.argv) >= 2 and sys.argv[1] == "gui":
    _auto_update_gui(sys.argv[1:])
    from .integrated_gui import launch_integrated_gui

    launch_integrated_gui()
else:
    from .runner import main

    main()
