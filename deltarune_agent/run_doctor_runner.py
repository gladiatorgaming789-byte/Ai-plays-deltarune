"""Gameplay-run wrapper that enables Automatic Run Doctor v1.0."""

from __future__ import annotations

from pathlib import Path

from .run_doctor_auto import install_post_run_hook


def _validate_population_support(args) -> None:
    """Fail early with actionable data.win repair instructions."""

    if not bool(getattr(args, "training", False)):
        return

    from .support_installation import format_guidance, inspect_installation

    chapter = int(getattr(args, "chapter", 1) or 1)
    explicit_root = getattr(args, "game_root", None)
    status = inspect_installation(
        Path(explicit_root) if explicit_root else None,
        chapters=range(1, 6),
    )
    if status.healthy:
        return

    # Keep the preflight focused on the chapter the requested population run
    # will actually use, while still showing the other stale/missing chapters
    # so the operator can repair the whole installation in one pass.
    chapter_status = next(
        (item for item in status.chapters if item.chapter == chapter),
        None,
    )
    print("[Runtime] Population Training preflight: AI Support installation is not ready.")
    print(f"[Runtime] Requested chapter: {chapter}")
    if chapter_status is not None and chapter_status.missing_markers:
        print(
            "[Runtime] Requested chapter is missing: "
            + ", ".join(chapter_status.missing_markers)
        )
    print("[Runtime] " + format_guidance(status).replace("\n", "\n[Runtime] "))
    raise RuntimeError(
        "Population Training cannot start until the current AI Support package "
        "has been re-imported into the affected Deltarune data.win files. "
        "See the [Runtime] repair instructions above."
    )


def run(args) -> None:
    _validate_population_support(args)
    if bool(getattr(args, "training", False)):
        from .multi_instance_training_release import run_multi_instance_training

        run_multi_instance_training(args)
        return
    # Independent-training workers reach this same non-training path, so every
    # game receives the exact same versioned runtime extension order.
    from .production_runtime import install_production_runtime

    install_production_runtime()
    # Install before the runner creates EpisodeTracker. The hook runs only after
    # EpisodeTracker.finish has successfully completed its normal artifacts.
    install_post_run_hook()
    from .run19_runner import run as gameplay_run

    gameplay_run(args)


__all__ = ["run"]
