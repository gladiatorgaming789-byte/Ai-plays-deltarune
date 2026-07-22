from __future__ import annotations

from pathlib import Path

from .policy import WARP_PROGRESS_ATTRIBUTION_STEPS
from .run17_reinforcement import Run17ReinforcementExplorer


class Run18ReinforcementExplorer(Run17ReinforcementExplorer):
    """Correct reward accounting while preserving neutral candidate discovery."""

    def __init__(self, seed: int = 0, memory_path: Path | None = None):
        super().__init__(seed, memory_path)
        self._portal_return_counts = {
            portal_id: int(record.get("return_backtracks", 0) or 0)
            for portal_id, record in self.world.warp_portals.items()
        }

    def _sync_reward_totals(self) -> None:
        """Derive global totals from authoritative per-action records.

        Run 17 stored every direct and trace reward correctly on its action record,
        but its cached global total counted only trace events. Rebuilding the cache
        avoids double-counting and also repairs existing Run 17 memory files.
        """
        self.reinforcement.total_reward = sum(
            float(record.get("total_reward", 0.0) or 0.0)
            for record in self.reinforcement.records.values()
        )
        self.reinforcement.reward_events = sum(
            int(record.get("reward_count", 0) or 0)
            for record in self.reinforcement.records.values()
        )

    def _finish_active_interaction(self, telemetry) -> None:
        key = self.active_interaction_key
        super()._finish_active_interaction(telemetry)
        if key is None:
            return
        record = self.interactables.get(key)
        if record is None or int(record.get("confirmations", 0) or 0) != 1:
            return
        action_key = self._interaction_key(key)
        reward_record = self.reinforcement.records.get(action_key, {})
        if str(reward_record.get("last_event") or "") == "first confirmed interaction":
            return
        self._reward_key(
            action_key,
            "information_gain",
            event="first confirmed interaction",
            kind="interaction",
            context={"room": key[0], "x": key[1], "y": key[2]},
        )

    def _record_story_progress(self, event: str, telemetry) -> None:
        portal_id = self.last_portal_id
        portal_is_recent = bool(
            portal_id is not None
            and self.navigation_tick - self.last_portal_crossing_tick
            <= WARP_PROGRESS_ATTRIBUTION_STEPS
        )
        super()._record_story_progress(event, telemetry)
        if event == "discovered a new room" or not portal_is_recent or portal_id is None:
            return
        portal = self.world.portal_metadata(portal_id) or {}
        self._reward_key(
            self._portal_key(portal_id),
            "warp_progress",
            event=event,
            kind="portal",
            context={
                "from_room": portal.get("from_room"),
                "to_room": portal.get("to_room"),
                "action": portal.get("action"),
            },
        )

    def _observe_room(self, telemetry) -> None:
        super()._observe_room(telemetry)
        for portal_id, record in self.world.warp_portals.items():
            current = int(record.get("return_backtracks", 0) or 0)
            previous = self._portal_return_counts.get(portal_id, 0)
            if current > previous:
                for _ in range(current - previous):
                    self._reward_key(
                        self._portal_key(portal_id),
                        "immediate_backtrack",
                        event="observed return through paired portal",
                        kind="portal",
                        context={
                            "from_room": record.get("from_room"),
                            "to_room": record.get("to_room"),
                            "action": record.get("action"),
                        },
                    )
            self._portal_return_counts[portal_id] = current

    def save_memory(self) -> None:
        self._sync_reward_totals()
        super().save_memory()

    def summary(self) -> dict:
        self._sync_reward_totals()
        return super().summary()
