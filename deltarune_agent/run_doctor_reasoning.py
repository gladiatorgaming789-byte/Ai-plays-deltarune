"""Run Doctor v0.2 reasoning diagnostics layered over the v0.1 foundation.

This module remains read-only. It diagnoses evidence use, interaction retry
behavior, objective/filter churn, and telemetry/speed health from recorded run
artifacts. It never supplies a game route or mutates learned state.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import run_doctor as foundation


RUN_DOCTOR_VERSION = "0.2.0"


def _reason(event: Mapping[str, Any]) -> str:
    return str(event.get("reason") or "")


def _updated_report(
    base: foundation.RunDoctorReport,
    findings: list[foundation.RunDoctorFinding],
) -> foundation.RunDoctorReport:
    combined = [*base.findings, *findings]
    combined.sort(
        key=lambda finding: (
            foundation._SEVERITY_ORDER.get(finding.severity, 99),
            finding.evidence.start_step
            if finding.evidence.start_step is not None
            else 10**18,
            finding.finding_id,
        )
    )
    severity_counts = {severity: 0 for severity in foundation._SEVERITY_ORDER}
    for finding in combined:
        severity_counts[finding.severity] = severity_counts.get(finding.severity, 0) + 1
    return foundation.RunDoctorReport(
        run_directory=base.run_directory,
        doctor_version=RUN_DOCTOR_VERSION,
        schema_version=base.schema_version,
        agent_revision=base.agent_revision,
        event_count=base.event_count,
        finding_count=len(combined),
        severity_counts=severity_counts,
        findings=tuple(combined),
        loader_warnings=base.loader_warnings,
    )


def detect_blind_search(
    run: foundation.NormalizedRun,
    *,
    streak_threshold: int = 20,
) -> list[foundation.RunDoctorFinding]:
    events = run.events
    findings: list[foundation.RunDoctorFinding] = []
    markers = ("no reachable frontier; probe", "blind probe")
    index = 0
    while index < len(events):
        if not any(marker in _reason(events[index]).casefold() for marker in markers):
            index += 1
            continue
        end = index
        while end + 1 < len(events) and any(
            marker in _reason(events[end + 1]).casefold() for marker in markers
        ):
            end += 1
        count = end - index + 1
        if count >= streak_threshold:
            start_step = foundation._step(events[index], index)
            end_step = foundation._step(events[end], end)
            findings.append(
                foundation.RunDoctorFinding(
                    finding_id=foundation._finding_id(
                        "blind_search", start_step, end_step
                    ),
                    finding_type="blind_search_streak",
                    title="Long no-frontier blind-search streak",
                    severity="critical" if count >= streak_threshold * 10 else "high",
                    confidence=0.99,
                    subsystem="planning/evidence utilization",
                    explanation=(
                        "The policy explicitly reported that no reachable frontier was "
                        "available and repeatedly fell back to unguided probing. This "
                        "identifies low-information search without assuming the correct route."
                    ),
                    recommendation=(
                        "Inspect whether learned interactions, visual hypotheses, known warps, "
                        "or other observed evidence remained available before fallback began."
                    ),
                    evidence=foundation.EvidenceRange(
                        start_step,
                        end_step,
                        foundation._elapsed(events[index]),
                        foundation._elapsed(events[end]),
                    ),
                    room=foundation._room(events[index]),
                    measured={"consecutive_blind_probe_steps": count},
                    threshold={"consecutive_blind_probe_steps": streak_threshold},
                )
            )
        index = end + 1
    return findings


def detect_failed_interactions(
    run: foundation.NormalizedRun,
    *,
    failure_threshold: int = 4,
) -> list[foundation.RunDoctorFinding]:
    failures: dict[tuple[str, str], list[tuple[int, float | None]]] = {}
    for fallback, event in enumerate(run.events):
        updates = event.get("map_updates")
        if not isinstance(updates, list):
            continue
        for update in updates:
            if not isinstance(update, Mapping):
                continue
            if str(update.get("type") or "") != "character_probe":
                continue
            if str(update.get("result") or "").casefold() != "no response":
                continue
            room = str(update.get("room") or foundation._room(event) or "unknown")
            direction = str(update.get("direction") or "unknown")
            failures.setdefault((room, direction), []).append(
                (foundation._step(event, fallback), foundation._elapsed(event))
            )

    findings: list[foundation.RunDoctorFinding] = []
    for (room, direction), records in sorted(failures.items()):
        if len(records) < failure_threshold:
            continue
        findings.append(
            foundation.RunDoctorFinding(
                finding_id=foundation._finding_id(
                    "failed_interactions",
                    room,
                    direction,
                    records[0][0],
                    records[-1][0],
                ),
                finding_type="repeated_failed_interaction",
                title=f"Repeated no-response interactions in {room}",
                severity="high" if len(records) >= failure_threshold * 2 else "medium",
                confidence=1.0,
                subsystem="interaction/planning",
                explanation=(
                    "Structured interaction-probe updates recorded repeated confirm attempts "
                    "that produced no game-state response from the same room/facing class."
                ),
                recommendation=(
                    "Inspect retry/cooldown retirement logic and whether evidence was being "
                    "reselected after its failed tests were already known."
                ),
                evidence=foundation.EvidenceRange(
                    records[0][0], records[-1][0], records[0][1], records[-1][1]
                ),
                room=room,
                measured={"no_response_attempts": len(records), "direction": direction},
                threshold={"no_response_attempts": failure_threshold},
            )
        )
    return findings


def _navigation_snapshot(run: foundation.NormalizedRun) -> dict[str, Any]:
    path = run.directory / "navigation.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def detect_unconsumed_evidence(
    run: foundation.NormalizedRun,
) -> list[foundation.RunDoctorFinding]:
    blind_indexes = [
        index
        for index, event in enumerate(run.events)
        if "no reachable frontier; probe" in _reason(event).casefold()
    ]
    if not blind_indexes:
        return []
    rows = _navigation_snapshot(run).get("screen_regions")
    if not isinstance(rows, list):
        return []

    unresolved: list[Mapping[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("hypothesis") or "") not in {
            "possible_character",
            "possible_interactable",
        }:
            continue
        state = str(row.get("guess_state") or "proposed")
        try:
            tests = int(row.get("completed_tests", row.get("inspections", 0)) or 0)
        except (TypeError, ValueError, OverflowError):
            tests = 0
        if state in {"rejected", "retired", "confirmed"} or tests > 0:
            continue
        unresolved.append(row)
    if not unresolved:
        return []

    first = blind_indexes[0]
    last = blind_indexes[-1]
    rooms = sorted({str(row.get("room") or "unknown") for row in unresolved})
    return [
        foundation.RunDoctorFinding(
            finding_id=foundation._finding_id(
                "unconsumed_evidence", len(unresolved), *rooms
            ),
            finding_type="unconsumed_observed_evidence",
            title="Observed entity evidence remained untested during a blind-search run",
            severity="high",
            confidence=0.78,
            subsystem="planning/evidence utilization",
            explanation=(
                "The final learned navigation snapshot contains unresolved possible-character/"
                "interactable evidence with zero completed tests, while the action log contains "
                "no-frontier blind probes. End-of-run snapshot timing makes this correlation, "
                "not proof that each hypothesis existed for the entire interval."
            ),
            recommendation=(
                "Reconstruct the evidence lifecycle around the first blind-search interval and "
                "verify observed entity evidence is considered before unguided probing."
            ),
            evidence=foundation.EvidenceRange(
                foundation._step(run.events[first], first),
                foundation._step(run.events[last], last),
                foundation._elapsed(run.events[first]),
                foundation._elapsed(run.events[last]),
            ),
            room=rooms[0] if len(rooms) == 1 else None,
            measured={"unresolved_zero_test_entities": len(unresolved), "rooms": rooms},
            threshold={"unresolved_zero_test_entities": 1},
            uncertainties=(
                "The end-of-run navigation snapshot does not prove the exact step when each hypothesis first became available.",
            ),
        )
    ]


def _summary_value(run: foundation.NormalizedRun, key: str) -> int | None:
    for source in (
        run.summary,
        run.run_report.get("policy_summary")
        if isinstance(run.run_report.get("policy_summary"), Mapping)
        else {},
    ):
        value = source.get(key) if isinstance(source, Mapping) else None
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError, OverflowError):
            continue
    return None


def detect_objective_churn(
    run: foundation.NormalizedRun,
    *,
    change_threshold: int = 50,
) -> list[foundation.RunDoctorFinding]:
    changes = _summary_value(run, "objective_changes")
    if changes is None or changes < change_threshold:
        return []
    ratio = changes / max(1, len(run.events))
    return [
        foundation.RunDoctorFinding(
            finding_id=foundation._finding_id("objective_churn", changes, len(run.events)),
            finding_type="objective_churn",
            title="Frequent objective changes",
            severity="high" if ratio >= 0.10 else "medium",
            confidence=0.95,
            subsystem="planning/objectives",
            explanation=(
                "The run summary reports many objective changes relative to recorded action. "
                "Frequent replanning can be healthy, so this flags churn for inspection rather "
                "than asserting that the objectives were wrong."
            ),
            recommendation=(
                "Inspect objective lifetimes and whether changes were driven by new evidence or "
                "repeated fallback cycling."
            ),
            evidence=foundation.EvidenceRange(
                foundation._step(run.events[0], 0) if run.events else None,
                foundation._step(run.events[-1], len(run.events) - 1)
                if run.events
                else None,
            ),
            measured={
                "objective_changes": changes,
                "changes_per_event": round(ratio, 6),
            },
            threshold={"objective_changes": change_threshold},
        )
    ]


def detect_filter_pressure(
    run: foundation.NormalizedRun,
    *,
    ratio_threshold: float = 0.25,
) -> list[foundation.RunDoctorFinding]:
    count = _summary_value(run, "single_side_interactable_routes_suppressed")
    if count is None or not run.events:
        return []
    ratio = count / len(run.events)
    if ratio < ratio_threshold:
        return []
    return [
        foundation.RunDoctorFinding(
            finding_id=foundation._finding_id("filter_pressure", count, len(run.events)),
            finding_type="evidence_filter_pressure",
            title="High interactable-evidence suppression pressure",
            severity="high" if ratio >= 0.75 else "medium",
            confidence=0.95,
            subsystem="planning/evidence filtering",
            explanation=(
                "Interactable-route suppression is high relative to action count. In older runs "
                "this may reflect repeated evaluation accounting rather than unique evidence."
            ),
            recommendation=(
                "Inspect both filter semantics and counter accounting before treating the raw "
                "count as unique rejected evidence."
            ),
            evidence=foundation.EvidenceRange(
                foundation._step(run.events[0], 0),
                foundation._step(run.events[-1], len(run.events) - 1),
            ),
            measured={
                "suppression_count": count,
                "suppression_per_event": round(ratio, 6),
            },
            threshold={"suppression_per_event": ratio_threshold},
            uncertainties=(
                "Older agent revisions may count repeated evaluations rather than unique evidence suppressions.",
            ),
        )
    ]


def detect_telemetry_speed_health(
    run: foundation.NormalizedRun,
) -> list[foundation.RunDoctorFinding]:
    findings: list[foundation.RunDoctorFinding] = []
    telemetry = run.telemetry_diagnostics
    if telemetry and not telemetry.get("disabled") and not telemetry.get("unavailable"):
        try:
            received = int(telemetry.get("received_packets") or 0)
            valid = int(telemetry.get("valid_packets") or 0)
            invalid = int(telemetry.get("invalid_packets") or 0)
        except (TypeError, ValueError, OverflowError):
            received = valid = invalid = 0
        if received == 0:
            findings.append(
                foundation.RunDoctorFinding(
                    finding_id=foundation._finding_id(
                        "telemetry_missing", run.agent_revision
                    ),
                    finding_type="telemetry_missing",
                    title="No telemetry packets received",
                    severity="high",
                    confidence=1.0,
                    subsystem="telemetry",
                    explanation=(
                        "Telemetry was enabled but saved diagnostics report zero received packets."
                    ),
                    recommendation=(
                        "Verify the Telemetry/AI Support mod, UDP port, and protocol before "
                        "attributing downstream uncertainty to policy logic."
                    ),
                    evidence=foundation.EvidenceRange(None, None),
                    measured={"received_packets": received},
                    threshold={"minimum_received_packets": 1},
                )
            )
        elif invalid / max(1, received) >= 0.05:
            findings.append(
                foundation.RunDoctorFinding(
                    finding_id=foundation._finding_id(
                        "telemetry_invalid_ratio", received, invalid
                    ),
                    finding_type="telemetry_invalid_packets",
                    title="Elevated invalid telemetry packet ratio",
                    severity="medium",
                    confidence=1.0,
                    subsystem="telemetry",
                    explanation=(
                        "A material share of received UDP packets failed telemetry parsing."
                    ),
                    recommendation=(
                        "Inspect protocol/version compatibility and packet diagnostics before "
                        "using telemetry-dependent conclusions."
                    ),
                    evidence=foundation.EvidenceRange(None, None),
                    measured={
                        "received_packets": received,
                        "valid_packets": valid,
                        "invalid_packets": invalid,
                        "invalid_ratio": round(invalid / max(1, received), 6),
                    },
                    threshold={"invalid_ratio": 0.05},
                )
            )

    speed = run.speed_diagnostics
    if speed:
        state = str(speed.get("verification_state") or "")
        requested = speed.get("requested")
        detected = speed.get("detected_multiplier")
        if state in {"unverified", "mismatch", "missing_or_stale"}:
            findings.append(
                foundation.RunDoctorFinding(
                    finding_id=foundation._finding_id(
                        "speed_verification", state, requested, detected
                    ),
                    finding_type="speed_verification_problem",
                    title=f"Game-speed timing is {state.replace('_', ' ')}",
                    severity="high" if state == "mismatch" else "medium",
                    confidence=1.0,
                    subsystem="timing/telemetry",
                    explanation=(
                        "Saved speed diagnostics do not confirm that AI timing matched the "
                        "game's measured DRSPEED state."
                    ),
                    recommendation=(
                        "Treat timing-sensitive behavioral conclusions cautiously and verify "
                        "Speed/AI Support telemetry before the next run."
                    ),
                    evidence=foundation.EvidenceRange(None, None),
                    measured={
                        "requested": requested,
                        "detected_multiplier": detected,
                        "verification_state": state,
                        "source": speed.get("source"),
                    },
                    threshold={"verification_state": "matched"},
                )
            )
    return findings


def analyze_run(
    run: foundation.NormalizedRun,
    thresholds: foundation.DoctorThresholds | None = None,
) -> foundation.RunDoctorReport:
    base = foundation.analyze_run(run, thresholds)
    findings: list[foundation.RunDoctorFinding] = []
    findings.extend(detect_blind_search(run))
    findings.extend(detect_failed_interactions(run))
    findings.extend(detect_unconsumed_evidence(run))
    findings.extend(detect_objective_churn(run))
    findings.extend(detect_filter_pressure(run))
    findings.extend(detect_telemetry_speed_health(run))
    return _updated_report(base, findings)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m deltarune_agent run-doctor",
        description=(
            "Analyze a recorded run without changing learned memory or gameplay policy."
        ),
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
        foundation.write_report(report, args.output)
    if args.json:
        print(json.dumps(report.as_dict(), indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(foundation.render_markdown(report))
    return 0


__all__ = [
    "RUN_DOCTOR_VERSION",
    "analyze_run",
    "cli",
    "detect_blind_search",
    "detect_failed_interactions",
    "detect_filter_pressure",
    "detect_objective_churn",
    "detect_telemetry_speed_health",
    "detect_unconsumed_evidence",
]
