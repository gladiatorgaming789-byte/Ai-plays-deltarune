"""Cardinal-crossing guard for Exit Detection v2 confirmation.

The base policy observes both player-crossed room changes and scripted/automatic
room changes.  Only a cardinal overworld movement crossing is evidence that a
nearby visual candidate behaved like a traversable exit.  Dialogue, cutscene,
menu, and interaction handling clears ``last_movement`` before their room
changes are observed, so this guard uses that already-observed action state
without introducing any game-specific route knowledge.
"""

from __future__ import annotations

from . import exit_detection_v2_confirmation as confirmation
from .policy import StarterPolicy


CARDINAL_MOVEMENTS = {"up", "down", "left", "right"}
_INSTALLED = False
_ORIGINAL_CONFIRM_CANDIDATE = None


def movement_crossing_is_confirmable(self: StarterPolicy) -> bool:
    return str(getattr(self, "last_movement", "") or "") in CARDINAL_MOVEMENTS


def _guarded_confirm_candidate_from_transition(
    self: StarterPolicy,
    room: str,
    source_cell: tuple[int, int],
    target_room: str,
):
    if not movement_crossing_is_confirmable(self):
        return None
    assert _ORIGINAL_CONFIRM_CANDIDATE is not None
    return _ORIGINAL_CONFIRM_CANDIDATE(self, room, source_cell, target_room)


def install_exit_detection_v2_transition_guard() -> None:
    global _INSTALLED, _ORIGINAL_CONFIRM_CANDIDATE
    if _INSTALLED:
        return
    _ORIGINAL_CONFIRM_CANDIDATE = confirmation.confirm_candidate_from_transition
    confirmation.confirm_candidate_from_transition = _guarded_confirm_candidate_from_transition
    _INSTALLED = True


__all__ = [
    "install_exit_detection_v2_transition_guard",
    "movement_crossing_is_confirmable",
]
