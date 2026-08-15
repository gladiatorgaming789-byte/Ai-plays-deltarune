"""Autonomy v1 diagnostics for Trusted Run Doctor v1.0.4.

All findings are derived from recorded Autonomy snapshots. The detector is
read-only and never decides which DELTARUNE route is correct.
"""

from __future__ import annotations

from typing import Any, Mapping

from . import run_doctor as foundation
from . import run_doctor_calibration_v102 as v102
from . import run_doctor_incidents as incident_engine


RUN_DOCTOR_VERSION = "1.0.4"
GOAL_SWITCH_WINDOW = 80
GOAL_SWITCH_THRESHOLD = 8
RECOVERY_THRASH_WINDOW = 80
RECOVERY_THRASH_CHANGES = 6
HIGH_RECOVERY_STREAK = 120
IGNORED_SCORE_GAP = 3.0
IGNORED_STRONGER_STREAK = 5
BROAD_RESET_STREAK = 8


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return default if result != result else result


def _autonomy_rows(
    run: foundation.NormalizedRun,
) -> list[tuple[int, float | None, str | None, Mapping[str, Any]]]:
    rows = []
    for fallback, prediction in enumerate(run.predictions):
        snapshot = prediction.get("prediction_snapshot")
        if not isinstance(snapshot, Mapping):
            continue
        autonomy = snapshot.get("autonomy")
        if not isinstance(autonomy, Mapping):
            continue
        room_value = snapshot.get("room")
        room = str(room_value) if room_value is not None else None
        rows.append(
            (
                foundation._step(prediction, fallback),
                foundation._elapsed(prediction),
                room,
                autonomy,
            )
        )
    rows.sort(key=lambda row: row[0])
    return rows


def _goal_thrash_findings(
    run: foundation.NormalizedRun,
) -> list[foundation.RunDoctorFinding]:
    rows = _autonomy_rows(run)
    switches: list[tuple[int, float | None, str | None, int, str, str]] = []
    previous: tuple[str | None, int, str | None] | None = None
    for step, elapsed, room, autonomy in rows:
        epoch = _safe_int(autonomy.get("story_epoch"))
        goal = str(autonomy.get("active_goal_id") or "") or None
        if previous is not None:
            previous_room, previous_epoch, previous_goal = previous
            if (
                room == previous_room
                and epoch == previous_epoch
                and goal is not None
                and previous_goal is not None
                and goal != previous_goal
            ):
                switches.append(
                    (step, elapsed, room, epoch, previous_goal, goal)
                )
        previous = (room, epoch, goal)

    findings = []
    left = 0
    for right, current in enumerate(switches):
        while (
            left <= right
            and (
                current[3] != switches[left][3]
                or current[2] != switches[left][2]
                or current[0] - switches[left][0] > GOAL_SWITCH_WINDOW
            )
        ):
            left += 1
        window = switches[left : right + 1]
        if len(window) < GOAL_SWITCH_THRESHOLD:
            continue
        first, last = window[0], window[-1]
        findings.append(
            foundation.RunDoctorFinding(
                finding_id=foundation._finding_id(
                    "autonomy_goal_thrash",
                    first[2],
                    first[3],
                    first[0],
                    last[0],
                    len(window),
                ),
                finding_type="autonomy_goal_thrashing",
                title="Autonomy switched recovery goals repeatedly",
                severity="high" if len(window) >= 12 else "medium",
                confidence=0.99,
                subsystem="autonomy/planning",
                explanation=(
                    "Recorded Autonomy snapshots switched between concrete recovery "
                    "goals many times in one room and story epoch. Goal commitment is "
                    "supposed to absorb small score fluctuations, so this pattern is "
                    "evidence of planner thrash rather than proof that any route was wrong."
                ),
                recommendation=(
                    "Inspect candidate score gaps and evidence changes. Increase commitment "
                    "only if switches were caused by small score noise; preserve immediate "
                    "breaks for invalid targets or materially stronger evidence."
                ),
                evidence=foundation.EvidenceRange(
                    first[0], last[0], first[1], last[1]
                ),
                room=first[2],
                measured={
                    "goal_switches": len(window),
                    "window_steps": last[0] - first[0],
                    "story_epoch": first[3],
                },
                threshold={
                    "goal_switches": GOAL_SWITCH_THRESHOLD,
                    "window_steps": GOAL_SWITCH_WINDOW,
                },
            )
        )
        # One strongest window per room/epoch is enough for a deterministic report.
        break
    return findings


