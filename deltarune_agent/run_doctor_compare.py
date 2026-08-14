"""Run Doctor v0.4: evidence-aware historical run comparison."""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import evaluation
from . import run_doctor as foundation
from . import run_doctor_incidents as previous


RUN_DOCTOR_VERSION = "0.4.0"
_BETTER_HIGH = {
    "story_progress_events",
    "telemetry_coverage",
    "visual_coverage",
    "exploration_efficiency",
    "rooms_seen",
    "unique_cells",
}
_BETTER_LOW = {
    "room_bounces",
    "repeated_actions",
    "unknown_steps",
    "low_confidence_steps",
    "invalid_visual_steps",
}


@dataclass(frozen=True)
class MetricComparison:
    metric: str
    baseline: float | int
    candidate: float | int
    delta: float
    classification: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "baseline": self.baseline,
            "candidate": self.candidate,
            "delta": self.delta,
            "classification": self.classification,
        }


@dataclass(frozen=True)
class RegressionComparison:
    baseline_run: str
    candidate_run: str
    comparability: str
    comparability_reasons: tuple[str, ...]
    caveats: tuple[str, ...]
    verdict: str
    health_delta: float
    finding_count_delta: int
    critical_high_delta: int
    metrics: tuple[MetricComparison, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "baseline_run": self.baseline_run,
            "candidate_run": self.candidate_run,
            "comparability": self.comparability,
            "comparability_reasons": list(self.comparability_reasons),
            "caveats": list(self.caveats),
            "verdict": self.verdict,
            "health_delta": self.health_delta,
            "finding_count_delta": self.finding_count_delta,
            "critical_high_delta": self.critical_high_delta,
            "metrics": [metric.as_dict() for metric in self.metrics],
        }


def _start_room(run: foundation.NormalizedRun) -> str | None:
    for event in run.events:
        room = foundation._room(event)
        if room:
            return room
    return None


def _config(run: foundation.NormalizedRun) -> Mapping[str, Any]:
    value = run.manifest.get("config")
    return value if isinstance(value, Mapping) else {}


