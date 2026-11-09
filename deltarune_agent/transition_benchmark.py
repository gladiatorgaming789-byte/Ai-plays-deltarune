"""Developer-only transition benchmark CLI.

This tool is deliberately a post-run utility. It reads recorded run artifacts,
builds developer-side transition truth, and optionally scores an independently
recorded detector prediction file. It is never imported by the live agent.
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
        sample = row.get("telemetry")
        if isinstance(sample, dict):
            telemetry.append(sample)
    return telemetry


def _load_predictions(path: Path) -> list[TransitionPrediction]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("Prediction file must contain a JSON list")
    result: list[TransitionPrediction] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        result.append(
            TransitionPrediction(
                sequence=int(row["sequence"]),
                source_room=(
                    str(row["source_room"])
                    if row.get("source_room") is not None
                    else None
                ),
                destination_room=(
                    str(row["destination_room"])
                    if row.get("destination_room") is not None
                    else None
                ),
                predicted_at=(
                    float(row["predicted_at"])
                    if row.get("predicted_at") is not None
                    else None
                ),
                confidence=float(row.get("confidence", 0.0)),
            )
        )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build/evaluate developer-only Deltarune room-transition benchmarks"
    )
    parser.add_argument("run_directory", type=Path)
    parser.add_argument(
        "--predictions",
        type=Path,
        help="JSON list of independent detector predictions",
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

    output = args.output or args.run_directory / "transition_benchmark"
    output.mkdir(parents=True, exist_ok=True)
    write_oracle(output / "oracle.json", truth)

    summary: dict[str, object] = {
        "oracle_version": 1,
        "run_directory": str(args.run_directory),
        "telemetry_samples": len(telemetry),
        "oracle_transitions": len(truth),
        "prediction_source": None,
        "evaluation": None,
    }

    if args.predictions is not None:
        predictions = _load_predictions(args.predictions)
        evaluation = evaluate_transitions(truth, predictions)
        write_evaluation(output / "evaluation.json", evaluation)
        summary["prediction_source"] = str(args.predictions)
        summary["evaluation"] = evaluation.as_dict()

    write_json(output / "summary.json", json_safe(summary))
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
