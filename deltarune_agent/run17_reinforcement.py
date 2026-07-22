from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .policy import DIRECTION_VECTORS
from .reinforcement import (
    REINFORCEMENT_MEMORY_FILENAME,
    REINFORCEMENT_SETTINGS_FILENAME,
    ReinforcementMemory,
    RewardSettings,
    load_reward_settings,
)
from .run16_warp_guard import Run16GuardedExplorer


PROMOTION_EVIDENCE_KIND = "repeated_compact_sprite_motion"


class Run17ReinforcementExplorer(Run16GuardedExplorer):
    """Use observed outcomes to learn action value without promoting regions.

    Perception remains neutral. This layer never invents a character hypothesis,
    changes a visual region's class because of source knowledge, or routes to an
    object solely because it animates. It only scores actions that the existing
    navigation/perception system already made available and then rewards or
    penalizes them from observed outcomes.
    """

    def __init__(self, seed: int = 0, memory_path: Path | None = None):
        super().__init__(seed, memory_path)
        memory_directory = memory_path.parent if memory_path is not None else None
        settings_path = (
            memory_directory / REINFORCEMENT_SETTINGS_FILENAME
            if memory_directory is not None
            else None
        )
        reinforcement_path = (
            memory_directory / REINFORCEMENT_MEMORY_FILENAME
            if memory_directory is not None
            else None
        )
        self.reward_settings = load_reward_settings(settings_path)
        self.reinforcement = ReinforcementMemory.load(reinforcement_path)
        self.reinforcement_actions_started = 0
        self.reinforcement_rewards_applied = 0
        self.removed_promoted_regions = 0
        self._last_mode_key: str | None = None
        self._remove_motion_promotions()

    def _remove_motion_promotions(self) -> None:
        """Migrate Run 16 promotion-only records back to neutral evidence."""
        for key, record in self.screen_regions.items():
            promoted = bool(record.get("motion_sprite_candidate")) or str(
                record.get("source_evidence_kind") or ""
            ) == PROMOTION_EVIDENCE_KIND
            if not promoted:
                continue
            was_promotion_hypothesis = (
                record.get("hypothesis") == "possible_character"
                and str(record.get("evidence_kind") or "")
                == PROMOTION_EVIDENCE_KIND
                and str(record.get("guess_state") or "proposed")
                not in {"confirmed"}
            )
            for field in (
                "motion_sprite_candidate",
                "motion_sprite_tested",
                "source_evidence_kind",
            ):
                record.pop(field, None)
            if was_promotion_hypothesis:
                record["hypothesis"] = None
                record["guess_state"] = "retired"
                record["retired_reason"] = (
                    "removed source-informed region promotion; future value must "
                    "come from an interaction the agent actually attempts"
                )
                self.visual_goal_cooldowns.pop(key, None)
                if self.visual_goal == key:
                    self.visual_goal = None
                    self.decision_visual_goal = None
            self.removed_promoted_regions += 1
            self.map_updates.append(self._screen_region_map_update(key, record))

    # Run 16 calls this through dynamic dispatch after every screen observation.
    # Keeping it empty guarantees that no region is promoted by animation.
    def _promote_motion_sprite_candidates(self, room: str) -> None:
        return

    def _motion_sprite_ready(self, room: str, cell: tuple[int, int]):
        return None

    @staticmethod
    def _interaction_key(key: tuple[str, int, int]) -> str:
        return f"interaction:{key[0]}:{key[1]}:{key[2]}"

    @staticmethod
    def _choice_key(record: Mapping[str, object], pattern: int) -> str:
        return (
            "choice:"
            f"{record.get('room', 'unknown')}:"
            f"{record.get('context_x', -1)}:"
            f"{record.get('context_y', -1)}:"
            f"{record.get('signature', '')}:"
            f"{pattern}"
        )

    @staticmethod
    def _portal_key(portal_id: str) -> str:
        return f"portal:{portal_id}"

    def _begin_reinforcement_action(
        self,
        key: str,
        *,
        kind: str,
        context: Mapping[str, object] | None = None,
    ) -> None:
        if self.reinforcement.begin_action(
            key,
            kind=kind,
            context=context,
            step=self.navigation_tick,
            settings=self.reward_settings,
        ):
            self.reinforcement_actions_started += 1

    def _reward_key(
        self,
        key: str,
        reward_name: str,
        *,
        event: str,
        kind: str,
        context: Mapping[str, object] | None = None,
    ) -> None:
        reward = self.reward_settings.reward(reward_name)
        if not self.reward_settings.enabled or reward == 0.0:
            return
        self.reinforcement.reward_key(
            key,
            reward,
            event=event,
            step=self.navigation_tick,
            kind=kind,
            context=context,
        )
        self.reinforcement_rewards_applied += 1

    def _reward_trace(self, reward_name: str, event: str) -> None:
        reward = self.reward_settings.reward(reward_name)
        if not self.reward_settings.enabled or reward == 0.0:
            return
        self.reinforcement.reward_trace(
            reward,
            event=event,
            step=self.navigation_tick,
            settings=self.reward_settings,
        )
        self.reinforcement_rewards_applied += 1

    def _complete_pending_interaction(self) -> None:
        super()._complete_pending_interaction()
        key = self.active_interaction_key
        if key is None:
            return
        self._begin_reinforcement_action(
            self._interaction_key(key),
            kind="interaction",
            context={"room": key[0], "x": key[1], "y": key[2]},
        )

    def _finish_active_interaction(self, telemetry) -> None:
        key = self.active_interaction_key
        previous_confirmations = 0
        if key is not None:
            previous_confirmations = int(
                self.interactables.get(key, {}).get("confirmations", 0)
            )
        super()._finish_active_interaction(telemetry)
        if key is None:
            return
        record = self.interactables.get(key)
        if record is None:
            return
        action_key = self._interaction_key(key)
        outcome = str(record.get("last_outcome") or "unknown")
        context = {"room": key[0], "x": key[1], "y": key[2]}
        if outcome in {"scripted_sequence", "battle_started", "room_change"}:
            self._reward_key(
                action_key,
                "interaction_progress",
                event=outcome,
                kind="interaction",
                context=context,
            )
        elif outcome == "ordinary_dialogue":
            self._reward_key(
                action_key,
                "ordinary_dialogue",
                event=outcome,
                kind="interaction",
                context=context,
            )
        elif outcome == "choice_without_progress":
            self._reward_key(
                action_key,
                "choice_failure",
                event=outcome,
                kind="interaction",
                context=context,
            )
        if previous_confirmations <= 0 and int(record.get("confirmations", 0)) > 0:
            self._reward_key(
                action_key,
                "information_gain",
                event="first confirmed interaction",
                kind="interaction",
                context=context,
            )

    def _start_choice_trial(self, observation, telemetry) -> None:
        super()._start_choice_trial(observation, telemetry)
        record = self.pending_choice_record
        pattern = self.pending_choice_pattern
        if record is None or pattern is None:
            return
        self._begin_reinforcement_action(
            self._choice_key(record, pattern),
            kind="choice",
            context={
                "room": record.get("room"),
                "context_x": record.get("context_x"),
                "context_y": record.get("context_y"),
                "pattern": pattern,
            },
        )

    def _mark_pending_choice_failed(
        self,
        event: str,
        *,
        prioritize_retry: bool = True,
    ) -> None:
        record = self.pending_choice_record
        pattern = self.pending_choice_pattern
        key = self._choice_key(record, pattern) if record is not None and pattern is not None else None
        context = (
            {
                "room": record.get("room"),
                "context_x": record.get("context_x"),
                "context_y": record.get("context_y"),
                "pattern": pattern,
            }
            if record is not None and pattern is not None
            else None
        )
        super()._mark_pending_choice_failed(
            event,
            prioritize_retry=prioritize_retry,
        )
        if key is not None:
            self._reward_key(
                key,
                "choice_failure",
                event=event,
                kind="choice",
                context=context,
            )

    def _record_story_progress(self, event: str, telemetry) -> None:
        pending_record = self.pending_choice_record
        pending_pattern = self.pending_choice_pattern
        pending_key = (
            self._choice_key(pending_record, pending_pattern)
            if pending_record is not None and pending_pattern is not None
            else None
        )
        super()._record_story_progress(event, telemetry)
        reward_name = (
            "room_discovery" if event == "discovered a new room" else "story_progress"
        )
        self._reward_trace(reward_name, event)
        if pending_key is not None and event != "discovered a new room":
            self._reward_key(
                pending_key,
                "choice_progress",
                event=event,
                kind="choice",
            )

    def _remember_failed_character_probe(self) -> None:
        had_candidate = self.interaction_candidate is not None
        super()._remember_failed_character_probe()
        if had_candidate:
            self._reward_trace("no_response", "interaction produced no state change")

    def _break_oscillation(
        self,
        room: str,
        cell: tuple[int, int],
        proposed: str,
    ) -> tuple[str, bool]:
        direction, broke = super()._break_oscillation(room, cell, proposed)
        if broke:
            self._reward_trace("navigation_loop", self.loop_reason)
        return direction, broke

    def _route_to_retryable_story_interaction(
        self,
        room: str,
        start: tuple[int, int],
    ):
        adjacency = self._adjacency(room)
        routes = []
        for key, interaction in self.interactables.items():
            if key[0] != room or not self._story_interaction_retryable(key):
                continue
            action_key = self._interaction_key(key)
            learned_score = self.reinforcement.score(action_key, self.reward_settings)
            for approach in interaction.get("approaches", []):
                if not isinstance(approach, dict):
                    continue
                direction = str(approach.get("direction") or "")
                if direction not in DIRECTION_VECTORS:
                    continue
                source = (int(approach.get("x", -1)), int(approach.get("y", -1)))
                if start == source:
                    first_direction, distance = direction, 0
                else:
                    route = self._route_to_target(adjacency, start, source)
                    if route is None:
                        continue
                    first_direction, distance = route
                routes.append(
                    (
                        (
                            -learned_score,
                            int(interaction.get("attempts", 0)),
                            distance,
                            self._recent_cell_cost(room, source),
                            key,
                        ),
                        first_direction,
                        key,
                    )
                )
        if not routes:
            return None
        _score, direction, key = min(routes, key=lambda item: item[0])
        self._begin_reinforcement_action(
            self._interaction_key(key),
            kind="interaction_retry",
            context={"room": key[0], "x": key[1], "y": key[2]},
        )
        # Deliberately do not create or modify a screen-region hypothesis here.
        return direction, key

    def _route_to_learned_warp(self, room: str, start: tuple[int, int]):
        candidates = []
        for warp, crossings in self.warps.items():
            source_room, source_x, source_y, action, target_room, _tx, _ty = warp
            if source_room != room or action not in DIRECTION_VECTORS:
                continue
            portal_id = self.world.portal_id_for_warp(warp, create=False)
            if portal_id is None:
                continue
            metadata = self.world.portal_metadata(portal_id) or {}
            if str(metadata.get("role") or "") == "automatic_sequence":
                continue
            source = (source_x, source_y)
            adjacency = self._adjacency(room, avoid_backtrack=False)
            if source == start:
                if self._blocked_near(room, start, action):
                    continue
                first_direction, distance = action, 0
            else:
                route = self._route_to_target(adjacency, start, source)
                if route is None:
                    continue
                first_direction, distance = route
            learned_score = self.reinforcement.score(
                self._portal_key(portal_id), self.reward_settings
            )
            backtrack = int(self.room_entry_from.get(room) == target_room)
            candidates.append(
                (
                    (-learned_score, backtrack, distance, crossings, portal_id),
                    first_direction,
                    warp,
                    portal_id,
                )
            )
        if not candidates:
            return super()._route_to_learned_warp(room, start)
        _score, direction, warp, portal_id = min(candidates, key=lambda item: item[0])
        metadata = self.world.portal_metadata(portal_id) or {}
        self._begin_reinforcement_action(
            self._portal_key(portal_id),
            kind="portal",
            context={
                "from_room": metadata.get("from_room"),
                "to_room": metadata.get("to_room"),
                "action": metadata.get("action"),
            },
        )
        return direction, warp

    def _plan_exploration(self, room: str, cell: tuple[int, int]):
        direction, steps, reason = super()._plan_exploration(room, cell)
        lowered = reason.casefold()
        if "frontier" in lowered or "explore new edge" in lowered:
            kind = "frontier_exploration"
        elif "warp" in lowered or "known exit" in lowered:
            kind = "portal_navigation"
        elif "interaction" in lowered or "character" in lowered:
            kind = "interaction_search"
        elif "exit" in lowered or "passage" in lowered:
            kind = "exit_search"
        else:
            kind = "local_search"
        mode_key = f"mode:{room}:{kind}"
        if mode_key != self._last_mode_key:
            self._begin_reinforcement_action(
                mode_key,
                kind="navigation_mode",
                context={"room": room, "mode": kind},
            )
            self._last_mode_key = mode_key
        return direction, steps, reason

    def _explore(self, telemetry):
        if self.reward_settings.enabled and self.reinforcement.trace:
            latest = self.reinforcement.trace[0]
            key = str(latest.get("key") or "")
            if key:
                self.reinforcement.reward_key(
                    key,
                    self.reward_settings.reward("step_cost"),
                    event="active decision step cost",
                    step=self.navigation_tick,
                    kind=str(latest.get("kind") or "unknown"),
                    context=(
                        latest.get("context")
                        if isinstance(latest.get("context"), Mapping)
                        else None
                    ),
                )
        return super()._explore(telemetry)

    def save_memory(self) -> None:
        super().save_memory()
        self.reinforcement.flush(force=True)

    def summary(self) -> dict:
        summary = super().summary()
        summary["reinforcement_enabled"] = self.reward_settings.enabled
        summary["reinforcement_preset"] = self.reward_settings.detect_preset()
        summary["reinforcement_actions_started"] = self.reinforcement_actions_started
        summary["reinforcement_rewards_applied"] = self.reinforcement_rewards_applied
        summary["removed_promoted_regions"] = self.removed_promoted_regions
        summary["reinforcement"] = self.reinforcement.summary()
        return summary
