"""Activation guard for learned-control special gameplay.

The special-gameplay fallback is meant for a visible gameplay phase that stops
being represented by the normal telemetry stream. It must not hijack an
intentional visual-only/--no-telemetry run merely because the screen animates.
"""

from __future__ import annotations

from .special_gameplay import SpecialGameplayCoordinator


SPECIAL_GAMEPLAY_GUARD_VERSION = 1
_INSTALLED = False


def install_special_gameplay_guard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_choose = SpecialGameplayCoordinator.choose

    def choose(
        coordinator,
        frame,
        *,
        telemetry_present: bool,
        visual_valid: bool,
        state,
    ):
        if telemetry_present:
            coordinator._special_gameplay_had_telemetry = True
        elif not bool(
            getattr(coordinator, "_special_gameplay_had_telemetry", False)
        ):
            # Preserve the normal visual-only policy. A missing telemetry stream
            # is not itself evidence that DELTARUNE entered a special control
            # mode; require an earlier healthy stream before treating a gap as a
            # mode-change signal.
            coordinator.active = False
            coordinator.missing_telemetry_steps = 0
            coordinator.pending_action = None
            coordinator.pending_context = None
            coordinator.previous_frame = frame.copy() if visual_valid else None
            coordinator.reason = (
                "telemetry has not been observed in this run; keep normal "
                "visual-only policy instead of assuming special gameplay"
            )
            return None
        return original_choose(
            coordinator,
            frame,
            telemetry_present=telemetry_present,
            visual_valid=visual_valid,
            state=state,
        )

    SpecialGameplayCoordinator.choose = choose
    _INSTALLED = True


__all__ = [
    "SPECIAL_GAMEPLAY_GUARD_VERSION",
    "install_special_gameplay_guard",
]
