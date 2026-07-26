import sys


if len(sys.argv) >= 2 and sys.argv[1] == "gui":
    from .integrated_gui import launch_integrated_gui

    launch_integrated_gui()
else:
    from .replay import print_replay, replay_run
    from .run19_runner import run
    from .runner import build_parser, listen

    args = build_parser().parse_args()
    if args.command == "run":
        run(args)
    elif args.command == "telemetry":
        listen(args)
    elif args.command == "replay":
        print_replay(
            replay_run(
                args.run_directory,
                args.visual_memory,
                save_report=not args.no_save,
            )
        )
    elif args.command == "gui":
        from .integrated_gui import launch_integrated_gui

        launch_integrated_gui()
