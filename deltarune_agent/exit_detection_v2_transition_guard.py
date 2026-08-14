"""Cardinal-crossing guard for Exit Detection v2 confirmation.

The base policy observes both player-crossed room changes and scripted/automatic
room changes. Only a cardinal overworld movement crossing is evidence that a
nearby visual candidate behaved like a traversable exit. Dialogue, cutscene,
menu, and interaction handling clears ``last_movement`` before their room
changes are observed, so this guard uses that already-observed action state
without introducing any game-specific route knowledge.

The guard wraps the entire visual-exit confirmation method, not only the V2
fallback helper. This prevents the older semantic-ready confirmation path from
crediting nearby scenery during scripted room changes.
"""

from __future__ import annotations

from .policy import StarterPolicy


CARDINAL_MOVEMENTS = {"up", "down", "left", "right"}
_INSTALLED = False
_ORIGINAL_CONFIRM_VISUAL_EXIT = None


def movement_crossing_is_confirmable(self: StarterPolicy) -> bool:
    return str(getattr(self, "last_movement", "") or "") in CARDINAL_MOVEMENTS


def _guarded_confirm_visual_exit(
    self: StarterPolicy,
    room: str,
    source_cell: tuple[int, int],
    target_room: str,
) -> None:
    if not movement_crossing_is_confirmable(self):
        return
    assert _ORIGINAL_CONFIRM_VISUAL_EXIT is not None
    _ORIGINAL_CONFIRM_VISUAL_EXIT(self, room, source_cell, target_room)


def install_exit_detection_v2_transition_guard() -> None:
    global _INSTALLED, _ORIGINAL_CONFIRM_VISUAL_EXIT
    if _INSTALLED:
        return
    # Installed after exit_detection_v2_confirmation, so the captured method is
    # the complete legacy + V2 confirmation chain. Non-cardinal room changes
    # bypass that chain entirely while normal transition recording still runs.
    _ORIGINAL_CONFIRM_VISUAL_EXIT = StarterPolicy._confirm_visual_exit
    StarterPolicy._confirm_visual_exit = _guarded_confirm_visual_exit  # type: ignore[method-assign]
    _INSTALLED = True


__all__ = [
    "install_exit_detection_v2_transition_guard",
    "movement_crossing_is_confirmable",
]
