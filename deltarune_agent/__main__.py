from .runner import build_parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "run":
        from .run19_runner import run

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
    elif args.command == "gui":
        if args.legacy:
            from .integrated_gui import launch_integrated_gui

            launch_integrated_gui()
        else:
            from .qt_ui import launch_qt_gui

            raise SystemExit(launch_qt_gui())


if __name__ == "__main__":
    main()
