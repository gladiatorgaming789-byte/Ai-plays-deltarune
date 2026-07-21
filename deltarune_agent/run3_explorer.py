from __future__ import annotations

from collections import Counter
from pathlib import Path

from .run2_explorer import ROOM_LINK_COOLDOWN_STEPS, Run2Explorer
from .telemetry import TelemetrySample
from .world_model import Warp


MAX_LINK_BACKOFF_MULTIPLIER = 4
LOCAL_LEAD_TEST_LIMIT = 2
LOCAL_LEAD_FAILURE_LIMIT = 2
LOCAL_LEAD_MIN_CONFIDENCE = 0.45
GEOMETRY_MIN_CELLS = 12
WARP_CLUSTER_RADIUS = 2
FINAL_GUESS_STATES = {"confirmed", "rejected", "retired"}


class Run3Explorer(Run2Explorer):
    """Explorer fixes learned from the third recorded playthrough.

    The run stored several contradictory actions for the same physical doorway
    because a stale movement sample was credited when the room changed. It also
    preferred a known backtracking warp over untested local exit evidence. This
    layer canonicalizes doorway actions from room geometry, increases cooldowns
    for repeatedly crossed links, and defers known warps while local leads remain.
    """

    def __init__(self, seed: int = 0, memory_path: Path | None = None):
        super().__init__(seed, memory_path)
        self.room_link_crossings: Counter[frozenset[str]] = Counter()
        self.canonicalized_warps = 0
        self.deferred_warps_for_local_leads = 0
        self._canonicalize_all_warps()

    def _observe_room(self, telemetry: TelemetrySample) -> None:
        previous_room = self.observed_room
        room = self._room_key(telemetry)
        super()._observe_room(telemetry)
        if previous_room is None or room == previous_room:
            return

        link = frozenset((previous_room, room))
        self.room_link_crossings[link] += 1
        multiplier = self._room_link_backoff_multiplier(
            self.room_link_crossings[link]
        )
        self.room_link_cooldowns[link] = max(
            self.room_link_cooldowns.get(link, 0),
            self.navigation_tick + ROOM_LINK_COOLDOWN_STEPS * multiplier,
        )
        self._canonicalize_link(previous_room, room)
        self._canonicalize_link(room, previous_room)

    @staticmethod
    def _room_link_backoff_multiplier(crossings: int) -> int:
        # Escalate after each complete pair of crossings.  The previous
        # formula jumped at crossing 2, 4, 6... and suppressed a doorway one
        # traversal earlier than intended.
        return min(
            MAX_LINK_BACKOFF_MULTIPLIER,
            max(1, (crossings + 1) // 2),
        )

    def _canonicalize_all_warps(self) -> None:
        links = {(warp[0], warp[4]) for warp in self.warps}
        for source_room, target_room in links:
            self._canonicalize_link(source_room, target_room)

    def _canonicalize_link(self, source_room: str, target_room: str) -> None:
        candidates = [
            (warp, crossings)
            for warp, crossings in self.warps.items()
            if warp[0] == source_room and warp[4] == target_room
        ]
        if len(candidates) < 2:
            return

        clusters: list[list[tuple[Warp, int]]] = []
        for item in candidates:
            warp, _crossings = item
            for cluster in clusters:
                anchor = cluster[0][0]
                if max(abs(anchor[1] - warp[1]), abs(anchor[2] - warp[2])) <= WARP_CLUSTER_RADIUS:
                    cluster.append(item)
                    break
            else:
                clusters.append([item])

        for cluster in clusters:
            if len({warp[3] for warp, _count in cluster}) < 2:
                continue
            direction = self._geometry_direction(source_room, cluster)
            if direction is None:
                direction = max(
                    cluster,
                    key=lambda item: (item[1], item[0][3]),
                )[0][3]
            total = sum(count for _warp, count in cluster)
            representative = max(cluster, key=lambda item: item[1])[0]
            canonical: Warp = (
                representative[0],
                representative[1],
                representative[2],
                direction,
                representative[4],
                representative[5],
                representative[6],
            )
            for warp, _count in cluster:
                self.warps.pop(warp, None)
            self.warps[canonical] += total
            self.canonicalized_warps += len(cluster) - 1

    def _geometry_direction(
        self,
        room: str,
        cluster: list[tuple[Warp, int]],
    ) -> str | None:
        cells = [(x, y) for seen_room, x, y in self.seen_cells if seen_room == room]
        if len(cells) < GEOMETRY_MIN_CELLS:
            return None
        xs = [cell[0] for cell in cells]
        ys = [cell[1] for cell in cells]
        source_x = round(sum(warp[1] * count for warp, count in cluster) / max(1, sum(count for _warp, count in cluster)))
        source_y = round(sum(warp[2] * count for warp, count in cluster) / max(1, sum(count for _warp, count in cluster)))
        distances = {
            "left": abs(source_x - min(xs)),
            "right": abs(max(xs) - source_x),
            "up": abs(source_y - min(ys)),
            "down": abs(max(ys) - source_y),
        }
        nearest = min(distances.values())
        choices = [direction for direction, distance in distances.items() if distance == nearest]
        observed = Counter(warp[3] for warp, count in cluster for _ in range(count))
        return max(choices, key=lambda direction: (observed[direction], direction))

    def _visual_lead_is_actionable(
        self,
        key: tuple[str, int, int],
        record: dict[str, object],
        *,
        hypotheses: set[str],
    ) -> bool:
        """Return whether a learned visual lead can still produce a new test.

        ``inspections`` was the old, coarse lifecycle counter.  New memories
        distinguish a completed test from an approach that never reached its
        target, so prefer those fields while retaining old-memory support.
        """

        hypothesis = str(record.get("hypothesis") or "")
        if hypothesis not in hypotheses:
            return False
        state = str(record.get("guess_state") or "proposed")
        if state in FINAL_GUESS_STATES:
            return False
        cooldown_until = max(
            int(record.get("cooldown_until_tick", 0) or 0),
            int(self.visual_goal_cooldowns.get(key, 0)),
        )
        if state == "cooldown" and self.navigation_tick < cooldown_until:
            return False
        completed_tests = int(
            record.get("completed_tests", record.get("inspections", 0)) or 0
        )
        failed_approaches = int(record.get("failed_approaches", 0) or 0)
        if (
            completed_tests >= LOCAL_LEAD_TEST_LIMIT
            or failed_approaches >= LOCAL_LEAD_FAILURE_LIMIT
        ):
            return False
        if record.get("choice_retry"):
            return True
        confidence = float(
            record.get("guess_confidence", record.get("interest", 0.0)) or 0.0
        )
        return confidence >= LOCAL_LEAD_MIN_CONFIDENCE

    def _has_local_lead(self, room: str) -> bool:
        """Keep a known warp waiting only for an actionable local story lead.

        A visual seam or an arbitrary outline cell is not stronger evidence
        than a room transition the agent has already observed.  Exit evidence
        is ranked separately by :class:`Run4Explorer`; this gate is reserved
        for nearby character/object hypotheses that may advance dialogue.
        """

        return any(
            key[0] == room
            and self._visual_lead_is_actionable(
                key,
                record,
                hypotheses={"possible_character", "possible_interactable"},
            )
            for key, record in self.screen_regions.items()
        )

    def _route_to_learned_warp(
        self,
        room: str,
        start: tuple[int, int],
    ) -> tuple[str, Warp] | None:
        route = super()._route_to_learned_warp(room, start)
        if route is None:
            return None
        if self._has_local_lead(room):
            self.deferred_warps_for_local_leads += 1
            return None
        return route

    def summary(self) -> dict:
        summary = super().summary()
        summary["canonicalized_warps"] = self.canonicalized_warps
        summary["deferred_warps_for_local_leads"] = self.deferred_warps_for_local_leads
        summary["repeated_room_links"] = sum(count > 1 for count in self.room_link_crossings.values())
        return summary
