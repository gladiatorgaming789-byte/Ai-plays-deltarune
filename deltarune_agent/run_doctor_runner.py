"""Gameplay-run wrapper that enables Automatic Run Doctor v1.0."""

from __future__ import annotations

from .run_doctor_auto import install_post_run_hook


def run(args) -> None:
    if bool(getattr(args, "training", False)):
        from .multi_instance_training import run_multi_instance_training

        run_multi_instance_training(args)
        return
    # Install before the runner creates EpisodeTracker. The hook runs only after
    # EpisodeTracker.finish has successfully completed its normal artifacts.
    install_post_run_hook()
    from .run19_runner import run as gameplay_run

    gameplay_run(args)


__all__ = ["run"]
