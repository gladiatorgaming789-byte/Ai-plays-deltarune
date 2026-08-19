"""Consequence gate for Battle System v2 menu learning."""

from __future__ import annotations

from .battle_v2 import BattleV2Controller


BATTLE_MENU_GUARD_VERSION = 1
_INSTALLED = False


def install_battle_v2_menu_guard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    original = BattleV2Controller._note_menu_result

    def guarded(controller, current_signature, soul) -> None:
        if controller.pending_signature is None or controller.pending_pattern is None:
            return
        # Moving a cursor legitimately changes the battle menu pixels. Do not
        # interpret that as a successful command while reset/navigation inputs
        # are still queued. A defensive SOUL appearing is stronger observed
        # evidence and may complete the trial immediately.
        if soul is None and controller.action_queue:
            return
        original(controller, current_signature, soul)

    BattleV2Controller._note_menu_result = guarded  # type: ignore[method-assign]
    _INSTALLED = True


__all__ = ["BATTLE_MENU_GUARD_VERSION", "install_battle_v2_menu_guard"]
