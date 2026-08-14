"""Trusted Automatic Run Doctor v1.0 report/CLI surface."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import run_doctor as foundation
from . import run_doctor_compare as comparison_engine
from . import run_doctor_incidents as incident_engine
from . import run_doctor_reasoning as reasoning_engine
from . import run_doctor_calibration as calibration_engine


RUN_DOCTOR_VERSION = "1.0.1"


def _historical_summary_value(run: foundation.NormalizedRun, key: str) -> int | None:
    """Read counters from modern summaries or older policy reports."""
    policy_summary = run.run_report.get("policy_summary")
    sources = (
        run.summary,
        policy_summary if isinstance(policy_summary, Mapping) else {},
    )
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        value = source.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            continue
    return None


# v0.2 accidentally returned None as soon as summary.json lacked a counter,
# preventing its intended fallback to historical run_report.policy_summary.
# Install the corrected semantics at the trusted release boundary so historical
# detector modules remain stable while v1 can analyze both artifact layouts.
reasoning_engine._summary_value = _historical_summary_value


# Preserve the pre-calibration analyzer so comparison and direct analysis can use
# the same trusted calibration layer without changing the older v0.x modules.
_RAW_INCIDENT_ANALYZE = incident_engine.analyze_run


def _trusted_incident_analyze(
    run: foundation.NormalizedRun,
    thresholds: foundation.DoctorThresholds | None = None,
) -> incident_engine.IncidentDoctorReport:
    raw = _RAW_INCIDENT_ANALYZE(run, thresholds)
    return calibration_engine.calibrate_incident_report(run, raw)


# comparison_engine resolves incident_engine.analyze_run dynamically from the
# shared module object, so patching the trusted release boundary keeps candidate
# and baseline analysis consistent while leaving legacy detector modules intact.
incident_engine.analyze_run = _trusted_incident_analyze


def analyze_directory(
    candidate_path: Path,
    *,
    baseline_path: Path | None = None,
) -> tuple[
    incident_engine.IncidentDoctorReport,
    comparison_engine.RegressionComparison | None,
]:
    candidate = foundation.load_run(candidate_path)
    if baseline_path is not None:
        baseline = foundation.load_run(baseline_path)
        return comparison_engine.compare_runs(baseline, candidate)
    return incident_engine.analyze_run(candidate), None


def report_payload(
    report: incident_engine.IncidentDoctorReport,
    comparison: comparison_engine.RegressionComparison | None = None,
) -> dict[str, Any]:
    payload = report.as_dict()
    payload["doctor_version"] = RUN_DOCTOR_VERSION
    payload["trusted_release"] = True
    payload["read_only"] = True
    payload["mutates_learning"] = False
    if comparison is not None:
        payload["comparison"] = comparison.as_dict()
    return payload


def render_markdown(
    report: incident_engine.IncidentDoctorReport,
    comparison: comparison_engine.RegressionComparison | None = None,
) -> str:
    text = comparison_engine.render_markdown(report, comparison)
    text = text.replace(
        "Doctor version: `0.4.0`",
        f"Doctor version: `{RUN_DOCTOR_VERSION}`",
        1,
    )
    marker = "# Automatic Run Doctor\n"
    if text.startswith(marker):
        text = text.replace(
            marker,
            marker
            + "\n> Trusted v1.0 report: read-only diagnostics; learned memory, rewards, and policy are not modified.\n",
            1,
        )
    return text


def write_report(
    report: incident_engine.IncidentDoctorReport,
    comparison: comparison_engine.RegressionComparison | None = None,
    output_directory: Path | None = None,
) -> tuple[Path, Path]:
    destination = (
        Path(output_directory)
        if output_directory is not None
        else Path(report.base.run_directory)
    )
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "run_doctor.json"
    markdown_path = destination / "run_doctor.md"
    json_path.write_text(
        json.dumps(
            report_payload(report, comparison),
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(report, comparison), encoding="utf-8")
    return json_path, markdown_path


def analyze_and_write(
    candidate_path: Path,
    *,
    baseline_path: Path | None = None,
    output_directory: Path | None = None,
) -> tuple[dict[str, Any], tuple[Path, Path]]:
    report, comparison = analyze_directory(
        candidate_path,
        baseline_path=baseline_path,
    )
    paths = write_report(report, comparison, output_directory)
    return report_payload(report, comparison), paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m deltarune_agent run-doctor",
        description=(
            "Trusted read-only post-run diagnosis with optional historical comparison."
        ),
    )
    parser.add_argument("run", type=Path)
    parser.add_argument("--compare", type=Path, metavar="BASELINE_RUN")
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser


def cli(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    report, comparison = analyze_directory(args.run, baseline_path=args.compare)
    payload = report_payload(report, comparison)
    if not args.no_save:
        write_report(report, comparison, args.output)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(render_markdown(report, comparison))
    return 0


__all__ = [
    "RUN_DOCTOR_VERSION",
    "analyze_and_write",
    "analyze_directory",
    "cli",
    "render_markdown",
    "report_payload",
    "write_report",
]