def _recovery_thrash_findings(
    run: foundation.NormalizedRun,
) -> list[foundation.RunDoctorFinding]:
    rows = _autonomy_rows(run)
    changes: list[tuple[int, float | None, str | None, int, int, int]] = []
    previous: tuple[str | None, int, int] | None = None
    for step, elapsed, room, autonomy in rows:
        epoch = _safe_int(autonomy.get("story_epoch"))
        level = _safe_int(autonomy.get("recovery_level_value"))
        if previous is not None:
            previous_room, previous_epoch, previous_level = previous
            if room == previous_room and epoch == previous_epoch and level != previous_level:
                changes.append((step, elapsed, room, epoch, previous_level, level))
        previous = (room, epoch, level)

    findings = []
    for index, current in enumerate(changes):
        window = [
            row
            for row in changes
            if row[2] == current[2]
            and row[3] == current[3]
            and current[0] <= row[0] <= current[0] + RECOVERY_THRASH_WINDOW
        ]
        if len(window) < RECOVERY_THRASH_CHANGES:
            continue
        up = sum(row[5] > row[4] for row in window)
        down = sum(row[5] < row[4] for row in window)
        if not up or not down:
            # A monotonic escalation through exhausted tiers is expected.
            continue
        first, last = window[0], window[-1]
        findings.append(
            foundation.RunDoctorFinding(
                finding_id=foundation._finding_id(
                    "autonomy_recovery_thrash",
                    first[2],
                    first[3],
                    first[0],
                    last[0],
                    len(window),
                ),
                finding_type="autonomy_recovery_level_thrashing",
                title="Recovery level repeatedly escalated and de-escalated",
                severity="medium",
                confidence=0.98,
                subsystem="autonomy/planning",
                explanation=(
                    "The recovery ladder moved both upward and downward many times "
                    "without a story-epoch change. Monotonic escalation through exhausted "
                    "tiers is normal; repeated bidirectional movement can indicate unstable "
                    "evidence gating."
                ),
                recommendation=(
                    "Verify that de-escalation requires genuinely new map/evidence state and "
                    "that failed attempts cannot manufacture fresh evidence fingerprints."
                ),
                evidence=foundation.EvidenceRange(
                    first[0], last[0], first[1], last[1]
                ),
                room=first[2],
                measured={
                    "level_changes": len(window),
                    "escalations": up,
                    "deescalations": down,
                    "story_epoch": first[3],
                },
                threshold={
                    "level_changes": RECOVERY_THRASH_CHANGES,
                    "window_steps": RECOVERY_THRASH_WINDOW,
                },
            )
        )
        break
    return findings


def _budget_overrun_findings(
    run: foundation.NormalizedRun,
) -> list[foundation.RunDoctorFinding]:
    offenders: list[tuple[int, float | None, str | None, str, int, int]] = []
    for step, elapsed, room, autonomy in _autonomy_rows(run):
        raw = autonomy.get("ranked_options")
        if not isinstance(raw, list):
            continue
        for option in raw:
            if not isinstance(option, Mapping):
                continue
            limit = _safe_int(option.get("budget_limit"))
            spent = _safe_int(option.get("budget_spent"))
            if limit > 0 and spent > limit:
                offenders.append(
                    (
                        step,
                        elapsed,
                        room,
                        str(option.get("id") or "unknown"),
                        spent,
                        limit,
                    )
                )
    if not offenders:
        return []
    first, last = offenders[0], offenders[-1]
    return [
        foundation.RunDoctorFinding(
            finding_id=foundation._finding_id(
                "autonomy_budget_overrun",
                first[0],
                last[0],
                len(offenders),
            ),
            finding_type="autonomy_uncertainty_budget_overrun",
            title="An uncertainty option exceeded its recorded action budget",
            severity="high",
            confidence=1.0,
            subsystem="autonomy/planning",
            explanation=(
                "At least one recorded recovery option reports budget_spent greater "
                "than budget_limit. This is an internal invariant violation; it does "
                "not depend on knowing whether the gameplay target was correct."
            ),
            recommendation=(
                "Treat exhausted budgets as ineligible until a new evidence fingerprint "
                "or story epoch creates a fresh bounded budget."
            ),
            evidence=foundation.EvidenceRange(
                first[0], last[0], first[1], last[1]
            ),
            room=None,
            measured={
                "overrun_rows": len(offenders),
                "examples": [
                    {
                        "step": row[0],
                        "room": row[2],
                        "option_id": row[3],
                        "spent": row[4],
                        "limit": row[5],
                    }
                    for row in offenders[:8]
                ],
            },
            threshold={"allowed_budget_overruns": 0},
        )
    ]


