from __future__ import annotations

from pathlib import Path

from .entity_detection_v2 import single_side_entity_candidate
from .run21_link_cooldowns import Run21CooldownExplorer


_ROUTE_ONLY_FAILURE_MARKERS = (
    "route",
    "no safe learned approach",
    "loop",
    "made no progress",
)


class Run21Explorer(Run21CooldownExplorer):
    """Final Run21 migration policy after the eight-run calibration."""

    def __init__(self, seed: int = 0, memory_path: Path | None = None):
        super().__init__(seed, memory_path)
        self.reopened_legacy_route_only_entity_candidates = 0
        self._reopen_legacy_route_only_entity_candidates()

    def _reopen_legacy_route_only_entity_candidates(self) -> None:
        """Do not confuse an old routing failure with a failed interaction test."""
        for key, record in self.screen_regions.items():
            if not single_side_entity_candidate(record):
                continue
            if int(record.get("completed_tests", record.get("inspections", 0)) or 0) > 0:
                continue
            if int(record.get("failed_approaches", 0) or 0) <= 0:
                continue
            reason = str(record.get("last_failure_reason") or "").casefold()
            if not any(marker in reason for marker in _ROUTE_ONLY_FAILURE_MARKERS):
                continue

            record["failed_approaches"] = 0
            if str(record.get("guess_state") or "") in {"rejected", "retired"}:
                record["guess_state"] = "cooldown"
            record["entity_route_retry_migrated"] = True
            record["entity_route_retry_reason"] = (
                "legacy route-only failure reopened for one bounded concrete test"
            )
            self._refresh_visual_guess_metadata((key[1], key[2]), record)
            self.map_updates.append(self._screen_region_map_update(key, record))
            self.reopened_legacy_route_only_entity_candidates += 1

    def _weak_entity_probe_count(self, room: str) -> int:
        # Per-candidate lifecycle rules already bound cost: one-sided candidates
        # are nearby-only, approach-bounded, and rejected by a concrete no-response
        # test. A lifetime room cap could permanently skip a valid candidate just
        # because several scenery candidates were encountered first.
        return 0

    def summary(self) -> dict:
        summary = super().summary()
        summary["reopened_legacy_route_only_entity_candidates"] = (
            self.reopened_legacy_route_only_entity_candidates
        )
        return summary


__all__ = ["Run21Explorer"]
