"""Trusted Run Doctor v1.0.2 calibration from multiple real-run reviews.

This layer refines v1.0.1 using only evidence already recorded by the agent.
It never injects route, dialogue, object, or progression knowledge and never
mutates learned state.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, Mapping

from . import run_doctor as foundation
from . import run_doctor_calibration as v101
from . import run_doctor_incidents as incident_engine


RUN_DOCTOR_VERSION = "1.0.2"
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_TARGET_HYPOTHESES = {"possible_character", "possible_interactable"}
_ACTIONABLE_GUESS_STATES = {"proposed", "approaching"}
_BLIND_MARKER = "no reachable frontier; probe"


def _rebuild_report(
    base: foundation.RunDoctorReport,
    findings: Iterable[foundation.RunDoctorFinding],
) -> foundation.RunDoctorReport:
    rows = list(findings)
    rows.sort(
        key=lambda finding: (
            _SEVERITY_ORDER.get(finding.severity, 99),
            finding.evidence.start_step
            if finding.evidence.start_step is not None
            else 10**18,
            finding.finding_id,
        )
    )
    severity_counts = {severity: 0 for severity in _SEVERITY_ORDER}
    for finding in rows:
        severity_counts[finding.severity] = severity_counts.get(finding.severity, 0) + 1
    return foundation.RunDoctorReport(
        run_directory=base.run_directory,
        doctor_version=base.doctor_version,
        schema_version=base.schema_version,
        agent_revision=base.agent_revision,
        event_count=base.event_count,
        finding_count=len(rows),
        severity_counts=severity_counts,
        findings=tuple(rows),
        loader_warnings=base.loader_warnings,
    )


def _screen_region_updates(run: foundation.NormalizedRun) -> list[tuple[int, Mapping[str, Any]]]:
    rows: list[tuple[int, Mapping[str, Any]]] = []
    for record in run.navigation_updates:
        if not isinstance(record, Mapping):
            continue
        update = record.get("update")
        if not isinstance(update, Mapping) or str(update.get("type") or "") != "screen_region":
            continue
        step = foundation._integer(record.get("step"))
        if step is None:
            continue
        rows.append((step, update))
    rows.sort(key=lambda item: item[0])
    return rows


def _is_actionable_region(update: Mapping[str, Any]) -> bool:
    if str(update.get("hypothesis") or "") not in _TARGET_HYPOTHESES:
        return False
    state = str(update.get("guess_state") or "proposed")
    if state not in _ACTIONABLE_GUESS_STATES:
        return False
    try:
        completed = int(update.get("completed_tests", update.get("inspections", 0)) or 0)
    except (TypeError, ValueError, OverflowError):
        completed = 0
    return completed == 0


def _longest_consecutive(steps: list[int]) -> int:
    if not steps:
        return 0
    longest = 1
    current = 1
    for first, second in zip(steps, steps[1:]):
        if second == first + 1:
            current += 1
            longest = max(longest, current)
        else:
            current = 1
    return longest


def _evidence_routing_findings(
    run: foundation.NormalizedRun,
    updates: list[tuple[int, Mapping[str, Any]]] | None = None,
) -> list[foundation.RunDoctorFinding]:
    """Correlate blind probes with evidence proven usable before that exact step.

    v0.2 used the final navigation snapshot, which could back-date future evidence
    or combine evidence from another room. v1.0.2 reconstructs the latest recorded
    screen-region state strictly before each blind-probe decision. Qualifying
    episodes are aggregated to one finding per room to avoid warning spam.
    """

    updates = _screen_region_updates(run) if updates is None else updates
    if not updates or not run.events:
        return []

    record_type = tuple[int, float | None, str, tuple[tuple[str, tuple[int, ...]], ...]]
    states: dict[tuple[str, tuple[int, ...]], Mapping[str, Any]] = {}
    update_index = 0
    overlap_records: list[record_type] = []

    for fallback, event in enumerate(run.events):
        step = foundation._step(event, fallback)
        while update_index < len(updates) and updates[update_index][0] < step:
            _update_step, update = updates[update_index]
            room = str(update.get("room") or "")
            region_raw = update.get("region")
            region = (
                tuple(int(value) for value in region_raw)
                if isinstance(region_raw, (list, tuple))
                else ()
            )
            states[(room, region)] = update
            update_index += 1

        if _BLIND_MARKER not in str(event.get("reason") or "").casefold():
            continue
        room = foundation._room(event)
        if room is None:
            continue
        actionable: list[tuple[str, tuple[int, ...]]] = []
        for (candidate_room, region), update in states.items():
            if candidate_room != room or not _is_actionable_region(update):
                continue
            actionable.append((str(update.get("hypothesis") or "unknown"), region))
        if actionable:
            overlap_records.append(
                (
                    step,
                    foundation._elapsed(event),
                    room,
                    tuple(sorted(set(actionable))),
                )
            )

    if not overlap_records:
        return []

    by_room: dict[str, list[record_type]] = {}
    for record in overlap_records:
        by_room.setdefault(record[2], []).append(record)

    findings: list[foundation.RunDoctorFinding] = []
    for room in sorted(by_room):
        records = sorted(by_room[room], key=lambda item: item[0])
        episodes: list[list[record_type]] = []
        current = [records[0]]
        for record in records[1:]:
            if record[0] - current[-1][0] <= 5:
                current.append(record)
            else:
                episodes.append(current)
                current = [record]
        episodes.append(current)

        # One or two isolated decisions are trace evidence, not enough to score.
        qualifying = [episode for episode in episodes if len(episode) >= 3]
        if not qualifying:
            continue

        qualifying_records = [record for episode in qualifying for record in episode]
        all_steps = [record[0] for record in qualifying_records]
        episode_summaries = []
        longest = 0
        for episode in qualifying:
            steps = [record[0] for record in episode]
            episode_longest = _longest_consecutive(steps)
            longest = max(longest, episode_longest)
            episode_summaries.append(
                {
                    "start_step": steps[0],
                    "end_step": steps[-1],
                    "overlap_steps": len(episode),
                    "longest_consecutive_overlap": episode_longest,
                }
            )

        unique_regions = sorted(
            {
                (hypothesis, region)
                for _step, _elapsed, _room, evidence in qualifying_records
                for hypothesis, region in evidence
            },
            key=lambda item: (item[0], item[1]),
        )
        total = len(qualifying_records)
        severity = "high" if total >= 20 or longest >= 10 else "medium"
        findings.append(
            foundation.RunDoctorFinding(
                finding_id=foundation._finding_id(
                    "actionable_evidence_blind_overlap",
                    room,
                    all_steps[0],
                    all_steps[-1],
                    total,
                    longest,
                ),
                finding_type="unconsumed_observed_evidence",
                title="Actionable observed evidence was bypassed during blind search",
                severity=severity,
                confidence=0.97,
                subsystem="planning/evidence utilization",
                explanation=(
                    "Recorded screen-region history shows same-room character/interactable "
                    "evidence in an actionable, zero-test state before repeated no-frontier "
                    "blind probes. This is a direct evidence-routing conflict rather than an "
                    "end-of-run snapshot correlation."
                ),
                recommendation=(
                    "Inspect why the policy chose blind probing while this already-observed "
                    "same-room evidence remained actionable."
                ),
                evidence=foundation.EvidenceRange(
                    all_steps[0],
                    all_steps[-1],
                    qualifying_records[0][1],
                    qualifying_records[-1][1],
                ),
                room=room,
                measured={
                    "blind_probe_steps_with_actionable_evidence": total,
                    "longest_consecutive_overlap": longest,
                    "qualifying_episode_count": len(qualifying),
                    "episodes": episode_summaries,
                    "actionable_regions": [
                        {"hypothesis": hypothesis, "region": list(region)}
                        for hypothesis, region in unique_regions
                    ],
                    "actionable_region_count": len(unique_regions),
                },
                threshold={
                    "minimum_overlap_steps_per_episode": 3,
                    "maximum_gap_within_episode": 5,
                },
                uncertainties=(
                    "The finding proves recorded evidence was actionable before these decisions; "
                    "it does not assert that any specific hypothesis was the correct story route.",
                ),
            )
        )
    return findings


def _warp_updates(run: foundation.NormalizedRun) -> list[tuple[int, Mapping[str, Any]]]:
    rows: list[tuple[int, Mapping[str, Any]]] = []
    for record in run.navigation_updates:
        if not isinstance(record, Mapping):
            continue
        update = record.get("update")
        if not isinstance(update, Mapping) or str(update.get("type") or "") != "warp":
            continue
        step = foundation._integer(record.get("step"))
        if step is None:
            continue
        rows.append((step, update))
    rows.sort(key=lambda item: item[0])
    return rows


def _known_warp_underuse_findings(
    run: foundation.NormalizedRun,
    findings: Iterable[foundation.RunDoctorFinding],
) -> list[foundation.RunDoctorFinding]:
    """Find terminal stalls where an already learned room exit was never selected."""

    warp_updates = _warp_updates(run)
    if not warp_updates:
        return []
    results: list[foundation.RunDoctorFinding] = []
    for stall in findings:
        if stall.finding_type != "room_stall" or stall.room is None:
            continue
        if bool(stall.measured.get("eventually_exited_room")):
            continue
        start = stall.evidence.start_step
        end = stall.evidence.end_step
        if start is None or end is None:
            continue
        interval_events = [
            event
            for fallback, event in enumerate(run.events)
            if start <= foundation._step(event, fallback) <= end
        ]
        if not interval_events:
            continue

        available: dict[tuple[str, str, tuple[int, ...]], int] = {}
        for learned_step, update in warp_updates:
            if learned_step >= start or str(update.get("from_room") or "") != stall.room:
                continue
            destination = str(update.get("to_room") or "unknown")
            action = str(update.get("action") or "unknown")
            cell_raw = update.get("from_cell")
            cell = (
                tuple(int(value) for value in cell_raw)
                if isinstance(cell_raw, (list, tuple))
                else ()
            )
            key = (destination, action, cell)
            available[key] = min(learned_step, available.get(key, learned_step))
        if not available:
            continue

        selected_steps = [
            foundation._step(event, fallback)
            for fallback, event in enumerate(interval_events)
            if "follow learned warp" in str(event.get("reason") or "").casefold()
        ]
        if selected_steps:
            continue

        severity = "high" if len(interval_events) >= 300 else "medium"
        results.append(
            foundation.RunDoctorFinding(
                finding_id=foundation._finding_id(
                    "known_warp_underused",
                    stall.room,
                    start,
                    end,
                    *sorted(available),
                ),
                finding_type="known_warp_underused_during_stall",
                title="Known learned warp was not selected during terminal room stall",
                severity=severity,
                confidence=0.97,
                subsystem="planning/evidence utilization",
                explanation=(
                    "Before this terminal room-stall interval began, the run had already "
                    "recorded at least one successful warp out of the same room. No action in "
                    "the interval was labeled as selecting a learned warp. This identifies an "
                    "unused learned recovery option without assuming it was the intended route."
                ),
                recommendation=(
                    "Inspect why known same-room warps were not considered as bounded recovery "
                    "options after local exploration stopped making progress."
                ),
                evidence=foundation.EvidenceRange(
                    start,
                    end,
                    stall.evidence.start_seconds,
                    stall.evidence.end_seconds,
                ),
                room=stall.room,
                measured={
                    "known_warps_available_before_stall": len(available),
                    "known_warp_destinations": sorted({key[0] for key in available}),
                    "earliest_known_warp_step": min(available.values()),
                    "selected_learned_warp_steps": 0,
                    "stall_steps": len(interval_events),
                },
                threshold={"known_warps_available_before_stall": 1},
                uncertainties=(
                    "A learned warp is an observed recovery option; this finding does not claim "
                    "that taking it was the correct story-progression choice.",
                ),
            )
        )
    return results


def _requested_multiplier(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _calibrate_current_speed(
    run: foundation.NormalizedRun,
    finding: foundation.RunDoctorFinding,
) -> foundation.RunDoctorFinding:
    speed = run.speed_diagnostics
    requested = _requested_multiplier(speed.get("requested")) if speed else None
    try:
        speed_packets = int(run.telemetry_diagnostics.get("speed_packets") or 0)
    except (TypeError, ValueError, OverflowError):
        speed_packets = 0
    state = str(speed.get("verification_state") or "") if speed else ""
    if (
        requested is None
        or requested < 2.0
        or speed_packets > 0
        or state not in {"unverified", "missing_or_stale"}
    ):
        return finding
    measured = dict(finding.measured)
    measured.update(
        {
            "requested_multiplier": requested,
            "speed_packets": speed_packets,
            "synchronized": speed.get("synchronized"),
        }
    )
    uncertainty = (
        "The artifacts prove missing speed verification, not the game's unknown true multiplier."
    )
    uncertainties = tuple(finding.uncertainties)
    if uncertainty not in uncertainties:
        uncertainties += (uncertainty,)
    return replace(
        finding,
        severity="high",
        confidence=1.0,
        explanation=(
            "A high manual game-speed multiplier was requested, but saved telemetry contains "
            "no confirming DRSPEED packets and the speed state is unverified. Timing-dependent "
            "behavior therefore cannot be calibrated against measured game speed."
        ),
        recommendation=(
            "Verify AI Support/Speed DRSPEED telemetry before using high manual speed for "
            "behavioral calibration; otherwise compare timing-sensitive conclusions cautiously."
        ),
        measured=measured,
        uncertainties=uncertainties,
    )


def calibrate_base_report(
    run: foundation.NormalizedRun,
    raw_base: foundation.RunDoctorReport,
) -> foundation.RunDoctorReport:
    base = v101.calibrate_base_report(run, raw_base)
    lifecycle_updates = _screen_region_updates(run)
    has_lifecycle_history = bool(lifecycle_updates)
    findings: list[foundation.RunDoctorFinding] = []
    for finding in base.findings:
        # Exact lifecycle evidence supersedes the older final-snapshot correlation.
        # Snapshot-only historical runs keep the v1.0.1 lower-confidence finding
        # rather than silently losing a detector family they cannot reconstruct.
        if (
            finding.finding_type == "unconsumed_observed_evidence"
            and has_lifecycle_history
        ):
            continue
        if finding.finding_type == "speed_verification_problem":
            finding = _calibrate_current_speed(run, finding)
        findings.append(finding)

    if has_lifecycle_history:
        findings.extend(_evidence_routing_findings(run, lifecycle_updates))
    findings.extend(_known_warp_underuse_findings(run, findings))

    # Stable ID de-duplication protects comparison output if future detector layers
    # learn to emit one of these calibrated finding families natively.
    unique: dict[str, foundation.RunDoctorFinding] = {}
    for finding in findings:
        unique[finding.finding_id] = finding
    return _rebuild_report(base, unique.values())


def group_findings(
    findings: Iterable[foundation.RunDoctorFinding],
) -> tuple[incident_engine.DoctorIncident, ...]:
    rows = list(findings)
    room_specific = [finding for finding in rows if finding.room is not None]
    global_findings = [finding for finding in rows if finding.room is None]

    incidents = list(v101.group_findings(room_specific))
    # Global intervals normally span the whole run, so temporal overlap between
    # two global findings is not evidence that they are one incident. Keep them
    # separate unless a future explicit causal rule groups them intentionally.
    for finding in global_findings:
        incidents.extend(v101.group_findings([finding]))

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
