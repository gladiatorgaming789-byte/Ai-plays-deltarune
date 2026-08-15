from __future__ import annotations

from pathlib import Path

from .run21_multirun_fixes import Run21MultiRunExplorer
from .warp_classification_v2 import _room_completion_pressure


class Run21CooldownExplorer(Run21MultiRunExplorer):
    """Reconcile old blanket link cooldowns with newer recovery pressure."""

    def __init__(self, seed: int = 0, memory_path: Path | None = None):
        self.legacy_link_cooldown_pressure_overrides = 0
        self._legacy_override_links: set[frozenset[str]] = set()
        super().__init__(seed, memory_path)

    def _run21_link_hold_active(self, room: str, target_room: str) -> bool:
        link = frozenset((room, target_room))
        cooldowns = getattr(self, "_room_link_cooldown_until", {})
        expiry = int(cooldowns.get(link, 0) or 0)
        if expiry <= getattr(self, "navigation_tick", 0):
            cooldowns.pop(link, None)
            return False
        return True

    def _link_is_cooling_down(self, room: str, target_room: str) -> bool:
        # Keep Run21's short repeated-link hold authoritative.
        if self._run21_link_hold_active(room, target_room):
            return True

        legacy = super()._link_is_cooling_down(room, target_room)
        if not legacy:
            return False

        try:
            pressure = _room_completion_pressure(self, room)
        except AttributeError:
            pressure = False
        if not pressure:
            return True

        link = frozenset((room, target_room))
        if link not in self._legacy_override_links:
            self._legacy_override_links.add(link)
            self.legacy_link_cooldown_pressure_overrides += 1
        return False

    def summary(self) -> dict:
        summary = super().summary()
        summary["legacy_link_cooldown_pressure_overrides"] = (
            self.legacy_link_cooldown_pressure_overrides
        )
        return summary


__all__ = ["Run21CooldownExplorer"]
