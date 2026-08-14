"""Trusted Run Doctor calibration learned from real archived-run evidence.

This layer stays read-only. It refines generic detector output using only data
already present in the run artifacts. It does not inject route, dialogue, or
progression knowledge into the AI.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import math
from statistics import mean
from typing import Any, Iterable, Mapping

from . import run_doctor as foundation
from . import run_doctor_incidents as incident_engine


RUN_DOCTOR_VERSION = "1.0.1"
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_MOVEMENT_ACTIONS = {"left", "right", "up", "down"}
_DIALOGUE_STATES = {"dialogue", "cutscene"}


def _events_for_finding(
    run: foundation.NormalizedRun,
    finding: foundation.RunDoctorFinding,
) -> list[dict[str, Any]]:
    start = finding.evidence.start_step
    end = finding.evidence.end_step
    if start is None or end is None:
        return []
    return [
        event
        for fallback, event in enumerate(run.events)
        if start <= foundation._step(event, fallback) <= end
    ]


def _position(event: Mapping[str, Any]) -> tuple[float, float] | None:
    telemetry = event.get("telemetry")
    if not isinstance(telemetry, Mapping):
        return None
    x = telemetry.get("player_x", telemetry.get("x"))
    y = telemetry.get("player_y", telemetry.get("y"))
    try:
        x_value = float(x)
        y_value = float(y)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(x_value) or not math.isfinite(y_value):
        return None
    return x_value, y_value


def _movement_metrics(events: list[dict[str, Any]]) -> dict[str, float | int]:
    positions = [position for event in events if (position := _position(event)) is not None]
    total_distance = 0.0
    for first, second in zip(positions, positions[1:]):
        total_distance += math.hypot(second[0] - first[0], second[1] - first[1])
    net_distance = (
        math.hypot(
            positions[-1][0] - positions[0][0],
            positions[-1][1] - positions[0][1],
        )
        if len(positions) >= 2
        else 0.0
    )
    return {
        "position_samples": len(positions),
        "path_distance": round(total_distance, 3),
        "net_displacement": round(net_distance, 3),
    }


def _room_changed(events: list[dict[str, Any]]) -> bool:
    rooms = {
        foundation._room(event)
        for event in events
        if foundation._room(event) is not None
    }
    return len(rooms) > 1


def _expected_repetition(
    run: foundation.NormalizedRun,
    finding: foundation.RunDoctorFinding,
) -> tuple[bool, dict[str, Any]]:
    events = _events_for_finding(run, finding)
    if not events:
        return False, {}
    action = str(finding.measured.get("action") or events[0].get("action") or "")
    reasons = [str(event.get("reason") or "").casefold() for event in events]
    states = [str(event.get("state") or "").casefold() for event in events]
    count = len(events)

    if action == "wait":
        control_lock_ratio = sum("control locked" in reason for reason in reasons) / count
        passive_state_ratio = sum(state in _DIALOGUE_STATES for state in states) / count
        if control_lock_ratio >= 0.80 or passive_state_ratio >= 0.90:
            return True, {
                "expected_reason": "control_lock_or_passive_sequence",
                "control_lock_ratio": round(control_lock_ratio, 3),
                "passive_state_ratio": round(passive_state_ratio, 3),
            }

    if action == "confirm":
        advance_ratio = sum("advance" in reason for reason in reasons) / count
        dialogue_ratio = sum(state in _DIALOGUE_STATES for state in states) / count
        if advance_ratio >= 0.80 and dialogue_ratio >= 0.80:
            return True, {
                "expected_reason": "dialogue_or_cutscene_advancement",
                "advance_reason_ratio": round(advance_ratio, 3),
                "dialogue_cutscene_ratio": round(dialogue_ratio, 3),
            }

    if action in _MOVEMENT_ACTIONS:
        movement = _movement_metrics(events)
        samples = int(movement["position_samples"])
        path_distance = float(movement["path_distance"])
        net_distance = float(movement["net_displacement"])
        expected_floor = max(8.0, max(1, samples - 1) * 0.75)
        if (
            _room_changed(events)
            or path_distance >= expected_floor
            or net_distance >= max(8.0, expected_floor * 0.67)
        ):
            return True, {
                "expected_reason": "productive_sustained_movement",
                **movement,
                "productive_distance_floor": round(expected_floor, 3),
            }
        return False, movement

    return False, {}


def _calibrate_repeated_action(
    run: foundation.NormalizedRun,
    finding: foundation.RunDoctorFinding,
) -> foundation.RunDoctorFinding | None:
    expected, evidence = _expected_repetition(run, finding)
    if expected:
        return None
    if evidence:
        measured = dict(finding.measured)
        measured.update(evidence)
        return replace(
            finding,
            finding_type="unproductive_repeated_action_streak",
            title=f"Unproductive repeated {measured.get('action', 'action')!r} streak",
            confidence=min(finding.confidence, 0.95),
            explanation=(
                "The same action repeated without enough observed movement or state progress "
                "to explain the streak as ordinary sustained traversal."
            ),
            recommendation=(
                "Inspect collision, target persistence, and retry retirement inside this exact "
                "interval before changing broader navigation behavior."
            ),
            measured=measured,
        )
    return finding


def _calibrate_room_stall(
    run: foundation.NormalizedRun,
    finding: foundation.RunDoctorFinding,
) -> foundation.RunDoctorFinding:
    events = _events_for_finding(run, finding)
    if not events:
        return finding
    blind_count = sum(
        "no reachable frontier" in str(event.get("reason") or "").casefold()
        for event in events
    )
    invalid_count = sum(not bool(event.get("visual_valid", True)) for event in events)
    measured = dict(finding.measured)
    measured.update(
        {
            "blind_probe_ratio": round(blind_count / len(events), 6),
            "invalid_visual_ratio": round(invalid_count / len(events), 6),
        }
    )
    last_run_step = (
        foundation._step(run.events[-1], len(run.events) - 1) if run.events else None
    )
    eventually_exited = (
        last_run_step is not None
        and finding.evidence.end_step is not None
        and finding.evidence.end_step < last_run_step
    )
    measured["eventually_exited_room"] = eventually_exited

    # A long residence that ultimately exits is still useful efficiency evidence,
    # but it should not be scored like an end-of-run softlock unless the interval
    # itself is dominated by blind probing or is exceptionally long.
    if eventually_exited and len(events) < 1200 and blind_count / len(events) < 0.20:
        return replace(
            finding,
            severity="medium",
            confidence=min(finding.confidence, 0.90),
            explanation=(
                "The run spent a long contiguous interval in this room but later exited it. "
                "Treat this as an efficiency signal rather than proof of a navigation stall."
            ),
            recommendation=(
                "Inspect why this room required so many actions, while keeping the later "
                "successful exit as evidence that navigation was not permanently stuck."
            ),
            measured=measured,
        )
    return replace(finding, measured=measured)


def _calibrate_objective_churn(
    run: foundation.NormalizedRun,
    finding: foundation.RunDoctorFinding,
) -> foundation.RunDoctorFinding:
    measured = dict(finding.measured)
    changes = measured.get("objective_changes")
    if run.agent_revision == "run20-first-cleaned-run-fixes-v1" and changes == 100:
        measured["historical_counter_may_be_capped"] = True
        uncertainties = tuple(finding.uncertainties) + (
            "This historical agent revision reported objective_changes from a retained history "
            "that could cap at 100, so the saved value may be a lower bound rather than the true total.",
        )
        return replace(
            finding,
            confidence=min(finding.confidence, 0.85),
            explanation=(
                "The saved run reports frequent objective changes. For this historical agent "
                "revision, the exact value 100 may reflect the old retained-history cap, so "
                "the churn signal is valid but the exact count is not fully trustworthy."
            ),
            measured=measured,
            uncertainties=uncertainties,
        )
    return finding


def _requested_multiplier(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _historical_speed_finding(
    run: foundation.NormalizedRun,
) -> foundation.RunDoctorFinding | None:
    speed = run.speed_diagnostics
    if not speed or speed.get("verification_state"):
        return None
    requested = speed.get("requested")
    requested_multiplier = _requested_multiplier(requested)
    detected = speed.get("detected_multiplier")
    synchronized = speed.get("synchronized")
    try:
        speed_packets = int(run.telemetry_diagnostics.get("speed_packets") or 0)
    except (TypeError, ValueError, OverflowError):
        speed_packets = 0
    if (
        requested_multiplier is None
        or requested_multiplier <= 1.0
        or detected is not None
        or (synchronized is not False and speed_packets > 0)
    ):
        return None
    return foundation.RunDoctorFinding(
        finding_id=foundation._finding_id(
            "speed_verification_historical",
            requested,
            detected,
            synchronized,
            speed_packets,
        ),
        finding_type="speed_verification_problem",
        title="Game-speed timing is unverified",
        severity="high" if requested_multiplier >= 2.0 and speed_packets == 0 else "medium",
        confidence=1.0,
        subsystem="timing/telemetry",
        explanation=(
            "This older speed-diagnostics format has no verification_state field, but it records "
            "a manual multiplier above 1x with no detected multiplier and no confirming DRSPEED "
            "telemetry. The AI timing therefore cannot be verified against the game's actual speed."
        ),
        recommendation=(
            "Treat timing-sensitive conclusions cautiously and verify DRSPEED telemetry before "
            "using high manual multipliers for calibration runs."
        ),
        evidence=foundation.EvidenceRange(None, None),
        measured={
            "requested": requested,
            "requested_multiplier": requested_multiplier,
            "detected_multiplier": detected,
            "synchronized": synchronized,
            "speed_packets": speed_packets,
            "source": speed.get("source"),
        },
        threshold={"verified_high_speed_required_above": 1.0},
        uncertainties=(
            "The run artifacts confirm missing speed verification, not the game's unknown true multiplier.",
        ),
    )


def calibrate_base_report(
    run: foundation.NormalizedRun,
    base: foundation.RunDoctorReport,
) -> foundation.RunDoctorReport:
    findings: list[foundation.RunDoctorFinding] = []
    for finding in base.findings:
        calibrated: foundation.RunDoctorFinding | None = finding
        if finding.finding_type == "repeated_action_streak":
            calibrated = _calibrate_repeated_action(run, finding)
        elif finding.finding_type == "room_stall":
            calibrated = _calibrate_room_stall(run, finding)
        elif finding.finding_type == "objective_churn":
            calibrated = _calibrate_objective_churn(run, finding)
        if calibrated is not None:
            findings.append(calibrated)

    if not any(finding.finding_type == "speed_verification_problem" for finding in findings):
        historical_speed = _historical_speed_finding(run)
        if historical_speed is not None:
            findings.append(historical_speed)

    findings.sort(
        key=lambda finding: (
            _SEVERITY_ORDER.get(finding.severity, 99),
            finding.evidence.start_step
            if finding.evidence.start_step is not None
            else 10**18,
            finding.finding_id,
        )
    )
    severity_counts = {severity: 0 for severity in _SEVERITY_ORDER}
    for finding in findings:
        severity_counts[finding.severity] = severity_counts.get(finding.severity, 0) + 1
    return foundation.RunDoctorReport(
        run_directory=base.run_directory,
        doctor_version=base.doctor_version,
        schema_version=base.schema_version,
        agent_revision=base.agent_revision,
        event_count=base.event_count,
        finding_count=len(findings),
        severity_counts=severity_counts,
        findings=tuple(findings),
        loader_warnings=base.loader_warnings,
    )


def _overlap(
    first: foundation.RunDoctorFinding,
    second: foundation.RunDoctorFinding,
    *,
    gap_steps: int = 20,
) -> bool:
    if first.evidence.start_step is None or second.evidence.start_step is None:
        return False
    first_end = first.evidence.end_step or first.evidence.start_step
    second_end = second.evidence.end_step or second.evidence.start_step
    return (
        first.evidence.start_step <= second_end + gap_steps
        and second.evidence.start_step <= first_end + gap_steps
    )


def _compatible_context(
    first: foundation.RunDoctorFinding,
    second: foundation.RunDoctorFinding,
) -> bool:
    # Run-level findings are contextual evidence, not temporal bridges between
    # unrelated room incidents. Two run-level findings can still group together.
    if first.room is None or second.room is None:
        return first.room is None and second.room is None
    return first.room == second.room


def group_findings(
    findings: Iterable[foundation.RunDoctorFinding],
) -> tuple[incident_engine.DoctorIncident, ...]:
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

    incidents: list[incident_engine.DoctorIncident] = []
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
            {"capture_validity_degradation", "blind_search_streak"} <= finding_types
            or {"invalid_visual_streak", "blind_search_streak"} <= finding_types
        ):
            causal_note = (
                "Capture degradation overlaps blind search and is a plausible contributor; "
                "this is correlation, not proof of causation."
            )
        elif (
            "unconsumed_observed_evidence" in finding_types
            and "blind_search_streak" in finding_types
        ):
            causal_note = (
                "Unresolved observed evidence overlaps blind-search behavior and may indicate "
                "an evidence-routing failure; snapshot timing prevents a stronger causal claim."
            )
        incidents.append(
            incident_engine.DoctorIncident(
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


def calibrate_incident_report(
    run: foundation.NormalizedRun,
    report: incident_engine.IncidentDoctorReport,
) -> incident_engine.IncidentDoctorReport:
    base = calibrate_base_report(run, report.base)
    return incident_engine.IncidentDoctorReport(
        base=base,
        incidents=group_findings(base.findings),
        health=incident_engine.health_scores(base.findings),
    )


__all__ = [
    "RUN_DOCTOR_VERSION",
    "calibrate_base_report",
    "calibrate_incident_report",
    "group_findings",
]