def _ignored_stronger_option_findings(
    run: foundation.NormalizedRun,
) -> list[foundation.RunDoctorFinding]:
    rows: list[tuple[int, float | None, str | None, float, str, str]] = []
    for step, elapsed, room, autonomy in _autonomy_rows(run):
        if bool(autonomy.get("commitment_hold")):
            continue
        selected_id = str(autonomy.get("selected_option_id") or "")
        raw = autonomy.get("ranked_options")
        if not selected_id or not isinstance(raw, list):
            continue
        options = [option for option in raw if isinstance(option, Mapping)]
        if not options:
            continue
        best = max(options, key=lambda option: _safe_float(option.get("score"), -9999.0))
        selected = next(
            (option for option in options if str(option.get("id") or "") == selected_id),
            None,
        )
        if selected is None:
            continue
        gap = _safe_float(best.get("score")) - _safe_float(selected.get("score"))
        if gap < IGNORED_SCORE_GAP:
            continue
        rows.append(
            (
                step,
                elapsed,
                room,
                gap,
                selected_id,
                str(best.get("id") or ""),
            )
        )

    streaks: list[list[tuple[int, float | None, str | None, float, str, str]]] = []
    current = []
    for row in rows:
        if (
            current
            and row[0] == current[-1][0] + 1
            and row[2] == current[-1][2]
            and row[4] == current[-1][4]
            and row[5] == current[-1][5]
        ):
            current.append(row)
        else:
            if current:
                streaks.append(current)
            current = [row]
    if current:
        streaks.append(current)

    findings = []
    for streak in streaks:
        if len(streak) < IGNORED_STRONGER_STREAK:
            continue
        first, last = streak[0], streak[-1]
        findings.append(
            foundation.RunDoctorFinding(
                finding_id=foundation._finding_id(
                    "autonomy_ignored_stronger",
                    first[2],
                    first[0],
                    last[0],
                    first[4],
                    first[5],
                ),
                finding_type="autonomy_stronger_ranked_option_ignored",
                title="Autonomy repeatedly selected a materially lower-scored option",
                severity="medium",
                confidence=0.99,
                subsystem="autonomy/planning",
                explanation=(
                    "For several consecutive decisions the selected option trailed another "
                    "recorded option by a large score margin, and the snapshots did not "
                    "mark a goal-commitment hold. This checks internal ranking consistency, "
                    "not which DELTARUNE route was correct."
                ),
                recommendation=(
                    "Inspect execution availability and selected-goal bookkeeping. If the "
                    "higher-ranked option was invalidated after ranking, record that reason "
                    "explicitly; otherwise correct the selection ordering."
                ),
                evidence=foundation.EvidenceRange(
                    first[0], last[0], first[1], last[1]
                ),
                room=first[2],
                measured={
                    "consecutive_steps": len(streak),
                    "selected_option_id": first[4],
                    "higher_option_id": first[5],
                    "minimum_score_gap": min(row[3] for row in streak),
                },
                threshold={
                    "consecutive_steps": IGNORED_STRONGER_STREAK,
                    "score_gap": IGNORED_SCORE_GAP,
                },
            )
        )
    return findings


