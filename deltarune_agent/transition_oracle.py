"""Developer-only room-transition oracle and detector evaluator.

This module is intentionally outside the gameplay decision path.  It consumes
trusted developer-side transition observations (currently the transition
metadata already emitted by telemetry) and compares them with independently
recorded detector predictions.

The oracle is never imported by policy, memory, prompts, population genomes,
or action selection.  Its records are evaluation-only evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Iterable, Mapping, Sequence


ORACLE_VERSION = 1


@dataclass(frozen=True)
class TransitionTruth:
    """Ground-truth transition known only to developer-side evaluation."""

    sequence: int
    source_room: str
    destination_room: str
    source_x: float | None = None
    source_y: float | None = None
    destination_x: float | None = None
    destination_y: float | None = None
    observed_at: float | None = None
    evidence: str = "telemetry-transition"


@dataclass(frozen=True)
class TransitionPrediction:
    """A prediction produced by the AI's independent transition detector."""

    sequence: int
    source_room: str | None
    destination_room: str | None
    predicted_at: float | None = None
    confidence: float = 0.0


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


def _finite_float(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _stable_room(value: object) -> str | None:
    room = str(value or "").strip()
    return room if room and room.casefold() != "unknown" else None


def _field(sample: object, name: str) -> object:\n    if isinstance(sample, Mapping):\n        return sample.get(name)\n    return getattr(sample, name, None)\n\n\ndef build_oracle_from_telemetry(samples: Iterable[object]) -> list[TransitionTruth]:
    """Extract trusted transition truth from developer-side telemetry samples.

    A transition is accepted only when the telemetry packet explicitly carries
    a source room and transition sequence.  This avoids manufacturing truth
    from a room-name change that could itself be caused by packet loss or
    ordering artifacts.
    """

    result: list[TransitionTruth] = []
    seen: set[tuple[int, str, str]] = set()

    for sample in samples:
        source = _stable_room(_field(sample, "transition_from_room_name"))
        destination = _stable_room(_field(sample, "room_name"))
        sequence = _field(sample, "transition_sequence")
        if source is None or destination is None or source == destination:
            continue
        try:
            sequence = int(sequence)
        except (TypeError, ValueError):
            continue
        key = (sequence, source, destination)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            TransitionTruth(
                sequence=sequence,
                source_room=source,
                destination_room=destination,
                source_x=_finite_float(
                    getattr(sample, "transition_from_x", None)
                ),
                source_y=_finite_float(
                    getattr(sample, "transition_from_y", None)
                ),
                destination_x=_finite_float(_field(sample, "x")),
                destination_y=_finite_float(_field(sample, "y")),
                observed_at=_finite_float(_field(sample, "received_at")),
            )
        )

    return sorted(result, key=lambda item: (item.sequence, item.observed_at or 0.0))


def _distance(a: TransitionTruth, b: TransitionPrediction) -> float:
    """Room-transition matching distance.

    Sequence is the primary identity.  Room pair is the semantic identity.
    This helper returns a deterministic ordering score rather than pretending
    that arbitrary time windows are ground truth.
    """

    sequence_distance = abs(a.sequence - b.sequence)
    source_penalty = 0 if (
        a.source_room == b.source_room
        or b.source_room is None
    ) else 100000
    destination_penalty = 0 if (
        a.destination_room == b.destination_room
        or b.destination_room is None
    ) else 100000
    return sequence_distance + source_penalty + destination_penalty


def evaluate_transitions(
    truth: Sequence[TransitionTruth],
    predictions: Sequence[TransitionPrediction],
) -> TransitionEvaluation:
    """Match detector predictions to oracle transitions deterministically.

    Each prediction can match at most one truth record.  Matching requires the
    transition sequence to be equal and the destination to agree.  A prediction
    with the right sequence but wrong destination is counted separately as a
    wrong destination rather than silently becoming a false positive.
    """

    available = list(truth)
    matched_records: list[dict[str, object]] = []
    wrong_destinations = 0
    false_positives = 0
    latency_values: list[float] = []

    for prediction in sorted(predictions, key=lambda item: item.sequence):
        same_sequence = [
            item for item in available if item.sequence == prediction.sequence
        ]
        if not same_sequence:
            false_positives += 1
            continue

        exact = [
            item for item in same_sequence
            if item.destination_room == prediction.destination_room
            and (
                prediction.source_room is None
                or item.source_room == prediction.source_room
            )
        ]
        if exact:
            candidate = min(exact, key=lambda item: _distance(item, prediction))
            available.remove(candidate)
            latency = None
            if (
                candidate.observed_at is not None
                and prediction.predicted_at is not None
            ):
                latency = prediction.predicted_at - candidate.observed_at
                latency_values.append(latency)
            matched_records.append(
                {
                    "sequence": candidate.sequence,
                    "source_room": candidate.source_room,
                    "destination_room": candidate.destination_room,
                    "latency_seconds": latency,
                    "confidence": prediction.confidence,
                }
            )
            continue

        wrong_destinations += 1

    missed = len(available)
    matched = len(matched_records)
    predicted = len(predictions)
    precision = matched / predicted if predicted else 0.0
    recall = matched / len(truth) if truth else 1.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )

    return TransitionEvaluation(
        oracle_count=len(truth),
        prediction_count=predicted,
        matched=matched,
        missed=missed,
        false_positives=false_positives,
        wrong_destinations=wrong_destinations,
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
        mean_latency_seconds=(
            round(sum(latency_values) / len(latency_values), 6)
            if latency_values else None
        ),
        max_latency_seconds=(
            round(max(latency_values), 6)
            if latency_values else None
        ),
        matches=tuple(matched_records),
    )


def write_oracle(path: Path, transitions: Sequence[TransitionTruth]) -> Path:
    """Persist developer-only oracle evidence for later evaluation."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": ORACLE_VERSION,
        "purpose": "developer_only_transition_evaluation",
        "transitions": [asdict(item) for item in transitions],
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_oracle(path: Path) -> list[TransitionTruth]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Transition oracle must contain a JSON object")
    if payload.get("version") != ORACLE_VERSION:
        raise ValueError("Unsupported transition oracle version")
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
