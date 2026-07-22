from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass
import json
from math import log, sqrt
from pathlib import Path
from typing import Mapping
from uuid import uuid4


SETTINGS_SCHEMA_VERSION = 1
MEMORY_SCHEMA_VERSION = 1
REINFORCEMENT_SETTINGS_FILENAME = "reinforcement_settings.json"
REINFORCEMENT_MEMORY_FILENAME = "reinforcement.json"
DEFAULT_PRESET = "Normal"
CUSTOM_PRESET = "Custom"

REWARD_FIELD_SPECS: tuple[tuple[str, str, str], ...] = (
    ("story_progress", "Story progress", "Observed non-discovery story progress."),
    (
        "interaction_progress",
        "Interaction progress",
        "Exact interaction that caused a scripted sequence, battle, or room change.",
    ),
    ("choice_progress", "Choice progress", "Exact response pattern that led to progress."),
    ("warp_progress", "Warp progress", "Portal followed shortly before observed progress."),
    ("room_discovery", "Room discovery", "First observed entry into a room."),
    (
        "information_gain",
        "Information gain",
        "First confirmation that a target is actually interactable.",
    ),
    (
        "ordinary_dialogue",
        "Ordinary dialogue",
        "Dialogue ended without an observed story consequence.",
    ),
    ("no_response", "No response", "An attempted interaction produced no state change."),
    (
        "choice_failure",
        "Choice failure",
        "A response returned to play without observed progress.",
    ),
    (
        "immediate_backtrack",
        "Backtrack",
        "A learned portal was used as a return leg without new progress.",
    ),
    ("navigation_loop", "Navigation loop", "The loop detector had to force an escape."),
    ("step_cost", "Step cost", "Small cost applied while a high-level action remains active."),
)

