"""Developer-only calibration using explicitly adjudicated visual-exit labels.

This module evaluates the existing heuristic score; it does not train a model,
change gameplay thresholds, or expose reviewed labels to gameplay code.
"""
from __future__ import annotations

from collections import Counter
import json
import math
from pathlib import Path
from typing import Mapping, Sequence

CALIBRATION_VERSION = 1
POSITIVE_LABEL = "confirmed_exit"
NEGATIVE_LABEL = "tested_non_exit"
SUPERVISED_LABELS = {POSITIVE_LABEL, NEGATIVE_LABEL}


def _validated_examples(
    rows: Sequence[Mapping[str, object]],
) -> tuple[list[dict[str, object]], dict[str, int]]:
    examples: list[dict[str, object]] = []
    label_counts: Counter[str] = Counter()
    seen_ids: set[str] = set()
    for index, row in enumerate(rows, 1):
        candidate_id = str(row.get("candidate_id") or "").strip()
        if not candidate_id:
            raise ValueError(f"Dataset row {index} has no candidate_id")
        if candidate_id in seen_ids:
            raise ValueError(f"Duplicate candidate_id in dataset: {candidate_id!r}")
        seen_ids.add(candidate_id)

        label = str(row.get("label") or "unknown").strip()
        if label not in {"confirmed_exit", "tested_non_exit", "unreachable", "unknown"}:
            raise ValueError(f"Unsupported dataset label {label!r} for candidate {candidate_id!r}")
        label_counts[label] += 1
        if label not in SUPERVISED_LABELS:
            continue

        raw_score = row.get("visual_score")
        try:
            score = float(raw_score)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                f"Supervised candidate {candidate_id!r} has no valid visual_score"
            ) from exc
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise ValueError(
                f"Supervised candidate {candidate_id!r} visual_score must be finite and between 0 and 1"
            )
        room = str(row.get("room") or "").strip()
        if not room:
            raise ValueError(f"Supervised candidate {candidate_id!r} has no room")
        examples.append({
            "candidate_id": candidate_id,
            "room": room,
            "source": str(row.get("source") or "unknown"),
            "label": label,
            "positive": label == POSITIVE_LABEL,
            "score": score,
        })
    return examples, dict(sorted(label_counts.items()))


def _metrics(examples: Sequence[Mapping[str, object]], threshold: float) -> dict[str, object]:
    tp = fp = tn = fn = 0
    for row in examples:
        actual = bool(row["positive"])
        predicted = float(row["score"]) >= threshold
        if actual and predicted:
            tp += 1
        elif not actual and predicted:
            fp += 1
        elif not actual and not predicted:
            tn += 1
        else:
            fn += 1

    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    specificity = tn / (tn + fp) if tn + fp else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else (0.0 if tp == 0 and tp + fn else None)
    )
    balanced_accuracy = (
        (recall + specificity) / 2
        if recall is not None and specificity is not None
        else None
    )
    return {
        "example_count": len(examples),
        "true_positives": tp,
        "false_positives": fp,
        "true_negatives": tn,
        "false_negatives": fn,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "specificity": round(specificity, 4) if specificity is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
        "balanced_accuracy": round(balanced_accuracy, 4) if balanced_accuracy is not None else None,
    }


def _thresholds(examples: Sequence[Mapping[str, object]]) -> list[float]:
    scores = sorted({float(row["score"]) for row in examples})
    if not scores:
        return []
    # Include an all-negative operating point in addition to observed scores.
    return [*scores, min(1.000001, scores[-1] + 0.000001)]


def _select_threshold(examples: Sequence[Mapping[str, object]]) -> float | None:
    positives = sum(bool(row["positive"]) for row in examples)
    negatives = len(examples) - positives
    if not positives or not negatives:
        return None
    candidates = []
    for threshold in _thresholds(examples):
        metrics = _metrics(examples, threshold)
        candidates.append((
            float(metrics["balanced_accuracy"]),
            float(metrics["f1"] or 0.0),
            threshold,
        ))
    # Conservative tie-break: prefer the higher cutoff after balanced accuracy
    # and F1, rather than silently favoring more positive predictions.
    return max(candidates)[2]


