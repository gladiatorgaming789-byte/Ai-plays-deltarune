"""Cross-run navigation and guessing fixes learned from the 2026-08-15 run set.

The rules in this layer are deliberately room-agnostic. They use only evidence
the agent has already observed: collision topology, visual lifecycle metadata,
learned open edges, observed room transitions, and interaction outcomes.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from . import guessing_v3 as v3
from .entity_detection_v2 import (
    ENTITY_DETECTION_VERSION,
    entity_candidate_state,
    response_evidence,
    single_side_entity_candidate,
)
from .exit_detection_v2 import (
    EXIT_DETECTION_VERSION,
    evaluate_exit_candidate,
    exit_candidate_source,
)
from .policy import (
    DIRECTION_VECTORS,
    VISUAL_GOAL_COOLDOWN_STEPS,
)
from .run20_run_analysis_fixes import Run20RunAnalysisExplorer


WEAK_ENTITY_APPROACH_ACTION_LIMIT = 5
WEAK_ENTITY_MAX_ROUTE_CELLS = 6
WEAK_ENTITY_ROOM_PROBE_LIMIT = 3

ROOM_LINK_WINDOW_STEPS = 220
ROOM_LINK_REPEAT_THRESHOLD = 3
ROOM_LINK_COOLDOWN_STEPS = 120


class Run21MultiRunExplorer(Run20RunAnalysisExplorer):
    """Precision fixes derived from eight consecutive live runs."""

    def __init__(self, seed: int = 0, memory_path: Path | None = None):
        super().__init__(seed, memory_path)
        self._weak_entity_probe_goal: (
            tuple[tuple[str, int, int], tuple[int, int], str] | None
        ) = None
        self._weak_entity_probe_steps = 0
        self._weak_entity_room_probes: dict[tuple[str, int], int] = {}
        self._recent_room_link_crossings: deque[
            tuple[int, frozenset[str], int]
        ] = deque(maxlen=32)
        self._room_link_cooldown_until: dict[frozenset[str], int] = {}

        self.exit_semantic_leaks_repaired = 0
        self.single_side_entity_labels_downgraded = 0
        self.weak_entity_probe_plans = 0
        self.weak_entity_probe_failures = 0
        self.weak_entity_probe_confirmations = 0
        self.room_link_pingpong_guards = 0
        self.room_link_crossings_suppressed = 0

        # Re-derive semantic exposure from the evidence already stored in old
        # navigation memory. No evidence is deleted and no room-specific answer
        # is introduced.
        for record in self.screen_regions.values():
            self._sanitize_exit_semantics(record, count_repair=True)
            self._sanitize_entity_semantics(record, count_repair=True)

    def _refresh_visual_guess_metadata(
        self,
        region: tuple[int, int],
        record: dict[str, object],
        obstruction_details: dict[str, object] | None = None,
    ) -> None:
        super()._refresh_visual_guess_metadata(
            region,
            record,
            obstruction_details,
        )
        self._sanitize_exit_semantics(record, count_repair=False)
        self._sanitize_entity_semantics(record, count_repair=False)

    def _sanitize_exit_semantics(
        self,
        record: dict[str, object],
        *,
        count_repair: bool,
    ) -> None:
        source = exit_candidate_source(record)
        if source is None and not record.get("path_continuation"):
            return

        state, score, reasons = evaluate_exit_candidate(record)
        record["exit_detection_version"] = EXIT_DETECTION_VERSION
        record["exit_candidate_source"] = source or "geometry_path_probe"
        record["exit_candidate_state"] = state
        record["exit_candidate_visual_score"] = round(score, 4)
        record["exit_candidate_reasons"] = reasons[-8:]

        if state in {"semantic_ready", "confirmed"}:
            return

        repaired = False
        if record.get("hypothesis") == "possible_exit":
            record["hypothesis"] = None
            repaired = True
        if record.get("guess_semantic_state") == "possible_exit":
            record["guess_semantic_state"] = v3.UNKNOWN_BUT_INTERESTING
            record["guess_label"] = "Exit-like feature; route evidence unresolved"
            repaired = True
        if repaired and count_repair:
            self.exit_semantic_leaks_repaired += 1

    def _sanitize_entity_semantics(
        self,
        record: dict[str, object],
        *,
        count_repair: bool,
    ) -> None:
        if not single_side_entity_candidate(record):
            return

        state = entity_candidate_state(record)
        record["entity_detection_version"] = ENTITY_DETECTION_VERSION
        record["entity_candidate_state"] = state
        record["entity_candidate_reason"] = (
            "one collision side proves a compact obstruction, not an interaction"
        )

        if response_evidence(record):
            return

        repaired = False
        if record.get("hypothesis") in {
            "possible_character",
            "possible_interactable",
        }:
            record["hypothesis"] = None
            repaired = True
        if record.get("guess_semantic_state") in {
            "possible_character",
            "possible_interactable",
        }:
            record["guess_semantic_state"] = v3.UNKNOWN_BUT_INTERESTING
            repaired = True
        record["guess_label"] = "Compact obstruction; interaction unresolved"
        record["evidence_kind"] = "single_side_obstruction_candidate"
        record["evidence_summary"] = (
            "One learned collision side shows that a compact obstruction exists; "
            "a bounded interaction test or independent collision side is still "
            "needed before treating it as a character or interactable."
        )
        if repaired and count_repair:
            self.single_side_entity_labels_downgraded += 1

    def _remember_path_continuation(self, probe) -> None:
        # Legacy policy temporarily stamps possible_exit here. The overridden
        # metadata refresher immediately re-evaluates it under Exit Detection v2,
        # turning geometry-only evidence back into a non-semantic candidate.
        super()._remember_path_continuation(probe)
        room, cell_x, cell_y, _direction = probe
        key = (room, *self._region((cell_x, cell_y)))
        record = self.screen_regions.get(key)
        if record is None:
            return
        before = record.get("hypothesis")
        self._sanitize_exit_semantics(record, count_repair=False)
        if before == "possible_exit" and record.get("hypothesis") is None:
            self.exit_semantic_leaks_repaired += 1
            self.map_updates.append(self._screen_region_map_update(key, record))

    def _observe_room(self, telemetry) -> None:
        previous_room = self.observed_room
        super()._observe_room(telemetry)
        room = self._room_key(telemetry)
        if previous_room is None or previous_room == room:
            return

        self._weak_entity_probe_goal = None
        self._weak_entity_probe_steps = 0

        if not hasattr(self, "_recent_room_link_crossings"):
            return
        link = frozenset((previous_room, room))
        if len(link) != 2:
            return
        now = self.navigation_tick
        epoch = self.story_epoch
        self._recent_room_link_crossings.append((now, link, epoch))
        recent_count = sum(
            recent_link == link
            and recent_epoch == epoch
            and now - tick <= ROOM_LINK_WINDOW_STEPS
            for tick, recent_link, recent_epoch in self._recent_room_link_crossings
        )
        if recent_count >= ROOM_LINK_REPEAT_THRESHOLD:
            previous_expiry = self._room_link_cooldown_until.get(link, 0)
            expiry = max(previous_expiry, now + ROOM_LINK_COOLDOWN_STEPS)
            self._room_link_cooldown_until[link] = expiry
            if previous_expiry <= now:
                self.room_link_pingpong_guards += 1

    def _link_temporarily_guarded(self, source_room: str, target_room: str) -> bool:
        link = frozenset((source_room, target_room))
        expiry = self._room_link_cooldown_until.get(link, 0)
        if expiry <= self.navigation_tick:
            self._room_link_cooldown_until.pop(link, None)
            return False
        return True

    def _is_entry_warp_direction(
        self,
        room: str,
        cell: tuple[int, int],
        direction: str,
    ) -> bool:
        if super()._is_entry_warp_direction(room, cell, direction):
            return True
        for (
            source_room,
            source_x,
            source_y,
            action,
            target_room,
            _target_x,
            _target_y,
        ) in self.warps:
            if (
                source_room == room
                and (source_x, source_y) == cell
                and action == direction
                and self._link_temporarily_guarded(source_room, target_room)
            ):
                self.room_link_crossings_suppressed += 1
                return True
        return False

    def _weak_entity_probe_count(self, room: str) -> int:
        return self._weak_entity_room_probes.get((room, self.story_epoch), 0)

    def _set_weak_entity_probe(
        self,
        goal: tuple[tuple[str, int, int], tuple[int, int], str],
    ) -> bool:
        key, _source, _direction = goal
        room = key[0]
        if self._weak_entity_probe_goal == goal:
            return True
        if self._weak_entity_probe_count(room) >= WEAK_ENTITY_ROOM_PROBE_LIMIT:
            return False
        self._weak_entity_probe_goal = goal
        self._weak_entity_probe_steps = 0
        counter_key = (room, self.story_epoch)
        self._weak_entity_room_probes[counter_key] = (
            self._weak_entity_room_probes.get(counter_key, 0) + 1
        )
        record = self.screen_regions.get(key)
        if record is not None:
            record["approach_attempts"] = int(record.get("approach_attempts", 0)) + 1
        return True

    def _clear_weak_entity_probe(self) -> None:
        self._weak_entity_probe_goal = None
        self._weak_entity_probe_steps = 0

    def _fail_weak_entity_probe(
        self,
        key: tuple[str, int, int],
        reason: str,
        *,
        tested: bool,
    ) -> None:
        record = self.screen_regions.get(key)
        if record is None:
            self._clear_weak_entity_probe()
            return
        if tested:
            record["completed_tests"] = int(
                record.get("completed_tests", record.get("inspections", 0))
            ) + 1
            record["inspections"] = int(record["completed_tests"])
        else:
            record["failed_approaches"] = int(record.get("failed_approaches", 0)) + 1
        record["last_failure_reason"] = reason
        # One exact Z test with no response is strong negative evidence for a
        # one-sided candidate. Route failures get one bounded retry.
        if tested or int(record.get("failed_approaches", 0)) >= 2:
            record["guess_state"] = "rejected"
        else:
            record["guess_state"] = "cooldown"
            self.visual_goal_cooldowns[key] = (
                self.navigation_tick + VISUAL_GOAL_COOLDOWN_STEPS
            )
        self._refresh_visual_guess_metadata((key[1], key[2]), record)
        self.map_updates.append(self._screen_region_map_update(key, record))
        self.weak_entity_probe_failures += 1
        self._clear_weak_entity_probe()

    def _weak_entity_routes(
        self,
        room: str,
        cell: tuple[int, int],
    ):
        adjacency = self._adjacency(room)
        routes = []
        for key, record in self.screen_regions.items():
            if key[0] != room or not single_side_entity_candidate(record):
                continue
            if str(record.get("guess_state") or "proposed") in {
                "confirmed",
                "rejected",
                "retired",
            }:
                continue
            if self._visual_goal_is_cooling(key):
                continue
            if int(record.get("completed_tests", record.get("inspections", 0))) > 0:
                continue
            if int(record.get("failed_approaches", 0)) > 0:
                continue
            if int(record.get("last_seen_sequence", record.get("views", 0))) <= 0:
                continue

            for (
                edge_room,
                source_x,
                source_y,
                direction,
            ), failures in self.blocked.items():
                if edge_room != room or failures <= 0:
                    continue
                source = (source_x, source_y)
                target = self._interaction_target(source, direction)
                if self._region(target) != (key[1], key[2]):
                    continue
                probe = (room, source_x, source_y, direction)
                if self.character_probes[probe] > 0:
                    continue
                if source == cell:
                    first_direction = direction
                    distance = 0
                else:
                    route = self._route_to_target(adjacency, cell, source)
                    if route is None:
                        continue
                    first_direction, distance = route
                if distance > WEAK_ENTITY_MAX_ROUTE_CELLS:
                    continue
                candidate_state = entity_candidate_state(record)
                routes.append(
                    (
                        (
                            0 if candidate_state == "single_side_stable" else 1,
                            distance,
                            int(record.get("approach_attempts", 0)),
                            self.visits[(room, *source)],
                            key[2],
                            key[1],
                            direction,
                        ),
                        key,
                        source,
                        direction,
                        first_direction,
                    )
                )
        return routes

    def _plan_weak_entity_probe(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> tuple[str, int, str] | None:
        if (
            self._weak_entity_probe_goal is None
            and self._weak_entity_probe_count(room) >= WEAK_ENTITY_ROOM_PROBE_LIMIT
        ):
            return None

        routes = self._weak_entity_routes(room, cell)
        if not routes:
            self._clear_weak_entity_probe()
            return None

        active = self._weak_entity_probe_goal
        selected = None
        if active is not None:
            active_key, active_source, active_direction = active
            for route in routes:
                _score, key, source, direction, first_direction = route
                if (
                    key == active_key
                    and source == active_source
                    and direction == active_direction
                ):
                    selected = route
                    break
        if selected is None:
            selected = min(routes, key=lambda item: item[0])
            _score, key, source, direction, _first_direction = selected
            if not self._set_weak_entity_probe((key, source, direction)):
                return None

        _score, key, source, direction, first_direction = selected
        self._weak_entity_probe_steps += 1
        if self._weak_entity_probe_steps > WEAK_ENTITY_APPROACH_ACTION_LIMIT:
            self._fail_weak_entity_probe(
                key,
                "bounded weak-entity approach made no concrete progress",
                tested=False,
            )
            return None

        self.weak_entity_probe_plans += 1
        if source == cell:
            return (
                direction,
                1,
                "story search: bounded test of unresolved compact obstruction "
                f"near region ({key[1]}, {key[2]})",
            )
        return (
            first_direction,
            1,
            "story search: bounded route toward unresolved compact obstruction "
            f"near region ({key[1]}, {key[2]})",
        )

    def _interaction_probe_is_justified(
        self,
        room: str,
        cell: tuple[int, int],
        direction: str,
    ) -> bool:
        goal = self._weak_entity_probe_goal
        if goal is not None:
            key, source, probe_direction = goal
            if (
                key[0] == room
                and source == cell
                and probe_direction == direction
            ):
                return True
        return super()._interaction_probe_is_justified(room, cell, direction)

    def _remember_failed_character_probe(self) -> None:
        goal = self._weak_entity_probe_goal
        candidate = self.interaction_candidate
        if goal is not None and candidate is not None:
            key, source, direction = goal
            room, cell_x, cell_y, attempted_direction, *_rest = candidate
            if (
                key[0] == room
                and source == (cell_x, cell_y)
                and direction == attempted_direction
            ):
                probe = (room, cell_x, cell_y, attempted_direction)
                self.character_probes[probe] += 1
                self._fail_weak_entity_probe(
                    key,
                    "exact interaction test produced no observed game-state response",
                    tested=True,
                )
                return
        super()._remember_failed_character_probe()

    def _complete_pending_interaction(self) -> None:
        goal = self._weak_entity_probe_goal
        candidate = self.interaction_candidate
        matched = None
        if goal is not None and candidate is not None:
            key, source, direction = goal
            room, cell_x, cell_y, attempted_direction, _instance, _name, tx, ty = candidate
            if (
                key[0] == room
                and source == (cell_x, cell_y)
                and direction == attempted_direction
                and self._region((tx, ty)) == (key[1], key[2])
            ):
                matched = (key, (tx, ty))
        super()._complete_pending_interaction()
        if matched is None:
            return
        key, target = matched
        record = self.screen_regions.get(key)
        if record is None:
            self._clear_weak_entity_probe()
            return
        record["confirmed_interactable_cell"] = [target[0], target[1]]
        record["hypothesis"] = "possible_interactable"
        record["guess_state"] = "confirmed"
        record["entity_detection_version"] = ENTITY_DETECTION_VERSION
        record["entity_candidate_state"] = "confirmed_response"
        self._refresh_visual_guess_metadata((key[1], key[2]), record)
        record["hypothesis"] = "possible_interactable"
        record["guess_state"] = "confirmed"
        record["guess_semantic_state"] = "possible_interactable"
        self.map_updates.append(self._screen_region_map_update(key, record))
        self.weak_entity_probe_confirmations += 1
        self._clear_weak_entity_probe()

    def _plan_exploration(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> tuple[str, int, str]:
        # Preserve concrete learned interactions and already-corroborated entity
        # semantics before spending the bounded weak-candidate budget.
        if self._progress_pressure(room, cell):
            retry_route = self._route_to_retryable_story_interaction(room, cell)
            if retry_route is not None:
                direction, target = retry_route
                return (
                    direction,
                    1,
                    "story search: retry another response at learned interaction "
                    f"({target[1]},{target[2]}) via {direction}",
                )
            strong_plan = self._direction_to_visual_hypothesis(
                room,
                cell,
                story_focus=True,
                allowed_hypotheses={
                    "possible_character",
                    "possible_interactable",
                },
            )
            if strong_plan is not None:
                direction, hypothesis, target_region = strong_plan
                return (
                    direction,
                    1,
                    "story search: approach corroborated "
                    f"{hypothesis.replace('_', ' ')} via {direction} "
                    f"near region {target_region}",
                )
            weak_plan = self._plan_weak_entity_probe(room, cell)
            if weak_plan is not None:
                return weak_plan

        return super()._plan_exploration(room, cell)

    def summary(self) -> dict:
        summary = super().summary()
        summary.update(
            {
                "run21_multirun_fixes": True,
                "exit_semantic_leaks_repaired": self.exit_semantic_leaks_repaired,
                "single_side_entity_labels_downgraded": (
                    self.single_side_entity_labels_downgraded
                ),
                "weak_entity_probe_plans": self.weak_entity_probe_plans,
                "weak_entity_probe_failures": self.weak_entity_probe_failures,
                "weak_entity_probe_confirmations": self.weak_entity_probe_confirmations,
                "room_link_pingpong_guards": self.room_link_pingpong_guards,
                "room_link_crossings_suppressed": self.room_link_crossings_suppressed,
                "active_room_link_cooldowns": sum(
                    expiry > self.navigation_tick
                    for expiry in self._room_link_cooldown_until.values()
                ),
            }
        )
        return summary


__all__ = ["Run21MultiRunExplorer"]