def comparability(
    baseline: foundation.NormalizedRun,
    candidate: foundation.NormalizedRun,
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    score = 0
    reasons: list[str] = []
    caveats: list[str] = []
    baseline_room = _start_room(baseline)
    candidate_room = _start_room(candidate)
    if baseline_room and baseline_room == candidate_room:
        score += 2
        reasons.append("same observed starting room")
    elif baseline_room and candidate_room:
        caveats.append("different observed starting rooms")
    else:
        caveats.append("starting room unavailable for one or both runs")

    baseline_config = _config(baseline)
    candidate_config = _config(candidate)
    for key in ("speed", "profile", "live"):
        if key not in baseline_config or key not in candidate_config:
            continue
        if baseline_config[key] == candidate_config[key]:
            score += 1
            reasons.append(f"same {key} configuration")
        else:
            caveats.append(f"different {key} configuration")

    if baseline.agent_revision != candidate.agent_revision:
        reasons.append("different agent revisions (expected for regression testing)")
    level = "strong" if score >= 4 else "moderate" if score >= 2 else "weak"
    if level == "weak":
        caveats.append("aggregate improvement/regression verdict should be treated cautiously")
    return level, tuple(reasons), tuple(caveats)


def _metrics(run: foundation.NormalizedRun) -> dict[str, float | int]:
    value = run.run_report.get("metrics")
    if isinstance(value, Mapping):
        result = {
            str(key): item
            for key, item in value.items()
            if isinstance(item, (int, float)) and not isinstance(item, bool)
        }
        if result:
            return result
    # Fallback for partial/historical runs. This is deliberately local and
    # observable; no game-specific facts are consulted.
    return evaluation.calculate_metrics(run.events).as_dict()


def classify_metric(
    name: str,
    baseline: float | int,
    candidate: float | int,
) -> str:
    if baseline == candidate:
        return "neutral"
    if name in _BETTER_HIGH:
        return "improved" if candidate > baseline else "regressed"
    if name in _BETTER_LOW:
        return "improved" if candidate < baseline else "regressed"
    return "neutral"


def compare_runs(
    baseline_run: foundation.NormalizedRun,
    candidate_run: foundation.NormalizedRun,
) -> tuple[previous.IncidentDoctorReport, RegressionComparison]:
    baseline_report = previous.analyze_run(baseline_run)
    candidate_report = previous.analyze_run(candidate_run)
    level, reasons, caveats = comparability(baseline_run, candidate_run)

    baseline_metrics = _metrics(baseline_run)
    candidate_metrics = _metrics(candidate_run)
    metric_rows: list[MetricComparison] = []
    for name in sorted(set(baseline_metrics) & set(candidate_metrics)):
        baseline_value = baseline_metrics[name]
        candidate_value = candidate_metrics[name]
        metric_rows.append(
            MetricComparison(
                metric=name,
                baseline=baseline_value,
                candidate=candidate_value,
                delta=round(float(candidate_value) - float(baseline_value), 6),
                classification=classify_metric(name, baseline_value, candidate_value),
            )
        )

    health_delta = round(
        float(candidate_report.health["overall"])
        - float(baseline_report.health["overall"]),
        1,
    )
    finding_delta = candidate_report.base.finding_count - baseline_report.base.finding_count
    baseline_major = sum(
        baseline_report.base.severity_counts.get(level_name, 0)
        for level_name in ("critical", "high")
    )
    candidate_major = sum(
        candidate_report.base.severity_counts.get(level_name, 0)
        for level_name in ("critical", "high")
    )
    major_delta = candidate_major - baseline_major

    directional = [row.classification for row in metric_rows]
    improved_metrics = directional.count("improved")
    regressed_metrics = directional.count("regressed")
    if level == "weak":
        verdict = "inconclusive"
    elif major_delta > 0 or health_delta <= -5:
        verdict = "regressed"
    elif major_delta < 0 and health_delta >= 0:
        verdict = "improved"
    elif health_delta >= 5 and improved_metrics >= regressed_metrics:
        verdict = "improved"
    elif improved_metrics and regressed_metrics:
        verdict = "mixed"
    else:
        verdict = "no_clear_change"

    comparison = RegressionComparison(
        baseline_run=str(baseline_run.directory),
        candidate_run=str(candidate_run.directory),
        comparability=level,
        comparability_reasons=reasons,
        caveats=caveats,
        verdict=verdict,
        health_delta=health_delta,
        finding_count_delta=finding_delta,
        critical_high_delta=major_delta,
        metrics=tuple(metric_rows),
    )
    return candidate_report, comparison


def render_markdown(
    report: previous.IncidentDoctorReport,
    comparison: RegressionComparison | None = None,
) -> str:
    base_text = previous.render_markdown(report).replace(
        "Doctor version: `0.3.0`",
        f"Doctor version: `{RUN_DOCTOR_VERSION}`",
        1,
    )
    if comparison is None:
        return base_text
    lines = [
        base_text.rstrip(),
        "",
        "## Regression comparison",
        "",
        f"- Baseline: `{comparison.baseline_run}`",
        f"- Comparability: **{comparison.comparability}**",
        f"- Verdict: **{comparison.verdict}**",
        f"- Health delta: **{comparison.health_delta:+.1f}**",
        f"- Critical/high finding delta: **{comparison.critical_high_delta:+d}**",
        "",
    ]
    if comparison.comparability_reasons:
        lines.append("Comparable signals: " + "; ".join(comparison.comparability_reasons))
        lines.append("")
    if comparison.caveats:
        lines.append("Caveats: " + "; ".join(comparison.caveats))
        lines.append("")
    directional = [
        metric
        for metric in comparison.metrics
        if metric.classification != "neutral"
    ]
    if directional:
        lines.extend(["### Directional metrics", ""])
        for metric in directional:
            lines.append(
                f"- `{metric.metric}`: {metric.baseline} → {metric.candidate} "
                f"(**{metric.classification}**)"
            )
        lines.append("")
    return "\n".join(lines)


def write_report(
    report: previous.IncidentDoctorReport,
    comparison: RegressionComparison | None,
    output_directory: Path | None = None,
) -> tuple[Path, Path]:
    destination = (
        Path(output_directory)
        if output_directory is not None
        else Path(report.base.run_directory)
    )
    destination.mkdir(parents=True, exist_ok=True)
    payload = report.as_dict()
    payload["doctor_version"] = RUN_DOCTOR_VERSION
    if comparison is not None:
        payload["comparison"] = comparison.as_dict()
    json_path = destination / "run_doctor.json"
    markdown_path = destination / "run_doctor.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(report, comparison), encoding="utf-8")
    return json_path, markdown_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m deltarune_agent run-doctor",
        description="Analyze a recorded run and optionally compare it with a baseline.",
    )
    parser.add_argument("run", type=Path)
    parser.add_argument("--compare", type=Path, metavar="BASELINE_RUN")
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser


def cli(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    candidate = foundation.load_run(args.run)
    comparison = None
    if args.compare is not None:
        baseline = foundation.load_run(args.compare)
        report, comparison = compare_runs(baseline, candidate)
    else:
        report = previous.analyze_run(candidate)
    if not args.no_save:
        write_report(report, comparison, args.output)
    payload = report.as_dict()
    payload["doctor_version"] = RUN_DOCTOR_VERSION
    if comparison is not None:
        payload["comparison"] = comparison.as_dict()
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(render_markdown(report, comparison))
    return 0


__all__ = [
    "MetricComparison",
    "RegressionComparison",
    "RUN_DOCTOR_VERSION",
    "classify_metric",
    "comparability",
    "compare_runs",
    "cli",
    "render_markdown",
    "write_report",
]
