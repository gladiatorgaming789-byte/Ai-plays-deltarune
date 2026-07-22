from __future__ import annotations

from .run16_explorer import Run16Explorer
from .run16_semantics import CARDINAL_DIRECTIONS


class Run16GuardedExplorer(Run16Explorer):
    """Prevent automatic scene transitions from older learned-warp routing."""

    def _route_to_learned_warp(
        self,
        room: str,
        start: tuple[int, int],
    ):
        route = super()._route_to_learned_warp(room, start)
        if route is None:
            return None
        direction, warp = route
        metadata = self.world.portal_metadata(warp)
        role = str(metadata.get("role") or "") if metadata else ""
        if warp[3] not in CARDINAL_DIRECTIONS or role == "automatic_sequence":
            self.automatic_warps_deprioritized += 1
            return None
        return direction, warp
