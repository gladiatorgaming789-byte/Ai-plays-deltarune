"""Automatic Run Doctor: deterministic, read-only post-run diagnostics.

Run Doctor consumes only artifacts already recorded by the agent. It never
imports the live controller, changes learned memory, or injects game/progression
knowledge. Detector findings describe observed behavior and evidence only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence


RUN_DOCTOR_VERSION = "0.1.0"
RUN_DOCTOR_SCHEMA_VERSION = 1
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


@dataclass(frozen=True)
class DoctorThresholds:
    """Conservative defaults for v0.1 detectors."""

    room_stall_steps: int = 300
    room_stall_seconds: float = 30.0
    repeated_action_streak: int = 12
    rapid_return_steps: int = 12
    invalid_visual_streak: int = 30
    low_visual_valid_ratio: float = 0.60
    low_visual_min_events: int = 50


@dataclass(frozen=True)
class EvidenceRange:
    start_step: int | None
    end_step: int | None
    start_seconds: float | None = None
    end_seconds: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RunDoctorFinding:
    finding_id: str
    finding_type: str
    title: str
    severity: str
    confidence: float
    subsystem: str
    explanation: str
    recommendation: str
    evidence: EvidenceRange
    room: str | None = None
    measured: Mapping[str, Any] = field(default_factory=dict)
    threshold: Mapping[str, Any] = field(default_factory=dict)
    uncertainties: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = self.evidence.as_dict()
        payload["measured"] = dict(self.measured)
        payload["threshold"] = dict(self.threshold)
        payload["uncertainties"] = list(self.uncertainties)
        return payload


@dataclass
class NormalizedRun:
    directory: Path
    manifest: dict[str, Any]
    summary: dict[str, Any]
    run_report: dict[str, Any]
    telemetry_diagnostics: dict[str, Any]
    speed_diagnostics: dict[str, Any]
    events: list[dict[str, Any]]
    predictions: list[dict[str, Any]]
    navigation_updates: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)

    @property
    def agent_revision(self) -> str | None:
        for source in (self.manifest, self.run_report, self.summary):
            value = source.get("agent_revision") if isinstance(source, Mapping) else None
            if value:
                return str(value)
        return None


@dataclass(frozen=True)
class RunDoctorReport:
    run_directory: str
    doctor_version: str
    schema_version: int
    agent_revision: str | None
    event_count: int
    finding_count: int
    severity_counts: Mapping[str, int]
    findings: tuple[RunDoctorFinding, ...]
    loader_warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_directory": self.run_directory,
            "doctor_version": self.doctor_version,
            "schema_version": self.schema_version,
            "agent_revision": self.agent_revision,
            "event_count": self.event_count,
            "finding_count": self.finding_count,
            "severity_counts": dict(self.severity_counts),
            "findings": [finding.as_dict() for finding in self.findings],
            "loader_warnings": list(self.loader_warnings),
        }


def _load_object(path: Path, warnings: list[str]) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        warnings.append(f"Could not read {path.name}: {exc}")
        return {}
    if not isinstance(value, dict):
        warnings.append(f"Ignored non-object JSON in {path.name}")
        return {}
    return value


def _load_jsonl(path: Path, warnings: list[str]) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        warnings.append(f"Could not read {path.name}: {exc}")
        return rows
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            warnings.append(f"Skipped malformed {path.name}:{line_number}: {exc.msg}")
            continue
        if isinstance(value, dict):
            rows.append(value)
        else:
            warnings.append(f"Skipped non-object {path.name}:{line_number}")
    return rows


def resolve_run_directory(path: Path) -> Path:
    """Resolve a run directory from either the directory or one artifact file."""
    path = Path(path).expanduser()
    if path.is_dir():
        return path
    if path.is_file() and path.name in {
        "run.json",
        "summary.json",
        "run_report.json",
        "events.jsonl",
    }:
        return path.parent
    raise FileNotFoundError(f"Run directory or recognized run artifact not found: {path}")


def load_run(path: Path) -> NormalizedRun:
    """Load current or partial historical run artifacts without mutating them."""
    directory = resolve_run_directory(path)
    warnings: list[str] = []
    run = NormalizedRun(
        directory=directory,
        manifest=_load_object(directory / "run.json", warnings),
        summary=_load_object(directory / "summary.json", warnings),
        run_report=_load_object(directory / "run_report.json", warnings),
        telemetry_diagnostics=_load_object(directory / "telemetry_diagnostics.json", warnings),
        speed_diagnostics=_load_object(directory / "speed_diagnostics.json", warnings),
        events=_load_jsonl(directory / "events.jsonl", warnings),
        predictions=_load_jsonl(directory / "predictions.jsonl", warnings),
        navigation_updates=_load_jsonl(directory / "navigation_updates.jsonl", warnings),
        warnings=warnings,
    )
    if not run.events:
        warnings.append("No readable events were found; behavioral detectors have limited evidence")
    return run


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _step(event: Mapping[str, Any], fallback: int) -> int:
    value = _integer(event.get("step"))
    return fallback if value is None else value


def _elapsed(event: Mapping[str, Any]) -> float | None:
    return _number(event.get("elapsed_seconds"))


def _room(event: Mapping[str, Any]) -> str | None:
    telemetry = event.get("telemetry")
    if not isinstance(telemetry, Mapping):
        return None
    value = telemetry.get("room_name") or telemetry.get("room_id")
    if value is None or not str(value).strip():
        return None
    return str(value)


def _finding_id(kind: str, *parts: Any) -> str:
    stable = "|".join([kind, *(str(part) for part in parts)])
    digest = hashlib.sha256(stable.encode("utf-8")).hexdigest()[:12]
    return f"{kind}:{digest}"


def _severity_for_room_stall(steps: int, threshold: int) -> str:
    if steps >= threshold * 5:
        return "critical"
    if steps >= threshold * 2:
        return "high"
    return "medium"


def detect_room_stalls(run: NormalizedRun, thresholds: DoctorThresholds) -> list[RunDoctorFinding]:
    findings: list[RunDoctorFinding] = []
    events = run.events
    index = 0
    while index < len(events):
        room = _room(events[index])
        if room is None:
            index += 1
            continue
        end = index
        while end + 1 < len(events) and _room(events[end + 1]) == room:
            end += 1
        count = end - index + 1
        start_time = _elapsed(events[index])
        end_time = _elapsed(events[end])
        duration = (
            max(0.0, end_time - start_time)
            if start_time is not None and end_time is not None
            else None
        )
        if count >= thresholds.room_stall_steps or (
            duration is not None and duration >= thresholds.room_stall_seconds
        ):
            start_step = _step(events[index], index)
            end_step = _step(events[end], end)
            findings.append(
                RunDoctorFinding(
                    finding_id=_finding_id("room_stall", room, start_step, end_step),
                    finding_type="room_stall",
                    title=f"Long stay in room {room}",
                    severity=_severity_for_room_stall(count, thresholds.room_stall_steps),
                    confidence=0.98,
                    subsystem="navigation/planning",
                    explanation=(
                        "The run remained in the same observed room for an unusually long "
                        "contiguous action window. This is a symptom, not proof of a specific "
                        "progression mistake."
                    ),
                    recommendation=(
                        "Inspect the evidence, objective changes, interaction attempts, and "
                        "fallback behavior inside this interval before changing route logic."
                    ),
                    evidence=EvidenceRange(start_step, end_step, start_time, end_time),
                    room=room,
                    measured={"steps": count, "duration_seconds": duration},
                    threshold={
                        "steps": thresholds.room_stall_steps,
                        "duration_seconds": thresholds.room_stall_seconds,
                    },
                )
            )
        index = end + 1
    return findings


def detect_repeated_actions(run: NormalizedRun, thresholds: DoctorThresholds) -> list[RunDoctorFinding]:
    findings: list[RunDoctorFinding] = []
    events = run.events
    index = 0
    while index < len(events):
        action = str(events[index].get("action") or "wait")
        end = index
        while end + 1 < len(events) and str(events[end + 1].get("action") or "wait") == action:
            end += 1
        count = end - index + 1
        if count >= thresholds.repeated_action_streak:
            start_step = _step(events[index], index)
            end_step = _step(events[end], end)
            severity = "high" if count >= thresholds.repeated_action_streak * 4 else "medium"
            findings.append(
                RunDoctorFinding(
                    finding_id=_finding_id("repeated_action", action, start_step, end_step),
                    finding_type="repeated_action_streak",
                    title=f"Repeated {action!r} action streak",
                    severity=severity,
                    confidence=0.99,
                    subsystem="policy/navigation",
                    explanation=(
                        "The same action was selected repeatedly without an intervening action "
                        "change. Long streaks can indicate a stuck target, blocked movement, or "
                        "an over-persistent fallback."
                    ),
                    recommendation=(
                        "Inspect whether observations or target/evidence state changed during the "
                        "streak and whether the policy had a bounded retry condition."
                    ),
                    evidence=EvidenceRange(
                        start_step,
                        end_step,
                        _elapsed(events[index]),
                        _elapsed(events[end]),
                    ),
                    room=_room(events[index]),
                    measured={"action": action, "consecutive_steps": count},
                    threshold={"consecutive_steps": thresholds.repeated_action_streak},
                )
            )
        index = end + 1
    return findings


def _room_transitions(events: Sequence[Mapping[str, Any]]) -> list[tuple[str, int, float | None]]:
    sequence: list[tuple[str, int, float | None]] = []
    previous: str | None = None
    for fallback, event in enumerate(events):
        room = _room(event)
        if room is None or room == previous:
            continue
        sequence.append((room, _step(event, fallback), _elapsed(event)))
        previous = room
    return sequence


def detect_rapid_returns(run: NormalizedRun, thresholds: DoctorThresholds) -> list[RunDoctorFinding]:
    transitions = _room_transitions(run.events)
    findings: list[RunDoctorFinding] = []
    for index in range(len(transitions) - 2):
        room_a, step_a, time_a = transitions[index]
        room_b, step_b, _time_b = transitions[index + 1]
        room_c, step_c, time_c = transitions[index + 2]
        if room_a != room_c or room_a == room_b:
            continue
        return_steps = step_c - step_b
        if return_steps < 0 or return_steps > thresholds.rapid_return_steps:
            continue
        findings.append(
            RunDoctorFinding(
                finding_id=_finding_id("rapid_return", room_a, room_b, step_a, step_c),
                finding_type="rapid_room_return",
                title=f"Rapid {room_a} → {room_b} → {room_a} return",
                severity="high" if return_steps <= 3 else "medium",
                confidence=0.99,
                subsystem="navigation/portal handling",
                explanation=(
                    "The observed room sequence returned to the previous room shortly after the "
                    "transition. This is consistent with an arrival-door bounce or immediate "
                    "backtrack, but the detector does not assume which route was intended."
                ),
                recommendation=(
                    "Inspect arrival-position handling, return-portal suppression, and whether "
                    "the second transition was deliberately selected or automatic."
                ),
                evidence=EvidenceRange(step_a, step_c, time_a, time_c),
                room=room_b,
                measured={
                    "from_room": room_a,
                    "via_room": room_b,
                    "return_steps": return_steps,
                    "arrival_step": step_b,
                },
                threshold={"return_steps": thresholds.rapid_return_steps},
                uncertainties=(
                    "Room telemetry alone cannot distinguish a deliberate backtrack from an accidental bounce.",
                ),
            )
        )
    return findings


def detect_capture_degradation(run: NormalizedRun, thresholds: DoctorThresholds) -> list[RunDoctorFinding]:
    events = run.events
    findings: list[RunDoctorFinding] = []
    if not events:
        return findings

    validity = [bool(event.get("visual_valid", True)) for event in events]
    valid_count = sum(validity)
    ratio = valid_count / len(validity)
    if len(events) >= thresholds.low_visual_min_events and ratio < thresholds.low_visual_valid_ratio:
        findings.append(
            RunDoctorFinding(
                finding_id=_finding_id("low_visual_ratio", len(events), valid_count),
                finding_type="capture_validity_degradation",
                title="Low visual-validity ratio",
                severity="critical" if ratio < 0.20 else "high",
                confidence=1.0,
                subsystem="capture/perception",
                explanation=(
                    "A large fraction of action steps were recorded with visual_valid=false, "
                    "reducing the evidence available to visual planning and classification."
                ),
                recommendation=(
                    "Inspect capture-method diagnostics and correlate invalid periods with policy "
                    "fallback behavior before tuning navigation logic."
                ),
                evidence=EvidenceRange(
                    _step(events[0], 0),
                    _step(events[-1], len(events) - 1),
                    _elapsed(events[0]),
                    _elapsed(events[-1]),
                ),
                measured={
                    "events": len(events),
                    "valid_events": valid_count,
                    "valid_ratio": round(ratio, 6),
                },
                threshold={"minimum_valid_ratio": thresholds.low_visual_valid_ratio},
            )
        )

    index = 0
    while index < len(events):
        if validity[index]:
            index += 1
            continue
        end = index
        while end + 1 < len(events) and not validity[end + 1]:
            end += 1
        count = end - index + 1
        if count >= thresholds.invalid_visual_streak:
            start_step = _step(events[index], index)
            end_step = _step(events[end], end)
            findings.append(
                RunDoctorFinding(
                    finding_id=_finding_id("invalid_visual_streak", start_step, end_step),
                    finding_type="invalid_visual_streak",
                    title="Long invalid visual-capture streak",
                    severity="critical" if count >= thresholds.invalid_visual_streak * 10 else "high",
                    confidence=1.0,
                    subsystem="capture/perception",
                    explanation=(
                        "The agent went many consecutive action steps without a valid visual frame."
                    ),
                    recommendation=(
                        "Inspect capture fallback counters and window/capture state during this exact interval."
                    ),
                    evidence=EvidenceRange(
                        start_step,
                        end_step,
                        _elapsed(events[index]),
                        _elapsed(events[end]),
                    ),
                    room=_room(events[index]),
                    measured={"consecutive_invalid_steps": count},
                    threshold={"consecutive_invalid_steps": thresholds.invalid_visual_streak},
                )
            )
        index = end + 1
    return findings


def analyze_run(
    run: NormalizedRun,
    thresholds: DoctorThresholds | None = None,
) -> RunDoctorReport:
    thresholds = thresholds or DoctorThresholds()
    findings: list[RunDoctorFinding] = []
    for detector in (
        detect_room_stalls,
        detect_repeated_actions,
        detect_rapid_returns,
        detect_capture_degradation,
    ):
        findings.extend(detector(run, thresholds))
    findings.sort(
        key=lambda finding: (
            _SEVERITY_ORDER.get(finding.severity, 99),
            finding.evidence.start_step if finding.evidence.start_step is not None else 10**18,
            finding.finding_id,
        )
    )
    severity_counts = {severity: 0 for severity in _SEVERITY_ORDER}
    for finding in findings:
        severity_counts[finding.severity] = severity_counts.get(finding.severity, 0) + 1
    return RunDoctorReport(
        run_directory=str(run.directory),
        doctor_version=RUN_DOCTOR_VERSION,
        schema_version=RUN_DOCTOR_SCHEMA_VERSION,
        agent_revision=run.agent_revision,
        event_count=len(run.events),
        finding_count=len(findings),
        severity_counts=severity_counts,
        findings=tuple(findings),
        loader_warnings=tuple(run.warnings),
    )


def render_markdown(report: RunDoctorReport) -> str:
    lines = [
        "# Automatic Run Doctor",
        "",
        f"- Doctor version: `{report.doctor_version}`",
        f"- Agent revision: `{report.agent_revision or 'unknown'}`",
        f"- Events analyzed: **{report.event_count}**",
        f"- Findings: **{report.finding_count}**",
        "",
    ]
    nonzero = [f"{key}: {value}" for key, value in report.severity_counts.items() if value]
    lines.append("Severity: " + (", ".join(nonzero) if nonzero else "no findings"))
    lines.append("")
    if report.loader_warnings:
        lines.extend(["## Loader warnings", ""])
        lines.extend(f"- {warning}" for warning in report.loader_warnings)
        lines.append("")
    if not report.findings:
        lines.extend(["## Findings", "", "No v0.1 detector findings.", ""])
        return "\n".join(lines)
    lines.extend(["## Findings", ""])
    for finding in report.findings:
        evidence = finding.evidence
        step_range = (
            f"{evidence.start_step}–{evidence.end_step}"
            if evidence.start_step is not None and evidence.end_step is not None
            else "unknown"
        )
        lines.extend(
            [
                f"### [{finding.severity.upper()}] {finding.title}",
                "",
                f"- Type: `{finding.finding_type}`",
                f"- Confidence: **{finding.confidence:.0%}**",
                f"- Subsystem: **{finding.subsystem}**",
                f"- Evidence steps: **{step_range}**",
                f"- Room: `{finding.room or 'unknown'}`",
                "",
                finding.explanation,
                "",
                f"**Engineering action:** {finding.recommendation}",
                "",
            ]
        )
    return "\n".join(lines)


def write_report(report: RunDoctorReport, output_directory: Path | None = None) -> tuple[Path, Path]:
    destination = Path(output_directory) if output_directory is not None else Path(report.run_directory)
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
        description="Analyze a recorded run without changing learned memory or gameplay policy.",
    )
    parser.add_argument("run", type=Path, help="run directory or one recognized run artifact")
    parser.add_argument("--no-save", action="store_true", help="print results without writing run_doctor files")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON instead of the Markdown summary")
    parser.add_argument("--output", type=Path, help="write reports to a separate directory")
    return parser


def cli(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    run = load_run(args.run)
    report = analyze_run(run)
    if not args.no_save:
        write_report(report, args.output)
    if args.json:
        print(json.dumps(report.as_dict(), indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0


__all__ = [
    "DoctorThresholds",
    "EvidenceRange",
    "NormalizedRun",
    "RunDoctorFinding",
    "RunDoctorReport",
    "RUN_DOCTOR_VERSION",
    "analyze_run",
    "cli",
    "detect_capture_degradation",
    "detect_rapid_returns",
    "detect_repeated_actions",
    "detect_room_stalls",
    "load_run",
    "render_markdown",
    "resolve_run_directory",
    "write_report",
]
