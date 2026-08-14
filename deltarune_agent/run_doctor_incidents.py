"""Run Doctor v0.3: correlate findings into incidents and health scores."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import argparse
import hashlib
import json
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Sequence

from . import run_doctor as foundation
from . import run_doctor_reasoning as previous


RUN_DOCTOR_VERSION = "0.3.0"
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_SEVERITY_WEIGHT = {"critical": 35, "high": 20, "medium": 10, "low": 5, "info": 1}
_SUBSYSTEM_CATEGORY = {
    "capture/perception": "perception_capture",
    "navigation/planning": "navigation",
    "policy/navigation": "loop_resistance",
    "navigation/portal handling": "navigation",
    "planning/evidence utilization": "planning_reasoning",
    "interaction/planning": "interaction",
    "planning/objectives": "planning_reasoning",
    "planning/evidence filtering": "planning_reasoning",
    "telemetry": "telemetry_timing",
    "timing/telemetry": "telemetry_timing",
}
_HEALTH_CATEGORIES = (
    "perception_capture",
    "navigation",
    "interaction",
    "planning_reasoning",
    "telemetry_timing",
    "loop_resistance",
)


@dataclass(frozen=True)
class DoctorIncident:
    incident_id: str
    severity: str
    confidence: float
    title: str
    finding_ids: tuple[str, ...]
    start_step: int | None
    end_step: int | None
    room: str | None
    likely_primary_subsystem: str
    causal_note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["finding_ids"] = list(self.finding_ids)
        return payload


@dataclass(frozen=True)
class IncidentDoctorReport:
    base: foundation.RunDoctorReport
    incidents: tuple[DoctorIncident, ...]
    health: dict[str, Any]

    @property
    def doctor_version(self) -> str:
        return RUN_DOCTOR_VERSION

    def as_dict(self) -> dict[str, Any]:
        payload = self.base.as_dict()
        payload["doctor_version"] = RUN_DOCTOR_VERSION
        payload["incidents"] = [incident.as_dict() for incident in self.incidents]
        payload["incident_count"] = len(self.incidents)
        payload["health"] = self.health
        return payload


def _overlap(
    first: foundation.RunDoctorFinding,
    second: foundation.RunDoctorFinding,
    *,
    gap_steps: int = 20,
) -> bool:
    if first.evidence.start_step is None or second.evidence.start_step is None:
        return False
    first_end = (
        first.evidence.end_step
        if first.evidence.end_step is not None
        else first.evidence.start_step
    )
    second_end = (
        second.evidence.end_step
        if second.evidence.end_step is not None
        else second.evidence.start_step
    )
    return (
        first.evidence.start_step <= second_end + gap_steps
        and second.evidence.start_step <= first_end + gap_steps
    )


def _compatible_context(
    first: foundation.RunDoctorFinding,
    second: foundation.RunDoctorFinding,
) -> bool:
    return first.room == second.room or first.room is None or second.room is None


def group_findings(
    findings: Iterable[foundation.RunDoctorFinding],
) -> tuple[DoctorIncident, ...]:
    rows = list(findings)
    groups: list[list[foundation.RunDoctorFinding]] = []
    unused = set(range(len(rows)))

    while unused:
        seed = min(unused)
        group = {seed}
        changed = True
        while changed:
            changed = False
            for index in list(unused - group):
                if any(
                    _overlap(rows[index], rows[member])
                    and _compatible_context(rows[index], rows[member])
                    for member in group
                ):
                    group.add(index)
                    changed = True
        unused -= group
        groups.append([rows[index] for index in sorted(group)])

    incidents: list[DoctorIncident] = []
    for group in groups:
        finding_ids = tuple(sorted(finding.finding_id for finding in group))
        digest = hashlib.sha256("|".join(finding_ids).encode("utf-8")).hexdigest()[:12]
        primary = min(
            group,
            key=lambda finding: (
                _SEVERITY_ORDER.get(finding.severity, 99),
                -finding.confidence,
                finding.finding_id,
            ),
        )
        starts = [
            finding.evidence.start_step
            for finding in group
            if finding.evidence.start_step is not None
        ]
        ends = [
            finding.evidence.end_step
            for finding in group
            if finding.evidence.end_step is not None
        ]
        rooms = {finding.room for finding in group if finding.room}
        finding_types = {finding.finding_type for finding in group}
        causal_note = None
        if (
            {"capture_validity_degradation", "blind_search_streak"}
            <= finding_types
            or {"invalid_visual_streak", "blind_search_streak"} <= finding_types
        ):
            causal_note = (
                "Capture degradation overlaps blind search and is a plausible "
                "contributor; this is correlation, not proof of causation."
            )
        elif (
            "unconsumed_observed_evidence" in finding_types
            and "blind_search_streak" in finding_types
        ):
            causal_note = (
                "Unresolved observed evidence overlaps blind-search behavior and may "
                "indicate an evidence-routing failure; snapshot timing prevents a "
                "stronger causal claim."
            )

        incidents.append(
            DoctorIncident(
                incident_id=f"incident:{digest}",
                severity=primary.severity,
                confidence=round(mean(finding.confidence for finding in group), 3),
                title=primary.title,
                finding_ids=finding_ids,
                start_step=min(starts) if starts else None,
                end_step=max(ends) if ends else None,
                room=next(iter(rooms)) if len(rooms) == 1 else None,
                likely_primary_subsystem=primary.subsystem,
                causal_note=causal_note,
            )
        )

    incidents.sort(
        key=lambda incident: (
            _SEVERITY_ORDER.get(incident.severity, 99),
            incident.start_step if incident.start_step is not None else 10**18,
            incident.incident_id,
        )
    )
    return tuple(incidents)


def health_scores(
    findings: Iterable[foundation.RunDoctorFinding],
) -> dict[str, Any]:
    scores = {category: 100 for category in _HEALTH_CATEGORIES}
    for finding in findings:
        category = _SUBSYSTEM_CATEGORY.get(finding.subsystem, "planning_reasoning")
        scores[category] = max(
            0,
            scores[category] - _SEVERITY_WEIGHT.get(finding.severity, 5),
        )
    overall = round(mean(scores.values()), 1) if scores else 100.0
    grade = (
        "A"
        if overall >= 90
        else "B"
        if overall >= 80
        else "C"
        if overall >= 70
        else "D"
        if overall >= 60
        else "F"
    )
    return {
        "categories": dict(sorted(scores.items())),
        "overall": overall,
        "grade": grade,
    }


def analyze_run(
    run: foundation.NormalizedRun,
    thresholds: foundation.DoctorThresholds | None = None,
) -> IncidentDoctorReport:
    base = previous.analyze_run(run, thresholds)
    return IncidentDoctorReport(
        base=base,
        incidents=group_findings(base.findings),
        health=health_scores(base.findings),
    )


def render_markdown(report: IncidentDoctorReport) -> str:
    lines = [
        "# Automatic Run Doctor",
        "",
        f"- Doctor version: `{RUN_DOCTOR_VERSION}`",
        f"- Agent revision: `{report.base.agent_revision or 'unknown'}`",
        f"- Events analyzed: **{report.base.event_count}**",
        f"- Findings: **{report.base.finding_count}**",
        f"- Incidents: **{len(report.incidents)}**",
        f"- Health: **{report.health['overall']}/100 ({report.health['grade']})**",
        "",
        "## Health categories",
        "",
    ]
    for category, score in report.health["categories"].items():
        lines.append(f"- {category.replace('_', ' ').title()}: **{score}/100**")
    lines.extend(["", "## Incidents", ""])
    if not report.incidents:
        lines.extend(["No detector incidents.", ""])
    for incident in report.incidents:
        step_range = (
            f"{incident.start_step}–{incident.end_step}"
            if incident.start_step is not None and incident.end_step is not None
            else "global/run-level"
        )
        lines.extend(
            [
                f"### [{incident.severity.upper()}] {incident.title}",
                "",
                f"- Confidence: **{incident.confidence:.0%}**",
                f"- Evidence steps: **{step_range}**",
                f"- Primary subsystem: **{incident.likely_primary_subsystem}**",
                f"- Findings grouped: **{len(incident.finding_ids)}**",
            ]
        )
        if incident.causal_note:
            lines.append(f"- Causal note: {incident.causal_note}")
        lines.append("")
    lines.extend(["## Individual findings", ""])
    for finding in report.base.findings:
        step_range = (
            f"{finding.evidence.start_step}–{finding.evidence.end_step}"
            if finding.evidence.start_step is not None
            and finding.evidence.end_step is not None
            else "global/run-level"
        )
        lines.extend(
            [
                f"### [{finding.severity.upper()}] {finding.title}",
                "",
                f"- Type: `{finding.finding_type}`",
                f"- Confidence: **{finding.confidence:.0%}**",
                f"- Subsystem: **{finding.subsystem}**",
                f"- Evidence steps: **{step_range}**",
                "",
                finding.explanation,
                "",
                f"**Engineering action:** {finding.recommendation}",
                "",
            ]
        )
    return "\n".join(lines)


def write_report(
    report: IncidentDoctorReport,
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
        json.dumps(report.as_dict(), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m deltarune_agent run-doctor",
        description="Analyze and correlate recorded run problems without mutating AI state.",
    )
    parser.add_argument("run", type=Path)
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser


def cli(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    run = foundation.load_run(args.run)
    report = analyze_run(run)
    if not args.no_save:
        write_report(report, args.output)
    if args.json:
        print(json.dumps(report.as_dict(), indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0


__all__ = [
    "DoctorIncident",
    "IncidentDoctorReport",
    "RUN_DOCTOR_VERSION",
    "analyze_run",
    "cli",
    "group_findings",
    "health_scores",
    "render_markdown",
    "write_report",
]
