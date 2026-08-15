from __future__ import annotations

import deltarune_agent.autonomy_v1_runtime as runtime_module
from deltarune_agent.autonomy_v1 import AutonomyOption, RecoveryLevel
from deltarune_agent.autonomy_v1_runtime import (
    AUTONOMY_FRONTIER_ESCALATION_GRACE,
    AutonomyV1RuntimeExplorer,
)


def _frontier_level_stub(monkeypatch) -> AutonomyV1RuntimeExplorer:
    explorer = AutonomyV1RuntimeExplorer.__new__(AutonomyV1RuntimeExplorer)
    explorer.navigation_tick = 100
    explorer.story_stall_steps = 120
    explorer._frontier_pressure_since = None
    explorer._frontier_escalated = False
    explorer.frontier_recovery_escalations = 0
    monkeypatch.setattr(explorer, "_progress_pressure", lambda room, cell: True)
    monkeypatch.setattr(
        explorer,
        "_has_reachable_autonomy_frontier",
        lambda room, cell: True,
    )
    monkeypatch.setattr(
        runtime_module,
        "_room_completion_pressure",
        lambda explorer, room: False,
    )
    return explorer


def test_fresh_reachable_frontier_keeps_frontier_priority(monkeypatch) -> None:
    explorer = _frontier_level_stub(monkeypatch)

    assert explorer._desired_recovery_level("room", (0, 0)) is RecoveryLevel.FRONTIER
    assert explorer._frontier_pressure_since == 100
    assert explorer.frontier_recovery_escalations == 0


def test_unchanged_frontier_eventually_allows_recovery_escalation(monkeypatch) -> None:
    explorer = _frontier_level_stub(monkeypatch)
    assert explorer._desired_recovery_level("room", (0, 0)) is RecoveryLevel.FRONTIER

    explorer.navigation_tick += AUTONOMY_FRONTIER_ESCALATION_GRACE
    level = explorer._desired_recovery_level("room", (0, 0))

    assert level is RecoveryLevel.LEARNED_ROUTE
    assert explorer.frontier_recovery_escalations == 1
    # Re-evaluating the same episode must not inflate the diagnostic counter.
    assert explorer._desired_recovery_level("room", (0, 0)) is RecoveryLevel.LEARNED_ROUTE
    assert explorer.frontier_recovery_escalations == 1


def test_new_evidence_can_restart_frontier_grace_period(monkeypatch) -> None:
    explorer = _frontier_level_stub(monkeypatch)
    explorer._frontier_pressure_since = 10
    explorer._frontier_escalated = True

    explorer._reset_frontier_pressure()

    assert explorer._frontier_pressure_since is None
    assert explorer._frontier_escalated is False
    assert explorer._desired_recovery_level("room", (0, 0)) is RecoveryLevel.FRONTIER
    assert explorer._frontier_pressure_since == explorer.navigation_tick


def test_frontier_candidates_join_unified_ranking_after_escalation(monkeypatch) -> None:
    explorer = AutonomyV1RuntimeExplorer.__new__(AutonomyV1RuntimeExplorer)
    explorer.story_epoch = 2
    monkeypatch.setattr(
        explorer,
        "_autonomy_evidence_marker",
        lambda: (2, 10, 9, 1, 1),
    )
    monkeypatch.setattr(explorer, "_loop_avoid_directions", lambda room, cell: set())
    monkeypatch.setattr(
        explorer,
        "_direction_is_unexplored",
        lambda room, cell, direction: direction == "right",
    )
    monkeypatch.setattr(explorer, "_blocked_near", lambda room, cell, direction: False)
    monkeypatch.setattr(
        explorer,
        "_is_entry_warp_direction",
        lambda room, cell, direction: False,
    )
    monkeypatch.setattr(
        explorer,
        "_route_to_nearest_frontier",
        lambda room, cell, allowed_first=None: "right",
    )

    options = explorer._collect_frontier_options("room", (4, 5))

    assert {option.kind for option in options} == {"frontier", "frontier_route"}
    assert all(option.required_level is RecoveryLevel.EVIDENCE for option in options)
    assert all(option.budget_limit > 0 for option in options)
    assert all(option.information_value > 0.0 for option in options)


def test_frontier_execution_is_bounded_recovery_not_blind_probe() -> None:
    explorer = AutonomyV1RuntimeExplorer.__new__(AutonomyV1RuntimeExplorer)
    explorer.frontier_ranked_actions = 0
    option = AutonomyOption(
        option_id="frontier:room:4:5:right",
        kind="frontier",
        required_level=RecoveryLevel.EVIDENCE,
        base_score=10.0,
        metadata={"direction": "right"},
    )

    plan = explorer._execute_option(option, "room", (4, 5))

    assert plan[0] == "right"
    assert plan[1] == 1
    assert "frontier" in plan[2]
    assert explorer.frontier_ranked_actions == 1
