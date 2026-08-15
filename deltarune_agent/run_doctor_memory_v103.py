"""Final-memory invariant checks for Trusted Run Doctor v1.0.3."""

from __future__ import annotations

import json
from typing import Any, Mapping

from . import run_doctor as foundation
from . import run_doctor_calibration_v102 as v102
from . import run_doctor_incidents as incident_engine


UNRESOLVED_EXIT_STATES = {
    "geometry_candidate",
    "needs_approach_evidence",
    "visual_candidate",
    "contradicted",
}


def _load_navigation_snapshot(run: foundation.NormalizedRun) -> Mapping[str, Any]:
    path = run.directory / "navigation.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, Mapping) else {}


def final_memory_exit_leak_finding(
    run: foundation.NormalizedRun,
) -> foundation.RunDoctorFinding | None:
    navigation = _load_navigation_snapshot(run)
    regions = navigation.get("screen_regions")
    if not isinstance(regions, list):
        return None

    leaked: list[dict[str, Any]] = []
    for item in regions:
        if not isinstance(item, Mapping):
            continue
        state = str(item.get("exit_candidate_state") or "")
        if (
            state not in UNRESOLVED_EXIT_STATES
            or str(item.get("hypothesis") or "") != "possible_exit"
        ):
            continue
        leaked.append(
            {
                "room": str(item.get("room") or "unknown"),
                "region": [
                    foundation._integer(item.get("region_x")) or 0,
                    foundation._integer(item.get("region_y")) or 0,
                ],
                "state": state,
                "path_continuation": bool(item.get("path_continuation")),
                "guess_state": str(item.get("guess_state") or "proposed"),
            }
        )
    if not leaked:
        return None

    states = {
        state: sum(row["state"] == state for row in leaked)
        for state in sorted({row["state"] for row in leaked})
    }
    return foundation.RunDoctorFinding(
        finding_id=foundation._finding_id(
            "final_memory_exit_semantic_leak",
            len(leaked),
            *sorted(states.items()),
        ),
        finding_type="unresolved_exit_semantic_leak",
        title="Persistent navigation memory contains unresolved semantic exits",
        severity="high",
        confidence=1.0,
        subsystem="exit perception/memory lifecycle",
        explanation=(
            "The saved navigation snapshot contains screen regions whose Exit Detection v2 "
            "state is unresolved or contradicted while the legacy routing hypothesis still "
            "says possible_exit. Unlike lifecycle-only analysis, this check also catches bad "
            "labels inherited from earlier runs without being touched in the current run."
        ),
        recommendation=(
            "Re-derive semantic exit exposure when loading navigation memory and after every "
            "legacy path-continuation refresh. Preserve geometry/visual evidence but clear "
            "possible_exit until the candidate is semantic_ready or confirmed."
        ),
        evidence=foundation.EvidenceRange(None, None),
        room=None,
        measured={
            "persistent_leaked_candidate_count": len(leaked),
            "states": states,
            "examples": leaked[:10],
        },
        threshold={"allowed_persistent_unresolved_semantic_leaks": 0},
    )


def augment_incident_report(
    run: foundation.NormalizedRun,
    report: incident_engine.IncidentDoctorReport,
) -> incident_engine.IncidentDoctorReport:
    finding = final_memory_exit_leak_finding(run)
    if finding is None:
        return report

    findings = list(report.base.findings)
    # Prefer the persistent snapshot finding when both lifecycle and final-memory
    # checks describe the same invariant failure. It has broader evidence.
    findings = [
        existing
        for existing in findings
        if existing.finding_type != "unresolved_exit_semantic_leak"
    ]
    findings.append(finding)
    base = v102._rebuild_report(report.base, findings)
    return incident_engine.IncidentDoctorReport(
        base=base,
        incidents=v102.group_findings(base.findings),
        health=incident_engine.health_scores(base.findings),
    )


__all__ = ["augment_incident_report", "final_memory_exit_leak_finding"]
