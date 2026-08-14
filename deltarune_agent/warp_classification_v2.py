"""Warp classification v2 planner integration.

The semantic classifier lives in navigation_semantics. This installer updates
Run4Explorer's learned-warp eligibility so return/loop behavior is temporary
navigation evidence rather than a permanent statement about route meaning.

Only outcomes the agent has observed are used. No room, route, dialogue, or
progression answer is embedded here.
"""

from __future__ import annotations

from .run4_explorer import (
    MAX_FLAVOR_INTERACTIONS_BEFORE_EXIT,
    ROOM_EXIT_PRIORITY_MIN_CELLS,
    ROOM_EXIT_PRIORITY_STEPS,
    ROOM_EXIT_PRIORITY_STORY_STALL,
    Run4Explorer,
)
from .world_model import Warp


WARP_CLASSIFICATION_PLANNER_VERSION = 2
_INSTALLED = False


def _room_completion_pressure(explorer: Run4Explorer, room: str) -> bool:
    """Return whether observed behavior justifies retesting cautious exits."""

    if explorer._room_flavor_count(room) >= MAX_FLAVOR_INTERACTIONS_BEFORE_EXIT:
        return True
    if explorer.story_stall_steps >= ROOM_EXIT_PRIORITY_STORY_STALL:
        return True
    return (
        explorer._room_navigation_age(room) >= ROOM_EXIT_PRIORITY_STEPS
        and explorer._room_seen_cell_count(room) >= ROOM_EXIT_PRIORITY_MIN_CELLS
    )


def _warp_is_priority_candidate_v2(self: Run4Explorer, warp: Warp) -> bool:
    source_room, _x, _y, _action, target_room, _tx, _ty = warp

    # Existing link cooldowns remain authoritative for short-term anti-bounce
    # behavior. This is deliberately separate from semantic classification.
    if self._link_is_cooling_down(source_room, target_room):
        return False

    metadata = self.world.portal_metadata(warp)
    role = str(metadata.get("role") or "unknown") if metadata else "unknown"

    # Positive observed progress is the strongest evidence. A progression warp
    # remains eligible even if it was previously used to backtrack or loop.
    if role == "progression":
        return True

    recovery_pressure = _room_completion_pressure(self, source_room)

    # The portal back to the room we just entered from is initially avoided to
    # prevent an immediate A->B->A bounce. Once exploration has genuinely
    # stalled, however, the already learned warp becomes a valid recovery option
    # again. This does not assert that taking it is the correct story route.
    if self.room_entry_from.get(source_room) == target_room and not recovery_pressure:
        return False

    # Repeated loop evidence is a safety hold, not permanent semantic meaning.
    # Strong room-completion pressure permits one bounded reconsideration.
    if role == "loop_suppressed" and not recovery_pressure:
        return False

    # v2 intentionally keeps unknown/new-area routes eligible even when their
    # metadata contains return-prone behavior tags. Return behavior is not proof
    # of optionality.
    return True


def install_warp_classification_v2() -> None:
    """Install v2 learned-warp eligibility once for all derived explorers."""

    global _INSTALLED
    if _INSTALLED:
        return
    Run4Explorer._warp_is_priority_candidate = _warp_is_priority_candidate_v2  # type: ignore[method-assign]
    _INSTALLED = True


__all__ = [
    "WARP_CLASSIFICATION_PLANNER_VERSION",
    "install_warp_classification_v2",
]
