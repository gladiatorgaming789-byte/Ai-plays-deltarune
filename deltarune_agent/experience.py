"""Training experience records for the neural Deltarune agent.

This module is intentionally policy-agnostic. It records transitions between
numerical observations without deciding what constitutes a reward. Reward
calculation belongs to the environment/controller layer so that training
logic cannot accidentally smuggle developer-only labels into the agent.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import random
from pathlib import Path
from typing import Iterable

from .actions import ACTIONS
from .neural_env import NeuralObservation


@dataclass(frozen=True)
class NeuralTransition:
    """One environment transition suitable for offline or online learning."""

    observation: tuple[float, ...]
    available: tuple[float, ...]
    action: str
    reward: float
    next_observation: tuple[float, ...]
    next_available: tuple[float, ...]
    done: bool
    source_sequence: int | None = None
    next_source_sequence: int | None = None

    def __post_init__(self) -> None:
        if self.action not in ACTIONS:
            raise ValueError(f"unknown neural action: {self.action!r}")
        if len(self.observation) != len(self.available):
            raise ValueError("observation and availability widths differ")
        if len(self.next_observation) != len(self.next_available):
            raise ValueError("next observation and availability widths differ")
        if not all(isinstance(value, (int, float)) for value in self.observation):
            raise ValueError("observation contains a non-numeric value")
        if not all(isinstance(value, (int, float)) for value in self.next_observation):
            raise ValueError("next observation contains a non-numeric value")

    @classmethod
    def from_observations(
        cls,
        observation: NeuralObservation,
        action: str,
        reward: float,
        next_observation: NeuralObservation,
        *,
        done: bool = False,
    ) -> "NeuralTransition":
        return cls(
            observation=observation.values,
            available=observation.available,
            action=action,
            reward=float(reward),
            next_observation=next_observation.values,
            next_available=next_observation.available,
            done=bool(done),
            source_sequence=observation.source_sequence,
            next_source_sequence=next_observation.source_sequence,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "observation": list(self.observation),
            "available": list(self.available),
            "action": self.action,
            "reward": self.reward,
            "reward_status": "unassigned",
            "next_observation": list(self.next_observation),
            "next_available": list(self.next_available),
            "done": self.done,
            "source_sequence": self.source_sequence,
            "next_source_sequence": self.next_source_sequence,
        }


class NeuralReplayBuffer:
    """Bounded replay storage with deterministic sampling support."""

    def __init__(self, capacity: int = 100_000) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = int(capacity)
        self._items: list[NeuralTransition] = []

    def __len__(self) -> int:
        return len(self._items)

    @property
    def full(self) -> bool:
        return len(self._items) >= self.capacity

    def add(self, transition: NeuralTransition) -> None:
        if self.full:
            self._items.pop(0)
        self._items.append(transition)

    def extend(self, transitions: Iterable[NeuralTransition]) -> None:
        for transition in transitions:
            self.add(transition)

    def sample(self, batch_size: int, *, rng: random.Random | None = None) -> list[NeuralTransition]:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if batch_size > len(self._items):
            raise ValueError(
                f"cannot sample {batch_size} transitions from {len(self._items)}"
            )
        sampler = rng or random
        return sampler.sample(self._items, batch_size)

    def snapshot(self) -> tuple[NeuralTransition, ...]:
        return tuple(self._items)

    def clear(self) -> None:
        self._items.clear()


def write_transitions(path: Path, transitions: Iterable[NeuralTransition]) -> Path:
    """Write newline-delimited JSON transitions for offline training."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for transition in transitions:
            handle.write(json.dumps(transition.as_dict(), separators=(",", ":")) + "\n")
    temporary.replace(path)
    return path


def read_transitions(path: Path) -> list[NeuralTransition]:
    """Read newline-delimited JSON transitions with basic structural validation."""
    result: list[NeuralTransition] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                result.append(NeuralTransition(
                    observation=tuple(raw["observation"]),
                    available=tuple(raw["available"]),
                    action=str(raw["action"]),
                    reward=float(raw["reward"]),
                    next_observation=tuple(raw["next_observation"]),
                    next_available=tuple(raw["next_available"]),
                    done=bool(raw["done"]),
                    source_sequence=raw.get("source_sequence"),
                    next_source_sequence=raw.get("next_source_sequence"),
                ))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid transition on line {line_number}") from exc
    return result


class NeuralExperienceWriter:
    """Append-only writer for telemetry-aligned neural transitions."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a", encoding="utf-8")
        self.count = 0

    def write(self, transition: NeuralTransition) -> None:
        if self._handle.closed:
            raise RuntimeError("experience writer is closed")
        self._handle.write(json.dumps(
            transition.as_dict(),
            separators=(",", ":"),
            allow_nan=False,
        ) + "\\n")
        self.count += 1
        self._handle.flush()

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.flush()
            self._handle.close()
