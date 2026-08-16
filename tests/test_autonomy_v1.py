from __future__ import annotations

import deltarune_agent.autonomy_v1 as autonomy_module
import deltarune_agent.hierarchical_policy as hierarchical_module
from deltarune_agent.autonomy_v1 import (
    AUTONOMY_GOAL_BREAK_MARGIN,
    AutonomyOption,
    AutonomyV1Explorer,
    RecoveryLevel,
)
from deltarune_agent.autonomy_v1_runtime import AutonomyV1RuntimeExplorer


def _budget_stub() -> AutonomyV1Explorer:
    explorer = AutonomyV1Explorer.__new__(AutonomyV1Explorer)
    explorer.navigation_tick = 10
    explorer.story_epoch = 2
    explorer.uncertainty_budgets = {}
    explorer.uncertainty_budget_actions = 0
    explorer.uncertainty_budget_exhaustions = 0
    explorer.uncertainty_budget_evidence_resets = 0
    explorer.map_updates = []
    return explorer


def test_option_snapshot_exports_base_score_for_shadow_replay() -> None:
    explorer = AutonomyV1Explorer.__new__(AutonomyV1Explorer)
    option = AutonomyOption(
        option_id="evidence:E1",
        kind="semantic_entity",
        required_level=RecoveryLevel.EVIDENCE,
        base_score=4.75,
        score=7.25,
    )

    payload = explorer._option_payload(option, selected=True)

    assert payload["base_score"] == 4.75
    assert payload["score"] == 7.25
    assert payload["selected"] is True


def test_recovery_frontier_caps_expensive_escalation(monkeypatch) -> None:
    explorer = AutonomyV1Explorer.__new__(AutonomyV1Explorer)
    explorer.story_stall_steps = 500
    monkeypatch.setattr(explorer, "_progress_pressure", lambda room, cell: True)
    monkeypatch.setattr(
        explorer,
        "_has_reachable_autonomy_frontier",
        lambda room, cell: True,
    )

    assert explorer._desired_recovery_level("room", (1, 1)) is RecoveryLevel.FRONTIER


def test_recovery_thresholds_escalate_without_route_knowledge(monkeypatch) -> None:
    explorer = AutonomyV1Explorer.__new__(AutonomyV1Explorer)
    monkeypatch.setattr(explorer, "_progress_pressure", lambda room, cell: True)
    monkeypatch.setattr(
        explorer,
        "_has_reachable_autonomy_frontier",
        lambda room, cell: False,
    )
    monkeypatch.setattr(
        autonomy_module,
        "_room_completion_pressure",
        lambda explorer, room: False,
    )

    explorer.story_stall_steps = 50
    assert explorer._desired_recovery_level("room", (0, 0)) is RecoveryLevel.EVIDENCE
    explorer.story_stall_steps = 80
    assert explorer._desired_recovery_level("room", (0, 0)) is RecoveryLevel.BOUNDED_TEST
    explorer.story_stall_steps = 120
    assert explorer._desired_recovery_level("room", (0, 0)) is RecoveryLevel.LEARNED_ROUTE
    explorer.story_stall_steps = 200
    assert explorer._desired_recovery_level("room", (0, 0)) is RecoveryLevel.CONTROLLED_BACKTRACK
    explorer.story_stall_steps = 300
    assert explorer._desired_recovery_level("room", (0, 0)) is RecoveryLevel.BROAD_RESET


def test_visual_budget_fingerprint_ignores_own_failed_attempt_counters() -> None:
    explorer = _budget_stub()
    record = {
        "guess_semantic_state": "unknown_but_interesting",
        "entity_candidate_state": "single_side_stable",
        "independent_views": 1,
        "multi_view_sample_count": 1,
        "multi_view_consistency": 0.5,
        "guess_beliefs": {
            "possible_exit": 0.2,
            "possible_character": 0.2,
            "possible_interactable": 0.4,
            "scenery": 0.2,
        },
        "failed_approaches": 0,
        "completed_tests": 0,
        "approach_attempts": 0,
    }
    before = explorer._visual_evidence_fingerprint(record)
    record["failed_approaches"] = 7
    record["completed_tests"] = 5
    record["approach_attempts"] = 20
    after = explorer._visual_evidence_fingerprint(record)

    assert after == before


def test_visual_budget_fingerprint_changes_on_new_independent_view() -> None:
    explorer = _budget_stub()
    record = {
        "guess_semantic_state": "unknown_but_interesting",
        "independent_views": 1,
        "multi_view_sample_count": 1,
        "multi_view_consistency": 0.5,
    }
    before = explorer._visual_evidence_fingerprint(record)
    record["independent_views"] = 2
    record["multi_view_sample_count"] = 2
    record["multi_view_consistency"] = 0.8
    after = explorer._visual_evidence_fingerprint(record)

    assert after != before


def test_exhausted_uncertainty_budget_makes_option_ineligible() -> None:
    explorer = _budget_stub()
    option = AutonomyOption(
        option_id="weak:test",
        kind="weak_entity_test",
        required_level=RecoveryLevel.BOUNDED_TEST,
        base_score=5.0,
        budget_key="weak:test",
        budget_limit=2,
        fingerprint=(2, "same evidence"),
    )

    assert explorer._score_option(option) != float("-inf")
    explorer._consume_budget(option)
    explorer._consume_budget(option)
    assert explorer._score_option(option) == float("-inf")
    assert explorer.uncertainty_budget_exhaustions == 1


