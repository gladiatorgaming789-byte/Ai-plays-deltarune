from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Mapping
from uuid import uuid4


STRATEGY_SCHEMA_VERSION = 1
STRATEGY_FILENAME = "strategy.json"
MIN_POPULATION_SIZE = 2
DEFAULT_POPULATION_SIZE = 4
MAX_POPULATION_SIZE = 16


def _coefficient(value: object, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    return min(10.0, max(0.0, number))


@dataclass(frozen=True)
class StrategyGenome:
    """Versioned coefficients for the Autonomy option scorer.

    The defaults are the exact constants used before strategy genomes existed.
    Consequently a missing ``strategy.json`` remains behavior-compatible.
    ``reinforcement_influence`` scales only candidate-local learning deltas in
    population mode; it does not add a new term to an ordinary run.
    """

    schema_version: int = STRATEGY_SCHEMA_VERSION
    confidence: float = 3.0
    information: float = 2.8
    novelty: float = 2.0
    distance_cost: float = 0.30
    loop_cost: float = 4.0
    failure_cost: float = 1.3
    budget_cost: float = 2.2
    reinforcement_influence: float = 1.0

    @classmethod
    def default(cls) -> "StrategyGenome":
        return cls()

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "StrategyGenome":
        defaults = cls.default()
        return cls(
            schema_version=STRATEGY_SCHEMA_VERSION,
            confidence=_coefficient(payload.get("confidence"), defaults.confidence),
            information=_coefficient(payload.get("information"), defaults.information),
            novelty=_coefficient(payload.get("novelty"), defaults.novelty),
            distance_cost=_coefficient(payload.get("distance_cost"), defaults.distance_cost),
            loop_cost=_coefficient(payload.get("loop_cost"), defaults.loop_cost),
            failure_cost=_coefficient(payload.get("failure_cost"), defaults.failure_cost),
            budget_cost=_coefficient(payload.get("budget_cost"), defaults.budget_cost),
            reinforcement_influence=_coefficient(
                payload.get("reinforcement_influence"),
                defaults.reinforcement_influence,
            ),
        )

    @classmethod
    def load(cls, path: Path | None) -> tuple["StrategyGenome", str | None]:
        if path is None or not path.is_file():
            return cls.default(), None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError("strategy root must be an object")
            return cls.from_mapping(payload), None
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return cls.default(), f"Could not load strategy genome: {exc}"

    def clamped(self) -> "StrategyGenome":
        return StrategyGenome.from_mapping(asdict(self))

    def mutate(self, **multipliers: float) -> "StrategyGenome":
        values = self.to_dict()
        for name, multiplier in multipliers.items():
            if name not in values or name == "schema_version":
                raise KeyError(f"Unknown strategy coefficient: {name}")
            values[name] = _coefficient(float(values[name]) * float(multiplier), 0.0)
        return StrategyGenome.from_mapping(values)

    def to_dict(self) -> dict[str, object]:
        return asdict(self.clamped())

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    def score(
        self,
        *,
        base_score: float,
        confidence: float,
        information_value: float,
        novelty: float,
        distance: float,
        loop_risk: float,
        failure_cost: float,
        budget_fraction: float = 0.0,
        reinforcement_delta: float = 0.0,
    ) -> float:
        score = (
            float(base_score)
            + float(confidence) * self.confidence
            + float(information_value) * self.information
            + float(novelty) * self.novelty
            - min(12.0, max(0.0, float(distance))) * self.distance_cost
            - float(loop_risk) * self.loop_cost
            - float(failure_cost) * self.failure_cost
            - max(0.0, float(budget_fraction)) * self.budget_cost
            + float(reinforcement_delta) * self.reinforcement_influence
        )
        return round(score, 4)


POPULATION_VARIANTS: tuple[tuple[str, str, Mapping[str, float]], ...] = (
    ("balanced", "Balanced", {}),
    (
        "explorer",
        "Explorer",
        {
            "information": 1.50,
            "novelty": 1.70,
            "distance_cost": 0.75,
            "loop_cost": 0.90,
            "failure_cost": 0.80,
            "budget_cost": 0.75,
            "reinforcement_influence": 0.80,
        },
    ),
    (
        "progress",
        "Progress",
        {
            "confidence": 1.25,
            "information": 0.80,
            "novelty": 0.65,
            "distance_cost": 1.25,
            "loop_cost": 1.05,
            "failure_cost": 0.90,
            "budget_cost": 1.15,
            "reinforcement_influence": 1.50,
        },
    ),
    (
        "loop_safe",
        "Loop-safe",
        {
            "information": 0.90,
            "novelty": 0.90,
            "distance_cost": 0.90,
            "loop_cost": 1.75,
            "failure_cost": 1.50,
            "budget_cost": 1.25,
        },
    ),
)


def validate_population_size(value: object) -> int:
    """Return a supported population size or raise a useful error."""

    try:
        size = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("population size must be an integer") from exc
    if not MIN_POPULATION_SIZE <= size <= MAX_POPULATION_SIZE:
        raise ValueError(
            f"population size must be between {MIN_POPULATION_SIZE} and "
            f"{MAX_POPULATION_SIZE}"
        )
    return size


def _scaled_multipliers(
    multipliers: Mapping[str, float],
    intensity: float,
) -> dict[str, float]:
    """Move a named strategy farther from neutral without changing its shape."""

    return {
        name: max(0.0, 1.0 + (float(multiplier) - 1.0) * float(intensity))
        for name, multiplier in multipliers.items()
    }


def population_genomes(
    baseline: StrategyGenome,
    size: int = DEFAULT_POPULATION_SIZE,
) -> tuple[tuple[str, str, StrategyGenome], ...]:
    """Build a deterministic, bounded population around ``baseline``.

    Sizes two through four are prefixes of the original v1 population, so the
    default remains byte-for-byte behavior-compatible. Larger populations add
    progressively stronger Explorer, Progress, and Loop-safe family members.
    They are deliberately deterministic so a recorded size and baseline genome
    reproduce the same candidates in later analysis.
    """

    requested = validate_population_size(size)
    candidates = [
        (candidate_id, label, baseline.mutate(**dict(multipliers)))
        for candidate_id, label, multipliers in POPULATION_VARIANTS[:requested]
    ]
    extra_families = POPULATION_VARIANTS[1:]
    for extra_index in range(max(0, requested - len(POPULATION_VARIANTS))):
        family_id, family_label, multipliers = extra_families[
            extra_index % len(extra_families)
        ]
        tier = extra_index // len(extra_families) + 1
        intensity = 1.0 + tier * 0.25
        intensity_id = int(round(intensity * 100))
        candidates.append(
            (
                f"{family_id}_{intensity_id}",
                f"{family_label} {intensity:.2f}x",
                baseline.mutate(**_scaled_multipliers(multipliers, intensity)),
            )
        )
    return tuple(candidates)


__all__ = [
    "DEFAULT_POPULATION_SIZE",
    "MAX_POPULATION_SIZE",
    "MIN_POPULATION_SIZE",
    "POPULATION_VARIANTS",
    "STRATEGY_FILENAME",
    "STRATEGY_SCHEMA_VERSION",
    "StrategyGenome",
    "population_genomes",
    "validate_population_size",
]
