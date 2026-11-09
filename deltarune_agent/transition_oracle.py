"""Developer-only room-transition oracle and evaluator.

This module is post-run evaluation infrastructure. Never import it from the
live policy, memory, prompt, perception, or action-selection code.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Iterable, Mapping, Sequence


ORACLE_VERSION = 2


@dataclass(frozen=True)
class TransitionTruth:
    sequence: int
    source_room: str
    destination_room: str
    source_x: float | None = None
    source_y: float | None = None
    destination_x: float | None = None
    destination_y: float | None = None
    observed_at: float | None = None
    step: int | None = None
    evidence: str = "telemetry-transition"


@dataclass(frozen=True)
class TransitionPrediction:
    sequence: int
    source_room: str | None
    destination_room: str | None
    predicted_at: float | None = None
    confidence: float = 0.0
    step: int | None = None


@dataclass(frozen=True)
class TransitionEvaluation:
    oracle_count: int
    prediction_count: int
    matched: int
    missed: int
    false_positives: int
    wrong_destinations: int
    precision: float
    recall: float
    f1: float
    mean_latency_seconds: float | None
    max_latency_seconds: float | None
    matches: tuple[dict[str, object], ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _field(sample: object, name: str) -> object:
    if isinstance(sample, Mapping):
        return sample.get(name)
    return getattr(sample, name, None)


def _finite_float(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _stable_room(value: object) -> str | None:
    room = str(value or "").strip()
    return room if room and room.casefold() != "unknown" else None


def build_oracle_from_telemetry(samples: Iterable[object]) -> list[TransitionTruth]:
    """Extract only explicitly tagged, developer-trusted telemetry transitions.

    Each input may be a TelemetrySample or a recorded telemetry dictionary.
    Recorded event metadata may supply _run_step and _run_elapsed_seconds.
    """
    result: list[TransitionTruth] = []
    seen: set[tuple[int, str, str]] = set()
    for sample in samples:
        source = _stable_room(_field(sample, "transition_from_room_name"))
        destination = _stable_room(_field(sample, "room_name"))
        raw_sequence = _field(sample, "transition_sequence")
        if source is None or destination is None or source == destination:
            continue
        try:
            sequence = int(raw_sequence)
        except (TypeError, ValueError, OverflowError):
            continue
        key = (sequence, source, destination)
        if key in seen:
            continue
        seen.add(key)
        observed_at = _finite_float(_field(sample, "received_at"))
        if observed_at is None:
            observed_at = _finite_float(_field(sample, "_run_elapsed_seconds"))
        raw_step = _field(sample, "_run_step")
        try:
            step = int(raw_step) if raw_step is not None else None
        except (TypeError, ValueError, OverflowError):
            step = None
        result.append(
            TransitionTruth(
                sequence=sequence,
                source_room=source,
                destination_room=destination,
                source_x=_finite_float(_field(sample, "transition_from_foot_x"))
                or _finite_float(_field(sample, "transition_from_x")),
                source_y=_finite_float(_field(sample, "transition_from_foot_y"))
                or _finite_float(_field(sample, "transition_from_y")),
                destination_x=_finite_float(_field(sample, "player_foot_x"))
                or _finite_float(_field(sample, "player_x"))
                or _finite_float(_field(sample, "x")),
                destination_y=_finite_float(_field(sample, "player_foot_y"))
                or _finite_float(_field(sample, "player_y"))
                or _finite_float(_field(sample, "y")),
                observed_at=observed_at,
                step=step,
            )
        )
    return sorted(
        result,
        key=lambda item: (
            item.step if item.step is not None else 2**63,
            item.sequence,
            item.observed_at or 0.0,
        ),
    )


def evaluate_transitions(
    truth: Sequence[TransitionTruth],
    predictions: Sequence[TransitionPrediction],
    *,
    step_tolerance: int = 2,
) -> TransitionEvaluation:
    """One-to-one match by run step when available, else telemetry sequence.

    A matching step with a different destination is counted as a wrong
    destination. A prediction with no nearby truth is a false positive.
    """
    available = list(truth)
    matches: list[dict[str, object]] = []
    wrong_destinations = 0
    false_positives = 0
    latencies: list[float] = []

    for prediction in sorted(
        predictions,
        key=lambda item: (
            item.step if item.step is not None else 2**63,
            item.sequence,
        ),
    ):
        candidates = [
            item for item in available
            if (
                abs(item.step - prediction.step) <= step_tolerance
                if item.step is not None and prediction.step is not None
                else item.sequence == prediction.sequence
            )
        ]
        if not candidates:
            false_positives += 1
            continue

        exact = [
            item for item in candidates
            if item.destination_room == prediction.destination_room
            and (
                prediction.source_room is None
                or item.source_room == prediction.source_room
            )
        ]
        if not exact:
            wrong_destinations += 1
            # Consume the nearest truth so a wrong prediction cannot also
            # inflate the missed count for the same event.
            available.remove(min(
                candidates,
                key=lambda item: abs(
                    (item.step if item.step is not None else item.sequence)
                    - (prediction.step if prediction.step is not None else prediction.sequence)
                ),
            ))
            continue

        candidate = min(
            exact,
            key=lambda item: abs(
                (item.step or item.sequence)
                - (prediction.step or prediction.sequence)
            ),
        )
        available.remove(candidate)
        latency = None
        if candidate.observed_at is not None and prediction.predicted_at is not None:
            latency = prediction.predicted_at - candidate.observed_at
            latencies.append(latency)
        matches.append({
            "sequence": candidate.sequence,
            "step": candidate.step,
            "source_room": candidate.source_room,
            "destination_room": candidate.destination_room,
            "latency_seconds": latency,
            "confidence": prediction.confidence,
        })

    matched = len(matches)
    missed = len(available)
    predicted_count = len(predictions)
    precision = matched / predicted_count if predicted_count else 0.0
    recall = matched / len(truth) if truth else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return TransitionEvaluation(
        oracle_count=len(truth),
        prediction_count=predicted_count,
        matched=matched,
        missed=missed,
        false_positives=false_positives,
        wrong_destinations=wrong_destinations,
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
        mean_latency_seconds=round(sum(latencies) / len(latencies), 6) if latencies else None,
        max_latency_seconds=round(max(latencies), 6) if latencies else None,
        matches=tuple(matches),
    )


def write_oracle(path: Path, transitions: Sequence[TransitionTruth]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": ORACLE_VERSION,
        "purpose": "developer_only_transition_evaluation",
        "transitions": [asdict(item) for item in transitions],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_oracle(path: Path) -> list[TransitionTruth]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or payload.get("version") != ORACLE_VERSION:
        raise ValueError("Unsupported or invalid transition oracle")
    rows = payload.get("transitions")
    if not isinstance(rows, list):
        raise ValueError("Transition oracle transitions must be a list")
    return [TransitionTruth(**row) for row in rows if isinstance(row, dict)]


def write_evaluation(path: Path, evaluation: TransitionEvaluation) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(evaluation.as_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path