PRESETS: dict[str, dict[str, object]] = {
    "Normal": {
        "enabled": True,
        "exploration_constant": 1.10,
        "eligibility_decay": 0.65,
        "trace_length": 6,
        "decision_repeat_steps": 12,
        "rewards": {
            "story_progress": 10.0,
            "interaction_progress": 4.0,
            "choice_progress": 5.0,
            "warp_progress": 3.0,
            "room_discovery": 1.5,
            "information_gain": 0.5,
            "ordinary_dialogue": -0.20,
            "no_response": -0.80,
            "choice_failure": -0.60,
            "immediate_backtrack": -1.0,
            "navigation_loop": -2.0,
            "step_cost": -0.002,
        },
    },
    "Explore": {
        "enabled": True,
        "exploration_constant": 1.85,
        "eligibility_decay": 0.72,
        "trace_length": 8,
        "decision_repeat_steps": 10,
        "rewards": {
            "story_progress": 8.0,
            "interaction_progress": 3.0,
            "choice_progress": 4.0,
            "warp_progress": 2.0,
            "room_discovery": 4.0,
            "information_gain": 1.5,
            "ordinary_dialogue": -0.05,
            "no_response": -0.30,
            "choice_failure": -0.30,
            "immediate_backtrack": -0.40,
            "navigation_loop": -1.20,
            "step_cost": -0.0005,
        },
    },
    "Speedrun": {
        "enabled": True,
        "exploration_constant": 0.45,
        "eligibility_decay": 0.52,
        "trace_length": 5,
        "decision_repeat_steps": 18,
        "rewards": {
            "story_progress": 14.0,
            "interaction_progress": 6.0,
            "choice_progress": 7.0,
            "warp_progress": 5.0,
            "room_discovery": 0.50,
            "information_gain": 0.10,
            "ordinary_dialogue": -0.80,
            "no_response": -2.0,
            "choice_failure": -1.50,
            "immediate_backtrack": -3.0,
            "navigation_loop": -4.0,
            "step_cost": -0.010,
        },
    },
}


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _number(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _integer(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def preset_names() -> tuple[str, ...]:
    return tuple(PRESETS)


@dataclass(frozen=True)
class RewardSettings:
    enabled: bool
    preset: str
    exploration_constant: float
    eligibility_decay: float
    trace_length: int
    decision_repeat_steps: int
    rewards: dict[str, float]

    @classmethod
    def for_preset(cls, name: str = DEFAULT_PRESET) -> "RewardSettings":
        chosen = name if name in PRESETS else DEFAULT_PRESET
        raw = deepcopy(PRESETS[chosen])
        return cls(
            enabled=bool(raw["enabled"]),
            preset=chosen,
            exploration_constant=float(raw["exploration_constant"]),
            eligibility_decay=float(raw["eligibility_decay"]),
            trace_length=int(raw["trace_length"]),
            decision_repeat_steps=int(raw["decision_repeat_steps"]),
            rewards={
                str(key): float(value)
                for key, value in dict(raw["rewards"]).items()
            },
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "RewardSettings":
        requested_preset = str(payload.get("preset") or DEFAULT_PRESET)
        base = cls.for_preset(
            requested_preset if requested_preset in PRESETS else DEFAULT_PRESET
        )
        raw_rewards = payload.get("rewards")
        rewards = dict(base.rewards)
        if isinstance(raw_rewards, Mapping):
            for key, _label, _help in REWARD_FIELD_SPECS:
                if key in raw_rewards:
                    rewards[key] = _number(raw_rewards[key], rewards[key])
        settings = cls(
            enabled=bool(payload.get("enabled", base.enabled)),
            preset=requested_preset,
            exploration_constant=_number(
                payload.get("exploration_constant"), base.exploration_constant
            ),
            eligibility_decay=_number(
                payload.get("eligibility_decay"), base.eligibility_decay
            ),
            trace_length=_integer(payload.get("trace_length"), base.trace_length),
            decision_repeat_steps=_integer(
                payload.get("decision_repeat_steps"), base.decision_repeat_steps
            ),
            rewards=rewards,
        )
        settings.validate()
        detected = settings.detect_preset()
        return cls(
            enabled=settings.enabled,
            preset=detected,
            exploration_constant=settings.exploration_constant,
            eligibility_decay=settings.eligibility_decay,
            trace_length=settings.trace_length,
            decision_repeat_steps=settings.decision_repeat_steps,
            rewards=dict(settings.rewards),
        )

    def validate(self) -> None:
        if not 0.0 <= self.exploration_constant <= 10.0:
            raise ValueError("Exploration constant must be between 0 and 10.")
        if not 0.0 <= self.eligibility_decay <= 1.0:
            raise ValueError("Eligibility decay must be between 0 and 1.")
        if not 1 <= self.trace_length <= 32:
            raise ValueError("Trace length must be between 1 and 32.")
        if not 1 <= self.decision_repeat_steps <= 240:
            raise ValueError("Decision repeat steps must be between 1 and 240.")
        expected = {key for key, _label, _help in REWARD_FIELD_SPECS}
        missing = expected - set(self.rewards)
        if missing:
            raise ValueError(f"Missing reward values: {', '.join(sorted(missing))}")
        for key in expected:
            value = float(self.rewards[key])
            if not -1000.0 <= value <= 1000.0:
                raise ValueError(f"Reward {key!r} must be between -1000 and 1000.")

    def reward(self, name: str) -> float:
        return float(self.rewards.get(name, 0.0))

    def detect_preset(self) -> str:
        for name in PRESETS:
            preset = RewardSettings.for_preset(name)
            if (
                self.enabled == preset.enabled
                and abs(self.exploration_constant - preset.exploration_constant)
                < 1e-9
                and abs(self.eligibility_decay - preset.eligibility_decay) < 1e-9
                and self.trace_length == preset.trace_length
                and self.decision_repeat_steps == preset.decision_repeat_steps
                and all(
                    abs(self.reward(key) - preset.reward(key)) < 1e-9
                    for key, _label, _help in REWARD_FIELD_SPECS
                )
            ):
                return name
        return CUSTOM_PRESET

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "schema_version": SETTINGS_SCHEMA_VERSION,
            "enabled": self.enabled,
            "preset": self.detect_preset(),
            "exploration_constant": self.exploration_constant,
            "eligibility_decay": self.eligibility_decay,
            "trace_length": self.trace_length,
            "decision_repeat_steps": self.decision_repeat_steps,
            "rewards": {
                key: self.reward(key) for key, _label, _help in REWARD_FIELD_SPECS
            },
        }


def load_reward_settings(path: Path | None) -> RewardSettings:
    if path is None or not path.exists():
        return RewardSettings.for_preset(DEFAULT_PRESET)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("settings root must be an object")
        return RewardSettings.from_dict(payload)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return RewardSettings.for_preset(DEFAULT_PRESET)


def save_reward_settings(path: Path, settings: RewardSettings) -> None:
    _atomic_write_json(path, settings.to_dict())


class ReinforcementMemory:
    """Small persistent contextual-bandit memory with an eligibility trace."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.records: dict[str, dict[str, object]] = {}
        self.total_decisions = 0
        self.total_reward = 0.0
        self.reward_events = 0
        self.trace: deque[dict[str, object]] = deque(maxlen=32)
        self.load_warning: str | None = None
        self._dirty = False

    @classmethod
    def load(cls, path: Path | None) -> "ReinforcementMemory":
        memory = cls(path)
        if path is None or not path.exists():
            return memory
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError("reinforcement memory root must be an object")
            raw_records = payload.get("records")
            if isinstance(raw_records, Mapping):
                for raw_key, raw_record in raw_records.items():
                    if not isinstance(raw_record, Mapping):
                        continue
                    key = str(raw_key)
                    attempts = max(0, _integer(raw_record.get("attempts"), 0))
                    memory.records[key] = {
                        "kind": str(raw_record.get("kind") or "unknown"),
                        "context": dict(raw_record.get("context") or {})
                        if isinstance(raw_record.get("context"), Mapping)
                        else {},
                        "attempts": attempts,
                        "reward_count": max(
                            0, _integer(raw_record.get("reward_count"), 0)
                        ),
                        "total_reward": _number(
                            raw_record.get("total_reward"), 0.0
                        ),
                        "last_reward": _number(raw_record.get("last_reward"), 0.0),
                        "positive_outcomes": max(
                            0, _integer(raw_record.get("positive_outcomes"), 0)
                        ),
                        "negative_outcomes": max(
                            0, _integer(raw_record.get("negative_outcomes"), 0)
                        ),
                        "last_step": max(
                            0, _integer(raw_record.get("last_step"), 0)
                        ),
                        "last_event": str(raw_record.get("last_event") or ""),
                    }
            memory.total_decisions = max(
                0, _integer(payload.get("total_decisions"), 0)
            )
            memory.total_reward = _number(payload.get("total_reward"), 0.0)
            memory.reward_events = max(0, _integer(payload.get("reward_events"), 0))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            memory = cls(path)
            memory.load_warning = f"Could not load reinforcement memory: {exc}"
        return memory

    def _record(
        self,
        key: str,
        *,
        kind: str = "unknown",
        context: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        record = self.records.setdefault(
            key,
            {
                "kind": kind,
                "context": dict(context or {}),
                "attempts": 0,
                "reward_count": 0,
                "total_reward": 0.0,
                "last_reward": 0.0,
                "positive_outcomes": 0,
                "negative_outcomes": 0,
                "last_step": 0,
                "last_event": "",
            },
        )
        if kind and str(record.get("kind") or "unknown") == "unknown":
            record["kind"] = kind
        if context:
            existing = record.get("context")
            if not isinstance(existing, dict):
                existing = {}
                record["context"] = existing
            existing.update({str(name): value for name, value in context.items()})
        return record

    def score(self, key: str, settings: RewardSettings) -> float:
        if not settings.enabled:
            return 0.0
        record = self.records.get(key)
        attempts = max(0, int(record.get("attempts", 0))) if record else 0
        total = float(record.get("total_reward", 0.0)) if record else 0.0
        mean = total / max(1, attempts)
        exploration = settings.exploration_constant * sqrt(
            log(max(2, self.total_decisions + 2)) / (attempts + 1)
        )
        return mean + exploration

    def begin_action(
        self,
        key: str,
        *,
        kind: str,
        context: Mapping[str, object] | None,
        step: int,
        settings: RewardSettings,
    ) -> bool:
        if not settings.enabled:
            return False
        step = max(0, int(step))
        if self.trace:
            latest = self.trace[0]
            if (
                latest.get("key") == key
                and step - int(latest.get("step", 0))
                < settings.decision_repeat_steps
            ):
                return False
        record = self._record(key, kind=kind, context=context)
        record["attempts"] = int(record.get("attempts", 0)) + 1
        record["last_step"] = step
        self.total_decisions += 1
        self.trace.appendleft(
            {
                "key": key,
                "kind": kind,
                "context": dict(context or {}),
                "step": step,
            }
        )
        while len(self.trace) > settings.trace_length:
            self.trace.pop()
        self._dirty = True
        return True

    def reward_key(
        self,
        key: str,
        reward: float,
        *,
        event: str,
        step: int,
        kind: str = "unknown",
        context: Mapping[str, object] | None = None,
    ) -> None:
        reward = float(reward)
        if reward == 0.0:
            return
        record = self._record(key, kind=kind, context=context)
        record["reward_count"] = int(record.get("reward_count", 0)) + 1
        record["total_reward"] = float(record.get("total_reward", 0.0)) + reward
        record["last_reward"] = reward
        record["last_event"] = str(event)
        record["last_step"] = max(0, int(step))
        if reward > 0:
            record["positive_outcomes"] = int(
                record.get("positive_outcomes", 0)
            ) + 1
        elif reward < 0:
            record["negative_outcomes"] = int(
                record.get("negative_outcomes", 0)
            ) + 1
        self._dirty = True

    def reward_trace(
        self,
        reward: float,
        *,
        event: str,
        step: int,
        settings: RewardSettings,
    ) -> None:
        if not settings.enabled or reward == 0.0 or not self.trace:
            return
        seen: set[str] = set()
        for age, entry in enumerate(self.trace):
            key = str(entry.get("key") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            distributed = float(reward) * (settings.eligibility_decay ** age)
            self.reward_key(
                key,
                distributed,
                event=event,
                step=step,
                kind=str(entry.get("kind") or "unknown"),
                context=(
                    entry.get("context")
                    if isinstance(entry.get("context"), Mapping)
                    else None
                ),
            )
        self.total_reward += float(reward)
        self.reward_events += 1
        self._dirty = True

    def clear_trace(self) -> None:
        self.trace.clear()

    def flush(self, *, force: bool = False) -> None:
        if self.path is None or (not self._dirty and not force):
            return
        payload = {
            "schema_version": MEMORY_SCHEMA_VERSION,
            "total_decisions": self.total_decisions,
            "total_reward": round(self.total_reward, 6),
            "reward_events": self.reward_events,
            "records": {
                key: dict(record) for key, record in sorted(self.records.items())
            },
        }
        _atomic_write_json(self.path, payload)
        self._dirty = False

    def summary(self) -> dict[str, object]:
        ranked = sorted(
            self.records.items(),
            key=lambda item: (
                -(
                    float(item[1].get("total_reward", 0.0))
                    / max(1, int(item[1].get("attempts", 0)))
                ),
                -int(item[1].get("attempts", 0)),
                item[0],
            ),
        )
        return {
            "learned_actions": len(self.records),
            "total_decisions": self.total_decisions,
            "reward_events": self.reward_events,
            "total_reward": round(self.total_reward, 3),
            "top_actions": [
                {
                    "key": key,
                    "kind": record.get("kind"),
                    "attempts": int(record.get("attempts", 0)),
                    "mean_reward": round(
                        float(record.get("total_reward", 0.0))
                        / max(1, int(record.get("attempts", 0))),
                        3,
                    ),
                    "last_event": record.get("last_event"),
                }
                for key, record in ranked[:10]
            ],
        }
