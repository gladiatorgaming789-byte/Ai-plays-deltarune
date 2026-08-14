"""Order-safe installer for Guessing v3.

Guessing v3 can be imported by tests and developer tools before Run16 installs
its persistence extensions. Capture the methods to wrap at *installation* time
so an early import can never make the production installer bypass a newer
persistence/planner layer.
"""

from __future__ import annotations

from . import guessing_v3 as v3
from .policy import StarterPolicy
from .run4_explorer import Run4Explorer
from .world_model import WorldModel


_INSTALLED = False


def install_guessing_v3() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    # If a developer/test deliberately used the low-level installer already,
    # respect that installation instead of rebinding its delegates to the v3
    # wrappers themselves and creating recursion.
    if getattr(v3, "_INSTALLED", False):
        _INSTALLED = True
        return

    # Rebind the v3 wrapper delegates to whatever implementations are active
    # right now. In normal startup this is after Run16 semantics and warp v2.
    v3._ORIGINAL_REFRESH = StarterPolicy._refresh_visual_guess_metadata
    v3._ORIGINAL_MAP_UPDATE = StarterPolicy._screen_region_map_update
    v3._ORIGINAL_RUN4_PLAN = Run4Explorer._plan_exploration
    v3._ORIGINAL_RUN4_SUMMARY = Run4Explorer.summary
    v3._ORIGINAL_WORLD_SAVE = WorldModel.save
    v3._ORIGINAL_WORLD_LOAD = WorldModel.load.__func__

    v3.install_guessing_v3()
    _INSTALLED = True


__all__ = ["install_guessing_v3"]