def _commitment_stub() -> AutonomyV1Explorer:
    explorer = AutonomyV1Explorer.__new__(AutonomyV1Explorer)
    explorer.navigation_tick = 20
    explorer.active_autonomy_goal_id = "active"
    explorer.active_autonomy_goal_started_at = 18
    explorer.autonomy_goal_commitment_holds = 0
    explorer.autonomy_goal_breaks_for_stronger_evidence = 0
    explorer.last_autonomy_commitment_hold = False
    return explorer


def test_goal_commitment_holds_when_new_option_is_only_slightly_better() -> None:
    explorer = _commitment_stub()
    active = AutonomyOption(
        "active", "semantic_entity", RecoveryLevel.EVIDENCE, 0.0, score=8.0
    )
    alternative = AutonomyOption(
        "new", "learned_warp", RecoveryLevel.LEARNED_ROUTE, 0.0, score=8.6
    )

    chosen = explorer._choose_committed_option([alternative, active])

    assert chosen.option_id == "active"
    assert explorer.autonomy_goal_commitment_holds == 1
    assert explorer.last_autonomy_commitment_hold is True


def test_goal_commitment_breaks_for_materially_stronger_option() -> None:
    explorer = _commitment_stub()
    active = AutonomyOption(
        "active", "semantic_entity", RecoveryLevel.EVIDENCE, 0.0, score=7.0
    )
    alternative = AutonomyOption(
        "new", "learned_warp", RecoveryLevel.LEARNED_ROUTE, 0.0,
        score=7.0 + AUTONOMY_GOAL_BREAK_MARGIN + 0.5,
    )

    chosen = explorer._choose_committed_option([alternative, active])

    assert chosen.option_id == "new"
    assert explorer.autonomy_goal_breaks_for_stronger_evidence == 1


def test_loop_risk_is_a_planning_cost_not_a_portal_role_mutation() -> None:
    explorer = AutonomyV1Explorer.__new__(AutonomyV1Explorer)
    portal = {
        "role": "progression",
        "return_tendency": 0.8,
        "loop_risk": 0.2,
    }

    class _World:
        def portal_metadata(self, warp):
            return portal

    explorer.world = _World()
    explorer.recent_rooms = ["room_b", "room_b"]
    explorer.room_entry_from = {"room_a": "room_b"}
    explorer.suppressed_room_links = set()
    warp = ("room_a", 1, 1, "right", "room_b", 2, 2)

    risk = explorer._warp_loop_risk(warp)

    assert risk > 0.0
    assert portal["role"] == "progression"


def test_long_horizon_planner_uses_only_observed_warp_graph(monkeypatch) -> None:
    explorer = AutonomyV1Explorer.__new__(AutonomyV1Explorer)
    warp_ab = ("a", 1, 1, "right", "b", 2, 2)
    warp_bc = ("b", 3, 3, "down", "c", 4, 4)
    monkeypatch.setattr(explorer, "_reliable_warps", lambda: iter(((warp_ab, 2), (warp_bc, 1))))
    monkeypatch.setattr(explorer, "_run21_link_hold_active", lambda room, target: False)
    monkeypatch.setattr(explorer, "_link_is_cooling_down", lambda room, target: False)
    monkeypatch.setattr(
        explorer,
        "_warp_route",
        lambda room, cell, warp, allow_backtrack: ("right", 2),
    )
    monkeypatch.setattr(explorer, "_room_opportunity_score", lambda room: 5.0 if room == "c" else 0.0)
    monkeypatch.setattr(explorer, "_warp_loop_risk", lambda warp: 0.1)
    explorer.room_entry_from = {}
    explorer.suppressed_room_links = set()

    options = explorer._collect_long_horizon_options("a", (0, 0))

    assert len(options) == 1
    assert options[0].metadata["target_room"] == "c"
    assert options[0].metadata["path_rooms"] == ["a", "b", "c"]
    assert options[0].metadata["path_hops"] == 2


def test_broad_reset_is_reserved_for_final_recovery_tier() -> None:
    explorer = AutonomyV1Explorer.__new__(AutonomyV1Explorer)
    option = explorer._collect_broad_reset_option("room", (4, 5))
    assert option.required_level is RecoveryLevel.BROAD_RESET


def test_runtime_expired_cooldown_can_be_considered_again() -> None:
    explorer = AutonomyV1RuntimeExplorer.__new__(AutonomyV1RuntimeExplorer)
    assert explorer._active_visual_record({"guess_state": "cooldown"})
    assert not explorer._active_visual_record({"guess_state": "rejected"})


def test_hierarchical_policy_constructs_autonomy_runtime_explorer(monkeypatch) -> None:
    sentinel = object()
    monkeypatch.setattr(
        hierarchical_module,
        "AutonomyV1RuntimeExplorer",
        lambda seed, memory_path: sentinel,
    )

    policy = hierarchical_module.HierarchicalPolicy(seed=3, memory_path=None)

    assert policy.explorer is sentinel
