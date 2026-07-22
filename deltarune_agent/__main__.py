import sys


if len(sys.argv) >= 2 and sys.argv[1] == "gui":
    from .integrated_gui import launch_integrated_gui

    launch_integrated_gui()
else:
    from .runner import main

    main()
