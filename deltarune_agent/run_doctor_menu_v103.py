"""Menu-settle false-positive calibration for Trusted Run Doctor v1.0.3."""

from __future__ import annotations

from . import run_doctor as foundation
from . import run_doctor_calibration_v102 as v102
from . import run_doctor_incidents as incident_engine


def _confirmed_save_menu_settle(
    run: foundation.NormalizedRun,
    finding: foundation.RunDoctorFinding,
) -> bool:
    if finding.finding_type not in {
        "repeated_action_streak",
        "unproductive_repeated_action_streak",
    }:
        return False
    if str(finding.measured.get("action") or "") != "wait":
        return False
    start = finding.evidence.start_step
    end = finding.evidence.end_step
    if start is None or end is None:
        return False

    interval = [
        event
        for fallback, event in enumerate(run.events)
        if start <= foundation._step(event, fallback) <= end
    ]
    if not interval:
        return False
    menu_ratio = sum(
        str(event.get("state") or "").casefold() == "menu"
        for event in interval
    ) / len(interval)
    settle_ratio = sum(
        "save point already confirmed" in str(event.get("reason") or "").casefold()
        and "wait for the menu to close" in str(event.get("reason") or "").casefold()
        for event in interval
    ) / len(interval)
    if menu_ratio < 0.90 or settle_ratio < 0.90:
        return False

    following = [
        event
        for fallback, event in enumerate(run.events)
        if end < foundation._step(event, fallback) <= end + 5
    ]
    # Suppress only when the recorded run demonstrates that the passive settle
    # sequence actually ended. A menu that remains stuck stays diagnosable.
    return any(
        str(event.get("state") or "").casefold() != "menu"
        for event in following
    )


def augment_incident_report(
    run: foundation.NormalizedRun,
    report: incident_engine.IncidentDoctorReport,
) -> incident_engine.IncidentDoctorReport:
    findings = [
        finding
        for finding in report.base.findings
        if not _confirmed_save_menu_settle(run, finding)
    ]
    if len(findings) == len(report.base.findings):
        return report
    base = v102._rebuild_report(report.base, findings)
    return incident_engine.IncidentDoctorReport(
        base=base,
        incidents=v102.group_findings(base.findings),
        health=incident_engine.health_scores(base.findings),
    )


__all__ = ["augment_incident_report"]
