"""Developer-only room-transition and visual-exit benchmark CLI.

This post-run utility reads recorded observations and scores independently
recorded policy predictions. It is never imported by the live agent.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .run_artifacts import json_safe, write_json
from .transition_oracle import (
    TransitionPrediction,
    build_oracle_from_telemetry,
    evaluate_transitions,
    write_evaluation,
    write_oracle,
)
from .visual_exit_benchmark import (
    collect_visual_exit_candidates,
    calibrate_visual_exit_candidates,
    evaluate_visual_exits,
    write_visual_exit_calibration,
    write_visual_exit_evaluation,
)


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _telemetry_from_event_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    telemetry: list[dict[str, object]] = []
    for row in rows:
        candidates = row.get("telemetry_trace")
        samples = candidates if isinstance(candidates, list) else []
        current = row.get("telemetry")
        if isinstance(current, dict):
            samples = [*samples, current]
        for sample in samples:
            if isinstance(sample, dict):
                telemetry.append({
                    **sample,
                    "_run_step": row.get("step"),
                    "_run_elapsed_seconds": sample.get(
                        "_run_elapsed_seconds", row.get("elapsed_seconds")
                    ),
                })
    return telemetry


def _load_recorded_predictions(path: Path) -> list[TransitionPrediction]:
    predictions: list[TransitionPrediction] = []
    for row in _load_jsonl(path):
        elapsed = row.get("elapsed_seconds")
        run_step = row.get("step")
        nested = row.get("transition_predictions")
        if not isinstance(nested, list):
            continue
        for item in nested:
            if not isinstance(item, dict):
                continue
            raw_sequence = item.get("sequence")
            sequence = int(raw_sequence if raw_sequence is not None else run_step or 0)
            raw_step = item.get("step", run_step)
            predictions.append(TransitionPrediction(
                sequence=sequence,
                source_room=str(item["source_room"]) if item.get("source_room") is not None else None,
                destination_room=str(item["destination_room"]) if item.get("destination_room") is not None else None,
                predicted_at=float(elapsed) if elapsed is not None else None,
                confidence=float(item.get("confidence", 0.0)),
                step=int(raw_step) if raw_step is not None else None,
            ))
    return predictions


def _load_predictions(path: Path) -> list[TransitionPrediction]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("Prediction file must contain a JSON list")
    result: list[TransitionPrediction] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        result.append(TransitionPrediction(
            sequence=int(row["sequence"]),
            source_room=str(row["source_room"]) if row.get("source_room") is not None else None,
            destination_room=str(row["destination_room"]) if row.get("destination_room") is not None else None,
            predicted_at=float(row["predicted_at"]) if row.get("predicted_at") is not None else None,
            confidence=float(row.get("confidence", 0.0)),
            step=int(row["step"]) if row.get("step") is not None else None,
        ))
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build/evaluate developer-only Deltarune transition benchmarks"
    )
    parser.add_argument("run_directory", type=Path)
    parser.add_argument(
        "--predictions",
        type=Path,
        help="optional JSON-list prediction file; defaults to this run's predictions.jsonl",
    )
    parser.add_argument(
        "--exit-match-radius",
        type=float,
        default=16.0,
        help="maximum world-space distance between candidate anchor and transition source",
    )
    parser.add_argument(
        "--exit-max-lead-steps",
        type=int,
        default=180,
        help="maximum steps between first candidate observation and transition",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="benchmark directory; defaults to <run>/transition_benchmark",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    events_path = args.run_directory / "events.jsonl"
    if not events_path.is_file():
        raise SystemExit(f"Run events not found: {events_path}")

    events = _load_jsonl(events_path)
    telemetry = _telemetry_from_event_rows(events)
    truth = build_oracle_from_telemetry(telemetry)
    exit_candidates = collect_visual_exit_candidates(events)
    exit_evaluation = evaluate_visual_exits(
        exit_candidates,
        truth,
        spatial_match_radius=args.exit_match_radius,
        max_lead_steps=args.exit_max_lead_steps,
    )

    output = args.output or args.run_directory / "transition_benchmark"
    output.mkdir(parents=True, exist_ok=True)
    write_oracle(output / "oracle.json", truth)
    write_visual_exit_evaluation(
        output / "visual_exit_evaluation.json", exit_evaluation
    )
    exit_calibration = calibrate_visual_exit_candidates(
        exit_candidates,
        truth,
        spatial_match_radius=args.exit_match_radius,
        max_lead_steps=args.exit_max_lead_steps,
    )
    write_visual_exit_calibration(
        output / "visual_exit_calibration.json", exit_calibration
    )

    summary: dict[str, object] = {
        "oracle_version": 1,
        "run_directory": str(args.run_directory),
        "telemetry_samples": len(telemetry),
        "oracle_transitions": len(truth),
        "prediction_source": None,
        "evaluation": None,
        "visual_exit_candidates": len(exit_candidates),
        "visual_exit_evaluation": exit_evaluation.as_dict(),
        "visual_exit_calibration": exit_calibration,
    }

    prediction_path = args.predictions or args.run_directory / "predictions.jsonl"
    if prediction_path.is_file():
        predictions = (
            _load_predictions(prediction_path)
            if args.predictions is not None
            else _load_recorded_predictions(prediction_path)
        )
        evaluation = evaluate_transitions(truth, predictions)
        write_evaluation(output / "evaluation.json", evaluation)
        summary["prediction_source"] = str(prediction_path)
        summary["prediction_count"] = len(predictions)
        summary["evaluation"] = evaluation.as_dict()
    else:
        summary["prediction_warning"] = f"No prediction file found: {prediction_path}"

    write_json(output / "summary.json", json_safe(summary))
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
