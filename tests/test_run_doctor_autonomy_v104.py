from __future__ import annotations

from pathlib import Path

from deltarune_agent import run_doctor as foundation
from deltarune_agent.run_doctor_autonomy_v104 import autonomy_findings


def _run(predictions: list[dict]) -> foundation.NormalizedRun:
    return foundation.NormalizedRun(
        directory=Path("synthetic"),
        manifest={},
        summary={},
        run_report={},
        telemetry_diagnostics={},
        speed_diagnostics={},
        events=[],
        predictions=predictions,
        navigation_updates=[],
    )


def _prediction(step: int, autonomy: dict, room: str = "room") -> dict:
    return {
        "step": step,
        "elapsed_seconds": step / 10,
        "prediction_snapshot": {
            "room": room,
            "autonomy": autonomy,
        },
    }


def _autonomy(
    *,
    goal: str | None = None,
    level: int = 2,
    epoch: int = 0,
    selected: str | None = None,
    options: list[dict] | None = None,
    commitment: bool = False,
) -> dict:
    return {
        "active_goal_id": goal,
        "recovery_level_value": level,
        "story_epoch": epoch,
        "selected_option_id": selected,
        "commitment_hold": commitment,
        "ranked_options": list(options or []),
    }


def _option(option_id: str, score: float, *, spent: int = 0, limit: int = 0) -> dict:
    return {
        "id": option_id,
        "kind": "semantic_entity",
        "score": score,
        "budget_spent": spent,
        "budget_limit": limit,
    }


def test_budget_overrun_is_an_internal_invariant_finding() -> None:
    predictions = [
        _prediction(
            10,
            _autonomy(
                selected="weak",
                options=[_option("weak", 5.0, spent=3, limit=2)],
            ),
        )
    ]

    findings = autonomy_findings(_run(predictions))

    assert any(
        finding.finding_type == "autonomy_uncertainty_budget_overrun"
        for finding in findings
    )


def test_goal_switch_thrashing_requires_repeated_same_epoch_switches() -> None:
    predictions = []
    for step in range(12):
        predictions.append(
            _prediction(
                step,
                _autonomy(goal="a" if step % 2 == 0 else "b", epoch=4),
            )
        )

    findings = autonomy_findings(_run(predictions))

    assert any(
        finding.finding_type == "autonomy_goal_thrashing"
        for finding in findings
    )


def test_story_epoch_change_breaks_goal_thrash_correlation() -> None:
    predictions = []
    for step in range(12):
        predictions.append(
            _prediction(
                step,
                _autonomy(
                    goal="a" if step % 2 == 0 else "b",
                    epoch=step,
                ),
            )
        )

    findings = autonomy_findings(_run(predictions))

    assert not any(
        finding.finding_type == "autonomy_goal_thrashing"
        for finding in findings
    )


def test_monotonic_recovery_escalation_is_not_called_thrashing() -> None:
    predictions = [
        _prediction(step, _autonomy(level=min(6, step), epoch=0))
        for step in range(8)
    ]

    findings = autonomy_findings(_run(predictions))

    assert not any(
        finding.finding_type == "autonomy_recovery_level_thrashing"
        for finding in findings
    )


def test_bidirectional_recovery_level_changes_are_detected() -> None:
    levels = [2, 3, 2, 3, 2, 3, 2, 3]
    predictions = [
        _prediction(step, _autonomy(level=level, epoch=1))
        for step, level in enumerate(levels)
    ]

    findings = autonomy_findings(_run(predictions))

    assert any(
        finding.finding_type == "autonomy_recovery_level_thrashing"
        for finding in findings
    )


def test_commitment_hold_explains_lower_scored_selection() -> None:
    predictions = [
        _prediction(
            step,
            _autonomy(
                selected="active",
                commitment=True,
                options=[
                    _option("best", 10.0),
                    _option("active", 5.0),
                ],
            ),
        )
        for step in range(8)
    ]

    findings = autonomy_findings(_run(predictions))

    assert not any(
        finding.finding_type == "autonomy_stronger_ranked_option_ignored"
        for finding in findings
    )


def test_unexplained_lower_scored_selection_is_detected() -> None:
    predictions = [
        _prediction(
            step,
            _autonomy(
                selected="lower",
                commitment=False,
                options=[
                    _option("best", 10.0),
                    _option("lower", 5.0),
                ],
            ),
        )
        for step in range(6)
    ]

    findings = autonomy_findings(_run(predictions))

    assert any(
        finding.finding_type == "autonomy_stronger_ranked_option_ignored"
        for finding in findings
    )


def test_long_high_recovery_interval_is_efficiency_finding() -> None:
    predictions = [
        _prediction(step, _autonomy(level=5, epoch=2))
        for step in range(125)
    ]

    findings = autonomy_findings(_run(predictions))

    assert any(
        finding.finding_type == "autonomy_high_recovery_stall"
        for finding in findings
    )
