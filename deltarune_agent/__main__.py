import sys


if len(sys.argv) >= 2 and sys.argv[1] == "gui":
    from .profile_launcher import launch_profile_launcher

    launch_profile_launcher()
else:
    from .runner import main

    main()
