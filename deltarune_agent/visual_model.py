from __future__ import annotations

from dataclasses import dataclass
import json
from math import exp
from pathlib import Path


@dataclass
class RunningPrototype:
    count: int
    mean: list[float]
    m2: list[float]

    @classmethod
    def empty(cls, dimensions: int) -> RunningPrototype:
        return cls(0, [0.0] * dimensions, [0.0] * dimensions)

    def update(self, vector: tuple[float, ...]) -> None:
        if len(vector) != len(self.mean):
            raise ValueError("visual embedding dimension changed")
        self.count += 1
        for index, value in enumerate(vector):
            delta = value - self.mean[index]
            self.mean[index] += delta / self.count
            self.m2[index] += delta * (value - self.mean[index])

    def distance(self, vector: tuple[float, ...]) -> float:
        if len(vector) != len(self.mean):
            return float("inf")
        total = 0.0
        for index, value in enumerate(vector):
            variance = self.m2[index] / max(1, self.count - 1)
            # A variance floor prevents a static HUD pixel from dominating.
            total += (value - self.mean[index]) ** 2 / max(variance, 0.0025)
        return total / len(vector)


class OnlineVisualModel:
    """Incremental grayscale classifier trained by authoritative telemetry."""

    VERSION = 1
    MIN_SAMPLES = 6
    MAX_DISTANCE = 4.0

    def __init__(self, path: Path | None = None):
        self.path = path
        self.prototypes: dict[str, RunningPrototype] = {}
        self.load_warning: str | None = None

    @classmethod
    def load(cls, path: Path | None) -> OnlineVisualModel:
        model = cls(path)
        if path is None or not path.exists():
            return model
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("version") != cls.VERSION:
                raise ValueError(f"unsupported visual-model version {data.get('version')!r}")
            for label, item in data.get("prototypes", {}).items():
                mean = [float(value) for value in item["mean"]]
                m2 = [float(value) for value in item["m2"]]
                if len(mean) != len(m2) or not mean:
                    raise ValueError(f"invalid prototype for {label}")
                model.prototypes[str(label)] = RunningPrototype(
                    int(item["count"]), mean, m2
                )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            model = cls(path)
            model.load_warning = f"Could not load {path}: {exc}. Starting a new visual model."
        return model

    def update(self, label: str, vector: tuple[float, ...]) -> None:
        prototype = self.prototypes.get(label)
        if prototype is None:
            prototype = RunningPrototype.empty(len(vector))
            self.prototypes[label] = prototype
        prototype.update(vector)

    def predict(self, vector: tuple[float, ...]) -> tuple[str, float, float] | None:
        candidates = [
            (prototype.distance(vector), label)
            for label, prototype in self.prototypes.items()
            if prototype.count >= self.MIN_SAMPLES
        ]
        if not candidates:
            return None
        candidates.sort()
        best_distance, best_label = candidates[0]
        if best_distance > self.MAX_DISTANCE:
            return None
        absolute = 1.0 / (1.0 + exp(best_distance - 1.5))
        if len(candidates) > 1:
            margin = max(0.0, candidates[1][0] - best_distance)
            separation = 1.0 - exp(-margin)
        else:
            separation = 0.65
        confidence = min(0.98, 0.50 + 0.28 * absolute + 0.20 * separation)
        return best_label, confidence, best_distance

    def save(self) -> None:
        if self.path is None:
            return
        data = {
            "version": self.VERSION,
            "prototypes": {
                label: {
                    "count": prototype.count,
                    "mean": prototype.mean,
                    "m2": prototype.m2,
                }
                for label, prototype in sorted(self.prototypes.items())
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary.replace(self.path)