def _high_recovery_stall_findings(
    run: foundation.NormalizedRun,
) -> list[foundation.RunDoctorFinding]:
    qualifying = []
    for row in _autonomy_rows(run):
        autonomy = row[3]
        if _safe_int(autonomy.get("recovery_level_value")) >= int(5):
            qualifying.append(row)

    streaks = []
    current = []
    for row in qualifying:
        epoch = _safe_int(row[3].get("story_epoch"))
        if (
            current
            and row[0] == current[-1][0] + 1
            and row[2] == current[-1][2]
            and epoch == _safe_int(current[-1][3].get("story_epoch"))
        ):
            current.append(row)
        else:
            if current:
                streaks.append(current)
            current = [row]
    if current:
        streaks.append(current)

    findings = []
    for streak in streaks:
        if len(streak) < HIGH_RECOVERY_STREAK:
            continue
        first, last = streak[0], streak[-1]
        findings.append(
            foundation.RunDoctorFinding(
                finding_id=foundation._finding_id(
                    "autonomy_high_recovery_stall",
                    first[2],
                    first[0],
                    last[0],
                ),
                finding_type="autonomy_high_recovery_stall",
                title="High-cost recovery persisted for a long same-epoch interval",
                severity="medium",
                confidence=0.95,
                subsystem="autonomy/planning",
                explanation=(
                    "The controller remained at controlled-backtrack or broad-reset level "
                    "for a long interval without a recorded story-epoch change. This is an "
                    "efficiency/stall signal and does not imply any particular route answer."
                ),
                recommendation=(
                    "Review option exhaustion, learned-route reachability, and whether new "
                    "mapping evidence correctly de-escalated the recovery ladder."
                ),
                evidence=foundation.EvidenceRange(
                    first[0], last[0], first[1], last[1]
                ),
                room=first[2],
                measured={
                    "consecutive_high_recovery_steps": len(streak),
                    "story_epoch": _safe_int(first[3].get("story_epoch")),
                },
                threshold={"consecutive_steps": HIGH_RECOVERY_STREAK},
                uncertainties=(
                    "Some difficult sections may legitimately require prolonged recovery; "
                    "the finding measures persistence, not correctness.",
                ),
            )
        )
    return findings


def _broad_reset_streak_findings(
    run: foundation.NormalizedRun,
) -> list[foundation.RunDoctorFinding]:
    rows = []
    for row in _autonomy_rows(run):
        autonomy = row[3]
        raw = autonomy.get("ranked_options")
        selected_id = str(autonomy.get("selected_option_id") or "")
        if not selected_id or not isinstance(raw, list):
            continue
        selected = next(
            (
                option
                for option in raw
                if isinstance(option, Mapping)
                and str(option.get("id") or "") == selected_id
            ),
            None,
        )
        if isinstance(selected, Mapping) and str(selected.get("kind") or "") == "broad_reset":
            rows.append(row)

    streaks = []
    current = []
    for row in rows:
        if current and row[0] == current[-1][0] + 1 and row[2] == current[-1][2]:
            current.append(row)
        else:
            if current:
                streaks.append(current)
            current = [row]
    if current:
        streaks.append(current)

    findings = []
    for streak in streaks:
        if len(streak) < BROAD_RESET_STREAK:
            continue
        first, last = streak[0], streak[-1]
        findings.append(
            foundation.RunDoctorFinding(
                finding_id=foundation._finding_id(
                    "autonomy_broad_reset_streak",
                    first[2],
                    first[0],
                    last[0],
                ),
                finding_type="autonomy_repeated_broad_reset",
                title="Broad-reset fallback repeated for many consecutive decisions",
                severity="medium",
                confidence=0.97,
                subsystem="autonomy/planning",
                explanation=(
                    "Broad reset is the last recovery tier. Repeating it for many decisions "
                    "suggests that all more structured learned options remained exhausted "
                    "or unreachable."
                ),
                recommendation=(
                    "Inspect why new mapping evidence did not appear and whether exhausted "
                    "options are being reopened only when their evidence actually changes."
                ),
                evidence=foundation.EvidenceRange(
                    first[0], last[0], first[1], last[1]
                ),
                room=first[2],
                measured={"consecutive_broad_reset_steps": len(streak)},
                threshold={"consecutive_steps": BROAD_RESET_STREAK},
            )
        )
    return findings


def autonomy_findings(
    run: foundation.NormalizedRun,
) -> list[foundation.RunDoctorFinding]:
    findings = []
    findings.extend(_goal_thrash_findings(run))
    findings.extend(_recovery_thrash_findings(run))
    findings.extend(_budget_overrun_findings(run))
    findings.extend(_ignored_stronger_option_findings(run))
    findings.extend(_high_recovery_stall_findings(run))
    findings.extend(_broad_reset_streak_findings(run))
    return findings


def augment_incident_report(
    run: foundation.NormalizedRun,
    report: incident_engine.IncidentDoctorReport,
) -> incident_engine.IncidentDoctorReport:
    findings = list(report.base.findings)
    findings.extend(autonomy_findings(run))
    base = v102._rebuild_report(report.base, findings)
    return incident_engine.IncidentDoctorReport(
        base=base,
        incidents=v102.group_findings(base.findings),
        health=incident_engine.health_scores(base.findings),
    )


__all__ = [
    "RUN_DOCTOR_VERSION",
    "augment_incident_report",
    "autonomy_findings",
]
