"""Evidence-driven Battle System v2.

This controller learns visible menu transitions and defensive control modes from
screens/outcomes. It contains no enemy names, encounter routes, or progression
answers. Colored-SOUL behavior is selected from pixels the player can see.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from hashlib import blake2b
from math import hypot
from typing import Iterable

from PIL import Image

from .actions import ACTIONS, Action
from .battle import BattleController as LegacyBattleController, Threat


BATTLE_SYSTEM_VERSION = 2
MENU_SETTLE_STEPS = 2
MENU_PATTERNS: tuple[tuple[str, ...], ...] = (
    (),
    ("down",),
    ("right",),
    ("down", "right"),
    ("right", "down"),
    ("down", "down"),
    ("right", "right"),
    ("left",),
    ("up",),
)
SOUL_COLORS = {
    "red": lambda r, g, b: r >= 170 and g <= 115 and b <= 115 and r - max(g, b) >= 55,
    "yellow": lambda r, g, b: r >= 170 and g >= 135 and b <= 115 and min(r, g) - b >= 55,
    "green": lambda r, g, b: g >= 145 and r <= 135 and b <= 145 and g - max(r, b) >= 35,
    "purple": lambda r, g, b: r >= 105 and b >= 125 and g <= 135 and b - g >= 25,
    "orange": lambda r, g, b: r >= 175 and 55 <= g <= 165 and b <= 90 and r - b >= 90,
}


@dataclass(frozen=True)
class SoulObservation:
    mode: str
    x: float
    y: float
    pixels: int


@dataclass
class MenuMemory:
    attempts: list[int]
    failures: list[int]
    successes: list[int]
    successful_pattern: int | None = None


class BattleV2Controller:
    """Learn battle menus and use mode-specific short-horizon defense."""

    def __init__(self) -> None:
        self.legacy = LegacyBattleController()
        self.reason = "Battle System v2 starting"
        self.phase = "unknown"
        self.last_action = "wait"
        self.step = 0
        self.menu_memory: dict[str, MenuMemory] = {}
        self.action_queue: deque[str] = deque()
        self.pending_signature: str | None = None
        self.pending_pattern: int | None = None
        self.pending_settle = 0
        self.pending_started_at = 0
        self.last_menu_signature: str | None = None
        self.previous_soul: SoulObservation | None = None
        self.previous_threats: tuple[Threat, ...] = ()
        self.turns_advanced = 0
        self.menu_failures = 0
        self.defense_steps = 0
        self.mode_steps: dict[str, int] = {}
        self.visual_invalid_waits = 0
        self.yellow_shots = 0
        self.green_blocks = 0

    @staticmethod
    def _image(frame: Image.Image) -> Image.Image:
        return frame.convert("RGB").resize((320, 240), Image.Resampling.NEAREST)

    @classmethod
    def observe_soul(cls, frame: Image.Image) -> SoulObservation | None:
        image = cls._image(frame)
        pixels = image.load()
        # Battle SOULs live away from the extreme HUD margins. Restricting the
        # search avoids colored HP/name/UI text becoming a false SOUL.
        points_by_mode: dict[str, list[tuple[int, int]]] = {
            mode: [] for mode in SOUL_COLORS
        }
        for y in range(25, 210):
            for x in range(20, 300):
                pixel = pixels[x, y]
                for mode, predicate in SOUL_COLORS.items():
                    if predicate(*pixel):
                        points_by_mode[mode].append((x, y))
                        break

        candidates: list[SoulObservation] = []
        for mode, points in points_by_mode.items():
            if not 4 <= len(points) <= 260:
                continue
            # Prefer compact colored components near the central playfield.
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            width = max(xs) - min(xs) + 1
            height = max(ys) - min(ys) + 1
            if width > 38 or height > 38:
                continue
            candidates.append(
                SoulObservation(
                    mode=mode,
                    x=sum(xs) / len(xs),
                    y=sum(ys) / len(ys),
                    pixels=len(points),
                )
            )
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda soul: (
                abs(soul.x - 160) + abs(soul.y - 125),
                abs(soul.pixels - 30),
            ),
        )

    @classmethod
    def menu_signature(cls, frame: Image.Image) -> str:
        image = cls._image(frame).crop((0, 105, 320, 240)).convert("L")
        compact = image.resize((80, 34), Image.Resampling.BILINEAR)
        quantized = bytes(int(value) // 32 for value in compact.getdata())
        return blake2b(quantized, digest_size=12).hexdigest()

    def _menu_state(self, signature: str) -> MenuMemory:
        memory = self.menu_memory.get(signature)
        if memory is None:
            memory = MenuMemory(
                attempts=[0] * len(MENU_PATTERNS),
                failures=[0] * len(MENU_PATTERNS),
                successes=[0] * len(MENU_PATTERNS),
            )
            self.menu_memory[signature] = memory
        return memory

    def _start_menu_trial(self, signature: str) -> Action:
        memory = self._menu_state(signature)
        if memory.successful_pattern is not None:
            pattern_index = memory.successful_pattern
        else:
            pattern_index = min(
                range(len(MENU_PATTERNS)),
                key=lambda index: (
                    memory.attempts[index] + memory.failures[index],
                    memory.attempts[index],
                    index,
                ),
            )
        memory.attempts[pattern_index] += 1
        self.pending_signature = signature
        self.pending_pattern = pattern_index
        self.pending_settle = 0
        self.pending_started_at = self.step
        # Reset toward a stable top/left selection before exploring a relative
        # pattern. This mirrors visible menu exploration without assuming labels.
        self.action_queue = deque(
            ("up", "up", "up", "left", "left", "left", *MENU_PATTERNS[pattern_index], "confirm")
        )
        action = self.action_queue.popleft()
        self.phase = "command"
        self.reason = (
            f"battle menu {signature[:8]}: test visible selection pattern "
            f"{pattern_index + 1}/{len(MENU_PATTERNS)}"
        )
        return ACTIONS[action]

    def _note_menu_result(self, current_signature: str | None, soul: SoulObservation | None) -> None:
        if self.pending_signature is None or self.pending_pattern is None:
            return
        memory = self._menu_state(self.pending_signature)
        advanced = soul is not None or (
            current_signature is not None and current_signature != self.pending_signature
        )
        if advanced:
            memory.successes[self.pending_pattern] += 1
            memory.successful_pattern = self.pending_pattern
            self.turns_advanced += 1
            self.pending_signature = None
            self.pending_pattern = None
            self.pending_settle = 0
            return
        if self.pending_settle >= MENU_SETTLE_STEPS:
            memory.failures[self.pending_pattern] += 1
            self.menu_failures += 1
            self.pending_signature = None
            self.pending_pattern = None
            self.pending_settle = 0

    def _detect_threats(self, frame: Image.Image) -> tuple[Threat, ...]:
        return self.legacy.detect_threats(frame)

    @staticmethod
    def _candidate_position(soul: SoulObservation, name: str) -> tuple[float, float]:
        dx, dy = {
            "wait": (0, 0),
            "left": (-8, 0),
            "right": (8, 0),
            "up": (0, -8),
            "down": (0, 8),
        }[name]
        return soul.x + dx, soul.y + dy

    def _predicted_threats(self, threats: tuple[Threat, ...]) -> tuple[Threat, ...]:
        if not self.previous_threats:
            return threats
        predicted: list[Threat] = []
        unused = list(self.previous_threats)
        for threat in threats:
            if not unused:
                predicted.append(threat)
                continue
            previous = min(
                unused,
                key=lambda item: hypot(item.x - threat.x, item.y - threat.y),
            )
            unused.remove(previous)
            velocity_x = threat.x - previous.x
            velocity_y = threat.y - previous.y
            if hypot(velocity_x, velocity_y) <= 28:
                predicted.append(
                    Threat(
                        threat.x + velocity_x,
                        threat.y + velocity_y,
                        threat.radius,
                    )
                )
            else:
                predicted.append(threat)
        return tuple(predicted)

    def _dodge_action(self, frame: Image.Image, soul: SoulObservation) -> Action:
        threats = self._detect_threats(frame)
        predicted = self._predicted_threats(threats)
        names = ("wait", "left", "right", "up", "down")

        def score(name: str) -> tuple[float, float, float]:
            x, y = self._candidate_position(soul, name)
            nearest_now = min(
                (hypot(x - threat.x, y - threat.y) - threat.radius for threat in threats),
                default=999.0,
            )
            nearest_next = min(
                (hypot(x - threat.x, y - threat.y) - threat.radius for threat in predicted),
                default=999.0,
            )
            # Generic screen bounds are a last-resort wall prior; detected
            # threats dominate, and movement remains short-horizon.
            margin = min(x - 24, 296 - x, y - 24, 216 - y)
            repeat = 1.5 if name == self.last_action else 0.0
            return (
                min(nearest_now, nearest_next) - repeat,
                min(nearest_now, nearest_next) + min(20.0, margin) * 0.2,
                margin,
            )

        name = max(names, key=score)
        self.previous_threats = threats
        self.last_action = name
        self.reason = (
            f"{soul.mode} SOUL defense: predicted short-horizon safety against "
            f"{len(threats)} visible threat component(s)"
        )
        return ACTIONS[name]

    def _yellow_action(self, frame: Image.Image, soul: SoulObservation) -> Action:
        movement = self._dodge_action(frame, soul)
        self.yellow_shots += 1
        if movement.name == "wait":
            self.reason = "yellow SOUL: fire visible-mode shot while no safer displacement is needed"
            return ACTIONS["confirm"]
        self.reason = f"yellow SOUL: {self.reason}; fire while moving"
        return Action(
            f"{movement.name}+confirm",
            (*movement.keys, "z"),
            duration=max(0.05, movement.duration),
            cooldown=movement.cooldown,
            continuous=False,
        )

    def _green_action(self, frame: Image.Image, soul: SoulObservation) -> Action:
        threats = self._detect_threats(frame)
        predicted = self._predicted_threats(threats)
        self.previous_threats = threats
        if not predicted:
            self.reason = "green SOUL: no incoming bright threat localized; hold shield"
            return ACTIONS["wait"]
        threat = min(
            predicted,
            key=lambda item: hypot(item.x - soul.x, item.y - soul.y) - item.radius,
        )
        dx = threat.x - soul.x
        dy = threat.y - soul.y
        keys: list[str] = []
        if abs(dy) >= abs(dx) * 0.45:
            keys.append("down" if dy > 0 else "up")
        if abs(dx) >= abs(dy) * 0.45:
            keys.append("right" if dx > 0 else "left")
        if not keys:
            keys.append("right" if dx > 0 else "left")
        self.green_blocks += 1
        self.last_action = "+".join(keys)
        self.reason = (
            "green SOUL defense: face shield toward nearest predicted incoming "
            f"threat ({self.last_action})"
        )
        if len(keys) == 1:
            return ACTIONS[keys[0]]
        return Action(
            self.last_action,
            tuple(keys),
            duration=0.08,
            cooldown=0.0,
            continuous=False,
        )

    def _defense(self, frame: Image.Image, soul: SoulObservation) -> Action:
        self.phase = "defense"
        self.defense_steps += 1
        self.mode_steps[soul.mode] = self.mode_steps.get(soul.mode, 0) + 1
        if soul.mode == "yellow":
            action = self._yellow_action(frame, soul)
        elif soul.mode == "green":
            action = self._green_action(frame, soul)
        else:
            # Purple/orange and future constrained modes still receive cautious
            # cardinal information-gain movement until observed outcomes justify
            # a more specialized controller.
            action = self._dodge_action(frame, soul)
        self.previous_soul = soul
        return action

    def choose(
        self,
        frame: Image.Image,
        _telemetry_soul_position: tuple[float, float] | None,
        *,
        visual_valid: bool = True,
    ) -> Action:
        self.step += 1
        if not visual_valid:
            self.visual_invalid_waits += 1
            self.reason = "Battle System v2: capture is untrusted; release inputs and wait"
            return ACTIONS["wait"]

        soul = self.observe_soul(frame)
        signature = None if soul is not None else self.menu_signature(frame)
        self._note_menu_result(signature, soul)

        if soul is not None:
            self.action_queue.clear()
            return self._defense(frame, soul)

        self.previous_soul = None
        self.previous_threats = ()
        if self.action_queue:
            action_name = self.action_queue.popleft()
            self.phase = "command"
            self.reason = "continue bounded visible battle-menu input sequence"
            return ACTIONS[action_name]

        if self.pending_signature is not None:
            self.pending_settle += 1
            if self.pending_settle < MENU_SETTLE_STEPS:
                self.reason = "wait briefly for visible battle-menu consequence"
                return ACTIONS["wait"]
            self._note_menu_result(signature, soul)

        self.last_menu_signature = signature
        return self._start_menu_trial(signature)

    def summary(self) -> dict[str, object]:
        learned_menus = sum(
            memory.successful_pattern is not None for memory in self.menu_memory.values()
        )
        return {
            "version": BATTLE_SYSTEM_VERSION,
            "phase": self.phase,
            "visible_menu_states": len(self.menu_memory),
            "learned_menu_states": learned_menus,
            "turns_advanced": self.turns_advanced,
            "menu_failures": self.menu_failures,
            "defense_steps": self.defense_steps,
            "mode_steps": dict(sorted(self.mode_steps.items())),
            "yellow_shots": self.yellow_shots,
            "green_blocks": self.green_blocks,
            "visual_invalid_waits": self.visual_invalid_waits,
        }


def install_battle_v2() -> None:
    """Swap HierarchicalPolicy's controller factory and expose diagnostics."""

    from . import hierarchical_policy as policy_module

    if getattr(policy_module, "_battle_v2_installed", False):
        return
    original_summary = policy_module.HierarchicalPolicy.summary

    def summary(policy) -> dict:
        result = original_summary(policy)
        battle = getattr(policy, "battle", None)
        if isinstance(battle, BattleV2Controller):
            result["battle_system"] = battle.summary()
        return result

    policy_module.BattleController = BattleV2Controller
    policy_module.HierarchicalPolicy.summary = summary
    policy_module._battle_v2_installed = True


__all__ = [
    "BATTLE_SYSTEM_VERSION",
    "BattleV2Controller",
    "MENU_PATTERNS",
    "SoulObservation",
    "install_battle_v2",
]