def calibrate_tested_exit_dataset(
    rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Report supervised score metrics and leave-one-room-out validation.

    Threshold selection is performed only on the training rooms in each
    validation fold. Unknown and unreachable rows never become negatives.
    """
    examples, label_counts = _validated_examples(rows)
    positives = sum(bool(row["positive"]) for row in examples)
    negatives = len(examples) - positives
    base: dict[str, object] = {
        "calibration_version": CALIBRATION_VERSION,
        "purpose": "developer_only_adjudicated_exit_score_calibration",
        "model_trained": False,
        "gameplay_thresholds_modified": False,
        "candidate_count": len(rows),
        "label_counts": label_counts,
        "supervised_example_count": len(examples),
        "positive_example_count": positives,
        "negative_example_count": negatives,
        "excluded_unlabeled_count": len(rows) - len(examples),
        "threshold_selection": "maximize balanced accuracy, then F1, then prefer higher threshold",
    }
    if not positives or not negatives:
        return {
            **base,
            "calibration_status": "insufficient_supervised_data",
            "reason": "At least one confirmed_exit and one tested_non_exit label are required.",
            "selected_threshold": None,
            "overall_metrics": None,
            "threshold_sweep": [],
            "source_breakdown": [],
            "leave_one_room_out": {
                "status": "not_run",
                "reason": "Both supervised classes are required for threshold selection.",
                "folds": [],
                "aggregate_metrics": None,
            },
        }

    selected_threshold = _select_threshold(examples)
    assert selected_threshold is not None
    sweep = [
        {
            "threshold": threshold,
            **_metrics(examples, threshold),
        }
        for threshold in _thresholds(examples)
    ]

    sources = sorted({str(row["source"]) for row in examples})
    source_breakdown = []
    for source in sources:
        subset = [row for row in examples if str(row["source"]) == source]
        source_breakdown.append({
            "source": source,
            **_metrics(subset, selected_threshold),
        })

    rooms = sorted({str(row["room"]) for row in examples})
    folds: list[dict[str, object]] = []
    held_out_examples: list[Mapping[str, object]] = []
    skipped_folds: list[dict[str, str]] = []
    if len(rooms) >= 2:
        for held_out_room in rooms:
            training = [row for row in examples if str(row["room"]) != held_out_room]
            testing = [row for row in examples if str(row["room"]) == held_out_room]
            threshold = _select_threshold(training)
            if threshold is None:
                skipped_folds.append({
                    "held_out_room": held_out_room,
                    "reason": "Training rooms do not contain both supervised classes.",
                })
                continue
            metrics = _metrics(testing, threshold)
            folds.append({
                "held_out_room": held_out_room,
                "training_example_count": len(training),
                "testing_example_count": len(testing),
                "selected_threshold": threshold,
                "metrics": metrics,
            })
            held_out_examples.extend(testing)
        cv_status = "complete" if folds and not skipped_folds else (
            "partial" if folds else "insufficient_training_classes"
        )
        # Aggregate confusion counts from each fold's own threshold. Do not
        # recompute predictions with one shared threshold across held-out rooms.
        if folds:
            totals = {
                key: sum(int(fold["metrics"][key]) for fold in folds)
                for key in ("true_positives", "false_positives", "true_negatives", "false_negatives")
            }
            tp, fp = totals["true_positives"], totals["false_positives"]
            tn, fn = totals["true_negatives"], totals["false_negatives"]
            precision = tp / (tp + fp) if tp + fp else None
            recall = tp / (tp + fn) if tp + fn else None
            specificity = tn / (tn + fp) if tn + fp else None
            f1 = (
                2 * precision * recall / (precision + recall)
                if precision is not None and recall is not None and precision + recall
                else (0.0 if tp == 0 and tp + fn else None)
            )
            balanced = (
                (recall + specificity) / 2
                if recall is not None and specificity is not None
                else None
            )
            aggregate = {
                "example_count": sum(int(fold["metrics"]["example_count"]) for fold in folds),
                **totals,
                "precision": round(precision, 4) if precision is not None else None,
                "recall": round(recall, 4) if recall is not None else None,
                "specificity": round(specificity, 4) if specificity is not None else None,
                "f1": round(f1, 4) if f1 is not None else None,
                "balanced_accuracy": round(balanced, 4) if balanced is not None else None,
            }
        else:
            aggregate = None
        cross_room = {
            "status": cv_status,
            "method": "leave_one_room_out_threshold_selection",
            "room_count": len(rooms),
            "fold_count": len(folds),
            "skipped_folds": skipped_folds,
            "folds": folds,
            "aggregate_metrics": aggregate,
        }
    else:
        cross_room = {
            "status": "not_run",
            "reason": "At least two rooms with adjudicated examples are required.",
            "room_count": len(rooms),
            "folds": [],
            "aggregate_metrics": None,
        }

    return {
        **base,
        "calibration_status": "complete",
        "selected_threshold": selected_threshold,
        "overall_metrics": _metrics(examples, selected_threshold),
        "threshold_sweep": sweep,
        "source_breakdown": source_breakdown,
        "leave_one_room_out": cross_room,
        "limitations": [
            "This calibrates the existing heuristic score; it does not train a classifier.",
            "Only explicit confirmed_exit and tested_non_exit labels are scored.",
            "Unknown and unreachable candidates are excluded, never treated as negatives.",
            "Leave-one-room-out validation is reported only when threshold selection is possible without the held-out room.",
            "The selected threshold is diagnostic and is not applied to gameplay.",
        ],
    }


def load_tested_exit_dataset(path: Path) -> list[dict[str, object]]:
    """Load the generated JSONL dataset without consulting live game state."""
    rows: list[dict[str, object]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid dataset JSON on line {line_number}: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"Dataset line {line_number} must be a JSON object")
        rows.append(row)
    return rows


def write_tested_exit_calibration(path: Path, report: Mapping[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"calibration_version": CALIBRATION_VERSION, **dict(report)}
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path
