from __future__ import annotations

from pathlib import Path

from .policy import DIRECTION_VECTORS
from .run9_explorer import (
    ANIMATED_EVIDENCE_NOTE,
    PINCH_MIN_ALTERNATIONS,
    PINCH_RECENT_CELLS,
    Run9Explorer,
)
from .telemetry import TelemetrySample


WIDE_PINCH_MAX_GAP_CELLS = 2
PLAYER_MOTION_MASK_WORLD = 10.0
MOTION_ONLY_CHARACTER_LIMIT = 0.42


class Run13Explorer(Run9Explorer):
    """Run-nine explorer with fixes learned from the bedroom failure run.

    The latest screenshots and telemetry exposed two independent problems:

    * Kris alternated between two positions separated by one unsampled cell. The
      run-nine pinch detector required adjacent cells, so the same up/down wall
      loop continued for hundreds of decisions.
    * A fixed camera plus Kris moving through a region changed that region's
      image signature. Nearby furniture and floor seams were then credited as
      animated character evidence.

    This layer accepts narrow two-position pinches with a one-cell sampling gap
    and masks player-overlapping regions from same-view animation evidence.
    """

    def __init__(self, seed: int = 0, memory_path: Path | None = None):
        super().__init__(seed, memory_path)
        self.wide_pinch_recoveries = 0
        self.player_motion_regions_ignored = 0
        self.retired_motion_only_characters = 0
        self._retire_motion_only_character_memory()

    def _pinch_pattern(
        self,
        room: str,
    ) -> tuple[frozenset[tuple[int, int]], set[str], tuple[str, ...]] | None:
        recent = list(self.recent_cells)[-PINCH_RECENT_CELLS:]
        if len(recent) < PINCH_MIN_ALTERNATIONS:
            return None
        if any(recent_room != room for recent_room, _x, _y in recent):
            return None
        cells = [(x, y) for _recent_room, x, y in recent]
        unique = set(cells)
        if len(unique) != 2:
            return None
        if any(left == right for left, right in zip(cells, cells[1:])):
            return None

        first, second = sorted(unique)
        delta_x = abs(first[0] - second[0])
        delta_y = abs(first[1] - second[1])
        if delta_x and delta_y:
            return None
        gap = delta_x + delta_y
        if not (1 <= gap <= WIDE_PINCH_MAX_GAP_CELLS):
            return None

        if delta_y:
            top, bottom = sorted(unique, key=lambda cell: cell[1])
            if not (
                self._blocked_near(room, top, "up")
                and self._blocked_near(room, bottom, "down")
            ):
                return None
            if gap > 1:
                self.wide_pinch_recoveries += 1
            return frozenset(unique), {"up", "down"}, ("left", "right")

        left_cell, right_cell = sorted(unique, key=lambda cell: cell[0])
        if not (
            self._blocked_near(room, left_cell, "left")
            and self._blocked_near(room, right_cell, "right")
        ):
            return None
        if gap > 1:
            self.wide_pinch_recoveries += 1
        return frozenset(unique), {"left", "right"}, ("up", "down")

    @staticmethod
    def _region_overlaps_player(
        key: tuple[str, int, int],
        telemetry: TelemetrySample,
    ) -> bool:
        values = (
            telemetry.player_bbox_left,
            telemetry.player_bbox_top,
            telemetry.player_bbox_right,
            telemetry.player_bbox_bottom,
        )
        if any(value is None for value in values):
            return False
        left, top, right, bottom = (float(value) for value in values)
        left -= PLAYER_MOTION_MASK_WORLD
        top -= PLAYER_MOTION_MASK_WORLD
        right += PLAYER_MOTION_MASK_WORLD
        bottom += PLAYER_MOTION_MASK_WORLD
        region_left = key[1] * 32
        region_top = key[2] * 32
        region_right = region_left + 32
        region_bottom = region_top + 32
        return not (
            right <= region_left
            or left >= region_right
            or bottom <= region_top
            or top >= region_bottom
        )

    def _update_same_view_motion(
        self,
        observation,
        telemetry: TelemetrySample,
    ) -> None:
        if not observation.visual_valid or observation.step % 5:
            return
        room = self._room_key(telemetry)
        camera_x = int(round(float(telemetry.camera_x or 0.0)))
        camera_y = int(round(float(telemetry.camera_y or 0.0)))
        for key, record in self.screen_regions.items():
            if key[0] != room or int(record.get("last_seen_step", -1)) != observation.step:
                continue
            signature = str(record.get("last_signature") or "")
            if not signature:
                continue
            viewpoint_key = (room, key[1], key[2], camera_x, camera_y)
            previous = self._viewpoint_signatures.get(viewpoint_key)
            if self._region_overlaps_player(key, telemetry):
                self._viewpoint_signatures[viewpoint_key] = signature
                self.player_motion_regions_ignored += 1
                continue
            if previous is not None and previous != signature:
                record["motion"] = float(record.get("motion", 0.0) or 0.0) + 1.0
                self.same_view_motion_updates += 1
            self._viewpoint_signatures[viewpoint_key] = signature

    @staticmethod
    def _boxes_overlap(
        first: object,
        second: object,
        *,
        margin: float = 4.0,
    ) -> bool:
        if not (
            isinstance(first, (list, tuple))
            and len(first) == 4
            and isinstance(second, (list, tuple))
            and len(second) == 4
        ):
            return True
        try:
            left_a, top_a, right_a, bottom_a = (float(value) for value in first)
            left_b, top_b, right_b, bottom_b = (float(value) for value in second)
        except (TypeError, ValueError):
            return True
        return not (
            right_a + margin < left_b
            or left_a - margin > right_b
            or bottom_a + margin < top_b
            or top_a - margin > bottom_b
        )

    def _retire_motion_only_character_memory(self) -> None:
        """Downgrade screenshot bonuses that were spatially detached from collisions."""
        for key, record in self.screen_regions.items():
            if record.get("hypothesis") != "possible_character":
                continue
            if not record.get("animated_sprite_evidence"):
                continue
            if self._boxes_overlap(
                record.get("visual_box_world") or record.get("feature_box_world"),
                record.get("obstruction_box_world"),
            ):
                continue
            record["animated_sprite_evidence"] = False
            record["motion"] = 0.0
            evidence = str(record.get("evidence_summary") or "")
            evidence = evidence.replace(f"; {ANIMATED_EVIDENCE_NOTE}", "").replace(
                ANIMATED_EVIDENCE_NOTE,
                "",
            ).strip("; ")
            record["evidence_summary"] = evidence
            record["guess_confidence"] = min(
                MOTION_ONLY_CHARACTER_LIMIT,
                float(record.get("guess_confidence", 0.0) or 0.0),
            )
            record["guess_state"] = "cooldown"
            record["cooldown_until_tick"] = 72
            record["last_failure_reason"] = (
                "screenshot motion did not overlap the collision-backed obstruction"
            )
            self.retired_motion_only_characters += 1
            self.map_updates.append(self._screen_region_map_update(key, record))

    def summary(self) -> dict:
        summary = super().summary()
        summary["wide_pinch_recoveries"] = self.wide_pinch_recoveries
        summary["player_motion_regions_ignored"] = self.player_motion_regions_ignored
        summary["retired_motion_only_characters"] = self.retired_motion_only_characters
        return summary
