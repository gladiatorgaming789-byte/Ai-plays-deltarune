from __future__ import annotations

from pathlib import Path

from deltarune_agent import run_doctor as foundation
from deltarune_agent.run_doctor_menu_v103 import _confirmed_save_menu_settle


def _run(tmp_path: Path, events: list[dict]) -> foundation.NormalizedRun:
    return foundation.NormalizedRun(
        directory=tmp_path,
        manifest={},
        summary={},
        run_report={},
        telemetry_diagnostics={},
        speed_diagnostics={},
        events=events,
        predictions=[],
        navigation_updates=[],
    )


def _finding(end_step: int) -> foundation.RunDoctorFinding:
    return foundation.RunDoctorFinding(
        finding_id="repeat:test",
        finding_type="repeated_action_streak",
        title="Repeated wait",
        severity="medium",
        confidence=1.0,
        subsystem="navigation",
        explanation="test",
        recommendation="test",
        evidence=foundation.EvidenceRange(0, end_step),
        measured={"action": "wait"},
    )


def test_confirmed_save_menu_wait_that_closes_is_expected_settle(tmp_path: Path) -> None:
    events = [
        {
            "step": step,
            "state": "menu",
            "action": "wait",
            "reason": "save point already confirmed; wait for the menu to close",
        }
        for step in range(12)
    ]
    events.append(
        {
            "step": 12,
            "state": "overworld",
            "action": "up",
            "reason": "interaction completed; test up once",
        }
    )

    assert _confirmed_save_menu_settle(_run(tmp_path, events), _finding(11))


def test_menu_that_never_closes_remains_diagnosable(tmp_path: Path) -> None:
    events = [
        {
            "step": step,
            "state": "menu",
            "action": "wait",
            "reason": "save point already confirmed; wait for the menu to close",
        }
        for step in range(20)
    ]

    assert not _confirmed_save_menu_settle(_run(tmp_path, events), _finding(11))
