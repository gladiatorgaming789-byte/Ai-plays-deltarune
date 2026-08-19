"""Explicit installation order for production runtime extensions.

Older Run2-Run21 classes remain the learned-history inheritance foundation for
now. New cross-cutting systems are composed here so entry points cannot silently
install different subsets or orders.
"""

from __future__ import annotations


RUNTIME_STACK_VERSION = 1
_INSTALLED = False


def install_production_runtime() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    # HierarchicalPolicy is imported by frame synchronization. Install temporal
    # evidence protection first, then the battle controller factory plus its
    # visual-localization precision layer, then telemetry-gap control learning.
    from .frame_telemetry_sync import install_frame_telemetry_sync

    install_frame_telemetry_sync()

    from .battle_v2 import install_battle_v2

    install_battle_v2()

    from .battle_v2_components import install_battle_v2_components

    install_battle_v2_components()

    from .special_gameplay import install_special_gameplay

    install_special_gameplay()
    _INSTALLED = True


__all__ = ["RUNTIME_STACK_VERSION", "install_production_runtime"]
