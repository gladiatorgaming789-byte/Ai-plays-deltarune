"""Trusted Run Doctor v1.0.3 calibration from the eight-run 2026-08-15 set.

Adds detectors for failure families that became clear only across repeated live
runs: weak one-sided entity chase streaks, repeated two-room ping-pong, leaked
unresolved exit semantics, and stale visual capture while telemetry movement
continues. The detector remains read-only and route-agnostic.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from . import run_doctor as foundation
from . import run_doctor_calibration_v102 as v102
from . import run_doctor_incidents as incident_engine


RUN_DOCTOR_VERSION = "1.0.3"
WEAK_GUESS_STREAK = 8
PINGPONG_CROSSINGS = 4
PINGPONG_WINDOW_STEPS = 220
MOVING_INVALID_STREAK = 30
UNRESOLVED_EXIT_STATES = {
    "geometry_candidate",
    "needs_approach_evidence",
    "visual_candidate",
    "contradicted",
}


def _prediction_step(record: Mapping[str, Any], fallback: int) -> int:
    value = foundation._integer(record.get("step"))
    return fallback if value is None else value


def _guess_key(guess_id: object) -> tuple[str, tuple[int, int]] | None:
    text = str(guess_id or "")
    if "@" not in text or "," not in text:
        return None
    room, coordinates = text.rsplit("@", 1)
    left, right = coordinates.split(",", 1)
    try:
        return room, (int(left), int(right))
    except (TypeError, ValueError):
        return None


def _weak_guess_chase_findings(
    run: foundation.NormalizedRun,
) -> list[foundation.RunDoctorFinding]:
    updates = v102._screen_region_updates(run)
    if not updates or not run.predictions:
        return []

    states: dict[tuple[str, tuple[int, ...]], Mapping[str, Any]] = {}
    update_index = 0
    rows: list[tuple[int, float | None, str, str, Mapping[str, Any]]] = []
    predictions = sorted(
        enumerate(run.predictions),
        key=lambda item: _prediction_step(item[1], item[0]),
    )
    for fallback, prediction in predictions:
        step = _prediction_step(prediction, fallback)
        while update_index < len(updates) and updates[update_index][0] <= step:
            _update_step, update = updates[update_index]
            room = str(update.get("room") or "")
            raw_region = update.get("region")
            region = (
                tuple(int(value) for value in raw_region)
                if isinstance(raw_region, (list, tuple))
                else ()
            )
            states[(room, region)] = update
            update_index += 1

        snapshot = prediction.get("prediction_snapshot")
        if not isinstance(snapshot, Mapping):
            continue
        guess_id = str(snapshot.get("selected_guess_id") or "")
        key = _guess_key(guess_id)
        if key is None:
            continue
        room, region = key
        update = states.get((room, region))
        if not isinstance(update, Mapping):
            continue
        try:
            sides = int(update.get("entity_approach_directions") or 0)
            targets = int(update.get("obstruction_target_cells") or 0)
            tests = int(update.get("completed_tests", update.get("inspections", 0)) or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        if sides != 1 or not 1 <= targets <= 4 or tests > 0:
            continue
        if str(update.get("guess_state") or "proposed") in {
            "confirmed",
            "rejected",
            "retired",
        }:
            continue
        hypothesis = str(update.get("hypothesis") or "")
        if hypothesis not in {"possible_character", "possible_interactable"}:
            continue
        rows.append(
            (
                step,
                foundation._elapsed(prediction),
                room,
                guess_id,
                update,
            )
        )

    if not rows:
        return []

    streaks: list[list[tuple[int, float | None, str, str, Mapping[str, Any]]]] = []
    current = [rows[0]]
    for row in rows[1:]:
        previous = current[-1]
        if row[3] == previous[3] and row[0] == previous[0] + 1:
            current.append(row)
        else:
            streaks.append(current)
            current = [row]
    streaks.append(current)

    findings: list[foundation.RunDoctorFinding] = []
    for streak in streaks:
        if len(streak) < WEAK_GUESS_STREAK:
            continue
        first = streak[0]
        last = streak[-1]
        record = last[4]
        severity = "high" if len(streak) >= 16 else "medium"
        findings.append(
            foundation.RunDoctorFinding(
                finding_id=foundation._finding_id(
                    "weak_entity_chase",
                    first[2],
                    first[3],
                    first[0],
                    last[0],
                    len(streak),
                ),
                finding_type="repeated_weak_guess_approach",
                title="Weak one-sided entity guess consumed a long action streak",
                severity=severity,
                confidence=0.99,
                subsystem="planning/entity perception",
                explanation=(
                    "The same character/interactable guess was selected on consecutive "
                    "decisions even though its recorded topology had only one collision "
                    "approach side and no completed interaction test. One-sided collision "
                    "proves an obstruction, not its semantic identity."
                ),
                recommendation=(
                    "Keep one-sided compact obstructions unresolved, bound the cost of "
                    "approaching them, and require an independent side or response-producing "
                    "interaction before promoting them to normal semantic routing targets."
                ),
                evidence=foundation.EvidenceRange(
                    first[0],
                    last[0],
                    first[1],
                    last[1],
                ),
                room=first[2],
                measured={
                    "guess_id": first[3],
                    "consecutive_selected_steps": len(streak),
                    "entity_approach_directions": 1,
                    "obstruction_target_cells": int(
                        record.get("obstruction_target_cells") or 0
                    ),
                    "completed_tests": int(
                        record.get("completed_tests", record.get("inspections", 0))
                        or 0
                    ),
                    "failed_approaches": int(record.get("failed_approaches") or 0),
                },
                threshold={"consecutive_selected_steps": WEAK_GUESS_STREAK},
                uncertainties=(
                    "The finding does not claim the object was scenery; it identifies "
                    "that the routing commitment was disproportionate to the evidence.",
                ),
            )
        )
    return findings


def _room_link_pingpong_findings(
    run: foundation.NormalizedRun,
) -> list[foundation.RunDoctorFinding]:
    crossings: list[tuple[int, float | None, str, str]] = []
    previous_room: str | None = None
    for fallback, event in enumerate(run.events):
        if str(event.get("state") or "") != "overworld":
            continue
        room = foundation._room(event)
        if room is None:
            continue
        if previous_room is not None and room != previous_room:
            crossings.append(
                (
                    foundation._step(event, fallback),
                    foundation._elapsed(event),
                    previous_room,
                    room,
                )
            )
        previous_room = room

    by_link: dict[frozenset[str], list[tuple[int, float | None, str, str]]] = {}
    for crossing in crossings:
        link = frozenset((crossing[2], crossing[3]))
        if len(link) == 2:
            by_link.setdefault(link, []).append(crossing)

    findings: list[foundation.RunDoctorFinding] = []
    for link, rows in sorted(by_link.items(), key=lambda item: sorted(item[0])):
        best: list[tuple[int, float | None, str, str]] = []
        left = 0
        for right, row in enumerate(rows):
            while row[0] - rows[left][0] > PINGPONG_WINDOW_STEPS:
                left += 1
            window = rows[left : right + 1]
            if len(window) > len(best):
                best = window
        if len(best) < PINGPONG_CROSSINGS:
            continue
        start, end = best[0], best[-1]
        severity = "high" if len(best) >= 6 else "medium"
        rooms = sorted(link)
        findings.append(
            foundation.RunDoctorFinding(
                finding_id=foundation._finding_id(
                    "room_link_pingpong",
                    *rooms,
                    start[0],
                    end[0],
                    len(best),
                ),
                finding_type="repeated_room_link_pingpong",
                title="Same room link was crossed repeatedly in a short window",
                severity=severity,
                confidence=0.98,
                subsystem="navigation/portal safety",
                explanation=(
                    "The run repeatedly crossed the same observed two-room link in both "
                    "directions within a bounded decision window. This is broader than a "
                    "single A-B-A rapid return and can indicate repeated re-entry into the "
                    "same transition aperture."
                ),
                recommendation=(
                    "Use a temporary behavior-only cooldown for repeatedly crossed links "
                    "while preserving the learned portal and allowing later reconsideration."
                ),
                evidence=foundation.EvidenceRange(
                    start[0],
                    end[0],
                    start[1],
                    end[1],
                ),
                room=None,
                measured={
                    "rooms": rooms,
                    "crossings_in_window": len(best),
                    "window_steps": end[0] - start[0],
                    "directions": [f"{row[2]}->{row[3]}" for row in best],
                },
                threshold={
                    "crossings": PINGPONG_CROSSINGS,
                    "window_steps": PINGPONG_WINDOW_STEPS,
                },
                uncertainties=(
                    "Repeated crossings can be legitimate exploration; this finding "
                    "identifies costly oscillation risk and does not assign story meaning "
                    "to either direction.",
                ),
            )
        )
    return findings


def _exit_semantic_leak_findings(
    run: foundation.NormalizedRun,
) -> list[foundation.RunDoctorFinding]:
    latest: dict[tuple[str, tuple[int, ...]], tuple[int, Mapping[str, Any]]] = {}
    for step, update in v102._screen_region_updates(run):
        room = str(update.get("room") or "")
        raw_region = update.get("region")
        region = (
            tuple(int(value) for value in raw_region)
            if isinstance(raw_region, (list, tuple))
            else ()
        )
        latest[(room, region)] = (step, update)

    leaked = []
    for (room, region), (step, update) in latest.items():
        state = str(update.get("exit_candidate_state") or "")
        if (
            state in UNRESOLVED_EXIT_STATES
            and str(update.get("hypothesis") or "") == "possible_exit"
        ):
            leaked.append((room, region, step, state, update))
    if not leaked:
        return []

    steps = [row[2] for row in leaked]
    return [
        foundation.RunDoctorFinding(
            finding_id=foundation._finding_id(
                "exit_semantic_leak",
                min(steps),
                max(steps),
                len(leaked),
            ),
            finding_type="unresolved_exit_semantic_leak",
            title="Unresolved exit candidates leaked into semantic routing memory",
            severity="high",
            confidence=1.0,
            subsystem="exit perception/memory lifecycle",
            explanation=(
                "The latest screen-region lifecycle records label one or more candidates "
                "as possible_exit while Exit Detection v2 simultaneously records them as "
                "geometry-only, visually unresolved, approach-unresolved, or contradicted. "
                "Those states are internally inconsistent regardless of the true game route."
            ),
            recommendation=(
                "Enforce the Exit Detection v2 semantic gate after every legacy metadata "
                "refresh and when loading old memory; preserve the candidate evidence but "
                "clear possible_exit until semantic_ready or confirmed."
            ),
            evidence=foundation.EvidenceRange(min(steps), max(steps)),
            room=None,
            measured={
                "leaked_candidate_count": len(leaked),
                "states": {
                    state: sum(row[3] == state for row in leaked)
                    for state in sorted({row[3] for row in leaked})
                },
                "examples": [
                    {
                        "room": row[0],
                        "region": list(row[1]),
                        "step": row[2],
                        "state": row[3],
                    }
                    for row in leaked[:8]
                ],
            },
            threshold={"allowed_unresolved_semantic_leaks": 0},
        )
    ]


def _telemetry_position(event: Mapping[str, Any]) -> tuple[float, float] | None:
    telemetry = event.get("telemetry")
    if not isinstance(telemetry, Mapping):
        return None
    if str(telemetry.get("mode") or "") != "overworld":
        return None
    x = telemetry.get("player_foot_x")
    y = telemetry.get("player_foot_y")
    if x is None or y is None:
        x = telemetry.get("player_x", telemetry.get("x"))
        y = telemetry.get("player_y", telemetry.get("y"))
    try:
        return float(x), float(y)
    except (TypeError, ValueError, OverflowError):
        return None


def _moving_invalid_capture_findings(
    run: foundation.NormalizedRun,
) -> list[foundation.RunDoctorFinding]:
    streaks: list[list[tuple[int, Mapping[str, Any]]]] = []
    current: list[tuple[int, Mapping[str, Any]]] = []
    for fallback, event in enumerate(run.events):
        step = foundation._step(event, fallback)
        if event.get("visual_valid") is False:
            if current and step != current[-1][0] + 1:
                streaks.append(current)
                current = []
            current.append((step, event))
        elif current:
            streaks.append(current)
            current = []
    if current:
        streaks.append(current)

    findings: list[foundation.RunDoctorFinding] = []
    for streak in streaks:
        if len(streak) < MOVING_INVALID_STREAK:
            continue
        positions = [
            position
            for _step, event in streak
            if (position := _telemetry_position(event)) is not None
        ]
        if len(positions) < 2:
            continue
        unique = {
            (round(position[0], 1), round(position[1], 1))
            for position in positions
        }
        spread = max(
            max(position[0] for position in positions)
            - min(position[0] for position in positions),
            max(position[1] for position in positions)
            - min(position[1] for position in positions),
        )
        if len(unique) < 5 or spread <= 8.0:
            continue
        first_step, first_event = streak[0]
        last_step, last_event = streak[-1]
        severity = "high" if len(streak) >= 60 else "medium"
        findings.append(
            foundation.RunDoctorFinding(
                finding_id=foundation._finding_id(
                    "moving_invalid_capture",
                    first_step,
                    last_step,
                    len(streak),
                    len(unique),
                ),
                finding_type="capture_stale_while_player_moves",
                title="Visual capture stayed invalid while telemetry showed movement",
                severity=severity,
                confidence=0.99,
                subsystem="observer/capture",
                explanation=(
                    "Visual validity remained false for a long consecutive interval while "
                    "overworld telemetry reported many distinct player positions. This "
                    "separates a capture/freshness failure from a legitimately static scene."
                ),
                recommendation=(
                    "Probe an independent Windows capture backend after repeated identical "
                    "usable frames and record whether the alternate backend recovers a fresh "
                    "bitmap."
                ),
                evidence=foundation.EvidenceRange(
                    first_step,
                    last_step,
                    foundation._elapsed(first_event),
                    foundation._elapsed(last_event),
                ),
                room=foundation._room(first_event),
                measured={
                    "invalid_steps": len(streak),
                    "distinct_player_positions": len(unique),
                    "position_spread_pixels": round(spread, 2),
                },
                threshold={
                    "invalid_steps": MOVING_INVALID_STREAK,
                    "distinct_player_positions": 5,
                    "position_spread_pixels": 8.0,
                },
            )
        )
    return findings


def calibrate_base_report(
    run: foundation.NormalizedRun,
    raw_base: foundation.RunDoctorReport,
) -> foundation.RunDoctorReport:
    base = v102.calibrate_base_report(run, raw_base)
    findings = list(base.findings)
    findings.extend(_weak_guess_chase_findings(run))
    findings.extend(_room_link_pingpong_findings(run))
    findings.extend(_exit_semantic_leak_findings(run))
    findings.extend(_moving_invalid_capture_findings(run))

    unique: dict[str, foundation.RunDoctorFinding] = {}
    for finding in findings:
        unique[finding.finding_id] = finding
    return v102._rebuild_report(base, unique.values())


def group_findings(
    findings: Iterable[foundation.RunDoctorFinding],
) -> tuple[incident_engine.DoctorIncident, ...]:
    return v102.group_findings(findings)


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
