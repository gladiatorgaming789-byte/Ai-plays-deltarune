"""General learned-control framework for telemetry-poor special gameplay.

Chapter-specific facts are intentionally absent. The coordinator activates only
when normal telemetry is missing while the visible scene remains dynamic. It
then performs bounded experiments with controls the normal agent already knows,
learns which actions visibly affect the current kind of scene, and reuses those
observed control effects. It does not know what any minigame or platforming
segment expects in advance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import blake2b
from math import log, sqrt
from typing import Any

from PIL import Image, ImageChops, ImageStat

from .actions import ACTIONS, Action
from .perception import GameState


SPECIAL_GAMEPLAY_VERSION = 1
MISSING_TELEMETRY_GRACE = 8
MIN_DYNAMIC_ACTIVITY = 0.018
CONTROL_ACTIONS = ("left", "right", "up", "down", "confirm", "cancel")
MAX_EXPERIMENTS_PER_CONTEXT = 48


@dataclass
class ControlStats:
    attempts: int = 0
    response_total: float = 0.0
    novel_responses: int = 0

    @property
    def mean_response(self) -> float:
        return self.response_total / self.attempts if self.attempts else 0.0


@dataclass
class ContextMemory:
    actions: dict[str, ControlStats] = field(
        default_factory=lambda: {name: ControlStats() for name in CONTROL_ACTIONS}
    )
    experiments: int = 0


class SpecialGameplayCoordinator:
    def __init__(self) -> None:
        self.missing_telemetry_steps = 0
        self.active = False
        self.reason = "special gameplay inactive"
        self.previous_frame: Image.Image | None = None
        self.previous_context: str | None = None
        self.previous_signature: str | None = None
        self.pending_action: str | None = None
        self.pending_context: str | None = None
        self.contexts: dict[str, ContextMemory] = {}
        self.activations = 0
        self.deactivations = 0
        self.actions_selected = 0
        self.visible_responses = 0
        self.no_response_actions = 0

    @staticmethod
    def _sample(frame: Image.Image) -> Image.Image:
        return frame.convert("L").resize((64, 48), Image.Resampling.BILINEAR)

    @classmethod
    def activity(cls, previous: Image.Image | None, frame: Image.Image) -> float:
        if previous is None:
            return 0.0
        left = cls._sample(previous)
        right = cls._sample(frame)
        difference = ImageChops.difference(left, right)
        return float(ImageStat.Stat(difference).mean[0]) / 255.0

    @classmethod
    def context_key(cls, frame: Image.Image) -> str:
        sample = frame.convert("L").resize((8, 6), Image.Resampling.BILINEAR)
        buckets = bytes(min(7, int(value) // 32) for value in sample.getdata())
        return blake2b(buckets, digest_size=6).hexdigest()

    @classmethod
    def frame_signature(cls, frame: Image.Image) -> str:
        sample = cls._sample(frame)
        quantized = bytes(int(value) // 16 for value in sample.getdata())
        return blake2b(quantized, digest_size=8).hexdigest()

    def _record_pending_result(
        self,
        frame: Image.Image,
        context: str,
        activity: float,
    ) -> None:
        if self.pending_action is None or self.pending_context is None:
            return
        memory = self.contexts.setdefault(self.pending_context, ContextMemory())
        stats = memory.actions[self.pending_action]
        signature = self.frame_signature(frame)
        novel = signature != self.previous_signature
        response = min(1.0, activity * 8.0) + (0.15 if novel else 0.0)
        stats.response_total += response
        if novel:
            stats.novel_responses += 1
        if response >= 0.12:
            self.visible_responses += 1
        else:
            self.no_response_actions += 1
        self.pending_action = None
        self.pending_context = None

    @staticmethod
    def _ucb_score(stats: ControlStats, total_attempts: int) -> float:
        if stats.attempts == 0:
            return 1_000.0
        exploration = sqrt(2.0 * log(max(2, total_attempts)) / stats.attempts)
        return stats.mean_response + min(1.0, exploration) * 0.35

    def _choose_control(self, context: str) -> str | None:
        memory = self.contexts.setdefault(context, ContextMemory())
        if memory.experiments >= MAX_EXPERIMENTS_PER_CONTEXT:
            responsive = [
                (stats.mean_response, name)
                for name, stats in memory.actions.items()
                if stats.attempts and stats.mean_response >= 0.08
            ]
            return max(responsive)[1] if responsive else None
        total = sum(stats.attempts for stats in memory.actions.values()) + 1
        return max(
            CONTROL_ACTIONS,
            key=lambda name: (
                self._ucb_score(memory.actions[name], total),
                -CONTROL_ACTIONS.index(name),
            ),
        )

    def choose(
        self,
        frame: Image.Image,
        *,
        telemetry_present: bool,
        visual_valid: bool,
        state: GameState,
    ) -> Action | None:
        if telemetry_present:
            if self.active:
                self.deactivations += 1
            self.active = False
            self.missing_telemetry_steps = 0
            self.pending_action = None
            self.pending_context = None
            self.previous_frame = frame.copy() if visual_valid else None
            self.reason = "normal telemetry resumed"
            return None
        if not visual_valid or state not in {GameState.OVERWORLD, GameState.UNKNOWN}:
            self.missing_telemetry_steps = 0
            self.pending_action = None
            self.pending_context = None
            self.reason = "special-gameplay evidence unavailable"
            return None

        self.missing_telemetry_steps += 1
        activity = self.activity(self.previous_frame, frame)
        context = self.context_key(frame)
        self._record_pending_result(frame, context, activity)
        self.previous_signature = self.frame_signature(frame)
        self.previous_frame = frame.copy()
        self.previous_context = context

        if self.missing_telemetry_steps < MISSING_TELEMETRY_GRACE:
            self.reason = (
                f"telemetry gap {self.missing_telemetry_steps}/{MISSING_TELEMETRY_GRACE}; "
                "do not assume special controls yet"
            )
            return None
        if activity < MIN_DYNAMIC_ACTIVITY:
            self.reason = (
                f"telemetry absent but scene activity {activity:.3f} is too low for "
                "safe control discovery"
            )
            return None

        if not self.active:
            self.active = True
            self.activations += 1
        action_name = self._choose_control(context)
        if action_name is None:
            self.reason = "special gameplay context exhausted without a visible control response"
            return ACTIONS["wait"]
        memory = self.contexts[context]
        memory.experiments += 1
        memory.actions[action_name].attempts += 1
        self.pending_action = action_name
        self.pending_context = context
        self.actions_selected += 1
        self.reason = (
            f"learned-control experiment in dynamic telemetry-poor scene: {action_name}; "
            f"activity={activity:.3f}, context attempts={memory.experiments}"
        )
        return ACTIONS[action_name]

    def summary(self) -> dict[str, Any]:
        learned_controls = {
            context: {
                name: round(stats.mean_response, 4)
                for name, stats in memory.actions.items()
                if stats.attempts and stats.mean_response >= 0.08
            }
            for context, memory in self.contexts.items()
        }
        learned_controls = {
            context: controls
            for context, controls in learned_controls.items()
            if controls
        }
        return {
            "version": SPECIAL_GAMEPLAY_VERSION,
            "active": self.active,
            "activations": self.activations,
            "deactivations": self.deactivations,
            "actions_selected": self.actions_selected,
            "visible_responses": self.visible_responses,
            "no_response_actions": self.no_response_actions,
            "contexts_observed": len(self.contexts),
            "contexts_with_learned_controls": len(learned_controls),
            "learned_controls": learned_controls,
        }


def install_special_gameplay() -> None:
    """Compose learned special-gameplay fallback above HierarchicalPolicy."""

    from . import hierarchical_policy as policy_module

    if getattr(policy_module, "_special_gameplay_installed", False):
        return
    original_init = policy_module.HierarchicalPolicy.__init__
    original_choose = policy_module.HierarchicalPolicy.choose
    original_summary = policy_module.HierarchicalPolicy.summary

    def init(policy, *args, **kwargs) -> None:
        original_init(policy, *args, **kwargs)
        policy.special_gameplay = SpecialGameplayCoordinator()

    def choose(policy, observation, perception, telemetry=None):
        action = policy.special_gameplay.choose(
            observation.frame,
            telemetry_present=telemetry is not None,
            visual_valid=observation.visual_valid,
            state=perception.state,
        )
        if action is not None:
            policy.reason = f"[special_gameplay] {policy.special_gameplay.reason}"
            return action
        return original_choose(policy, observation, perception, telemetry)

    def summary(policy) -> dict:
        result = original_summary(policy)
        result["special_gameplay"] = policy.special_gameplay.summary()
        return result

    policy_module.HierarchicalPolicy.__init__ = init
    policy_module.HierarchicalPolicy.choose = choose
    policy_module.HierarchicalPolicy.summary = summary
    policy_module._special_gameplay_installed = True


__all__ = [
    "CONTROL_ACTIONS",
    "MISSING_TELEMETRY_GRACE",
    "SPECIAL_GAMEPLAY_VERSION",
    "SpecialGameplayCoordinator",
    "install_special_gameplay",
]
