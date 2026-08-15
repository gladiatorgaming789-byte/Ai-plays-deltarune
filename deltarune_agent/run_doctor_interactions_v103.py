"""Spatial interaction-retry calibration for Trusted Run Doctor v1.0.3."""

from __future__ import annotations

from typing import Any, Mapping

from . import run_doctor as foundation
from . import run_doctor_calibration_v102 as v102
from . import run_doctor_incidents as incident_engine


_VECTORS = {
    "left": (-1, 0),
    "right": (1, 0),
    "up": (0, -1),
    "down": (0, 1),
}


def _no_response_probes(
    run: foundation.NormalizedRun,
) -> list[dict[str, Any]]:
    probes: list[dict[str, Any]] = []
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
            cell = update.get("cell")
            direction = str(update.get("direction") or "")
            vector = _VECTORS.get(direction)
            if (
                vector is None
                or not isinstance(cell, (list, tuple))
                or len(cell) != 2
            ):
                continue
            try:
                source = (int(cell[0]), int(cell[1]))
            except (TypeError, ValueError, OverflowError):
                continue
            target = (source[0] + vector[0], source[1] + vector[1])
            probes.append(
                {
                    "room": str(update.get("room") or foundation._room(event) or "unknown"),
                    "source": source,
                    "target": target,
                    "direction": direction,
                    "step": foundation._step(event, fallback),
                    "elapsed": foundation._elapsed(event),
                }
            )
    return probes


def _near(first: tuple[int, int], second: tuple[int, int]) -> bool:
    return max(abs(first[0] - second[0]), abs(first[1] - second[1])) <= 1


def spatial_repeated_interaction_findings(
    run: foundation.NormalizedRun,
    *,
    failure_threshold: int = 2,
) -> list[foundation.RunDoctorFinding]:
    by_room: dict[str, list[dict[str, Any]]] = {}
    for probe in _no_response_probes(run):
        by_room.setdefault(str(probe["room"]), []).append(probe)

    findings: list[foundation.RunDoctorFinding] = []
    for room, probes in sorted(by_room.items()):
        remaining = list(sorted(probes, key=lambda probe: int(probe["step"])))
        while remaining:
            seed = remaining.pop(0)
            cluster = [seed]
            changed = True
            while changed:
                changed = False
                for probe in list(remaining):
                    if any(_near(probe["target"], member["target"]) for member in cluster):
                        cluster.append(probe)
                        remaining.remove(probe)
                        changed = True
            if len(cluster) < failure_threshold:
                continue
            cluster.sort(key=lambda probe: int(probe["step"]))
            first = cluster[0]
            last = cluster[-1]
            findings.append(
                foundation.RunDoctorFinding(
                    finding_id=foundation._finding_id(
                        "spatial_failed_interactions",
                        room,
                        first["target"],
                        first["step"],
                        last["step"],
                        len(cluster),
                    ),
                    finding_type="repeated_failed_interaction",
                    title=f"Repeated no-response tests near the same target in {room}",
                    severity="high" if len(cluster) >= 4 else "medium",
                    confidence=0.99,
                    subsystem="interaction/planning",
                    explanation=(
                        "Recorded no-response interaction probes repeatedly targeted the same "
                        "or adjacent learned target cells. Spatial correlation avoids grouping "
                        "unrelated objects merely because Kris happened to face the same direction."
                    ),
                    recommendation=(
                        "Retire or strongly cool the local target after a concrete no-response "
                        "test unless new collision/response evidence changes its lifecycle."
                    ),
                    evidence=foundation.EvidenceRange(
                        int(first["step"]),
                        int(last["step"]),
                        first["elapsed"],
                        last["elapsed"],
                    ),
                    room=room,
                    measured={
                        "no_response_attempts": len(cluster),
                        "target_cells": [list(probe["target"]) for probe in cluster],
                        "source_cells": [list(probe["source"]) for probe in cluster],
                        "directions": [str(probe["direction"]) for probe in cluster],
                    },
                    threshold={
                        "no_response_attempts_near_same_target": failure_threshold,
                        "target_chebyshev_radius": 1,
                    },
                )
            )
    return findings


def augment_incident_report(
    run: foundation.NormalizedRun,
    report: incident_engine.IncidentDoctorReport,
) -> incident_engine.IncidentDoctorReport:
    # Replace the legacy room+facing grouping entirely. It can combine unrelated
    # objects and produced a confirmed false positive in the eight-run set.
    findings = [
        finding
        for finding in report.base.findings
        if finding.finding_type != "repeated_failed_interaction"
    ]
    findings.extend(spatial_repeated_interaction_findings(run))
    base = v102._rebuild_report(report.base, findings)
    return incident_engine.IncidentDoctorReport(
        base=base,
        incidents=v102.group_findings(base.findings),
        health=incident_engine.health_scores(base.findings),
    )


__all__ = ["augment_incident_report", "spatial_repeated_interaction_findings"]
