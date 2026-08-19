"""Gameplay-run wrapper that enables Automatic Run Doctor v1.0."""

from __future__ import annotations

from .run_doctor_auto import install_post_run_hook


def run(args) -> None:
    if bool(getattr(args, "training", False)):
        from .multi_instance_training_release import run_multi_instance_training

        run_multi_instance_training(args)
        return
    # Install capture/telemetry alignment before the concrete runner creates its
    # observer, receiver, and hierarchical policy. Independent-training workers
    # reach this same non-training path, so every game process gets identical
    # visual-evidence protection.
    from .frame_telemetry_sync import install_frame_telemetry_sync

    install_frame_telemetry_sync()
    # Replace the old battle reflex before HierarchicalPolicy is instantiated.
    # v2 learns visible menu transitions and derives SOUL control mode from the
    # rendered battle state rather than enemy names or mixed overworld telemetry.
    from .battle_v2 import install_battle_v2

    install_battle_v2()
    # Install before the runner creates EpisodeTracker. The hook runs only after
    # EpisodeTracker.finish has successfully completed its normal artifacts.
    install_post_run_hook()
    from .run19_runner import run as gameplay_run

    gameplay_run(args)


__all__ = ["run"]
