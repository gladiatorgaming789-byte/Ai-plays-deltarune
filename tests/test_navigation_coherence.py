from __future__ import annotations

from pathlib import Path

import deltarune_agent.autonomy_v1_runtime as runtime_module
from deltarune_agent.autonomy_v1 import AutonomyOption, RecoveryLevel
from deltarune_agent.hierarchical_policy import HierarchicalPolicy
from deltarune_agent.navigation_coherence import (
    COHERENCE_BROAD_RESET_COOLDOWN,
    COHERENCE_ROUTE_STALL_TICKS,
    NavigationCoherenceExplorer,
)


def _explorer(tmp_path: Path) -> NavigationCoherenceExplorer:
    return NavigationCoherenceExplorer(
        seed=3,
        memory_path=tmp_path / "coherence-memory.json",
    )


def _option(
    option_id: str = "route:test",
    *,
    kind: str = "learned_warp",
    target: tuple[int, int] = (3, 0),
) -> AutonomyOption:
    return AutonomyOption(
        option_id=option_id,
        kind=kind,
        required_level=RecoveryLevel.EVIDENCE,
        base_score=8.0,
        distance=target[0],
        metadata={"target_cell": list(target), "target_room": "room_next"},
    )


def test_production_policy_uses_navigation_coherence(tmp_path: Path) -> None:
    policy = HierarchicalPolicy(seed=1, memory_path=tmp_path / "memory.json")

    assert isinstance(policy.explorer, NavigationCoherenceExplorer)


def test_room_cycle_penalty_detects_immediate_return_and_repeated_suffix() -> None:
    immediate = NavigationCoherenceExplorer.room_cycle_penalty(
        ["room_a", "room_b"],
        "room_a",
    )
    repeated = NavigationCoherenceExplorer.room_cycle_penalty(
        ["room_a", "room_b", "room_a", "room_b"],
        "room_a",
    )
    novel = NavigationCoherenceExplorer.room_cycle_penalty(
        ["room_a", "room_b"],
        "room_c",
    )

    assert immediate >= 0.5
    assert repeated > immediate
    assert novel == 0.0


def test_frontier_cells_are_clustered_by_exploration_region(tmp_path: Path) -> None:
    explorer = _explorer(tmp_path)
    explorer.seen_cells.update(
        {
            ("room_test", 0, 0),
            ("room_test", 1, 0),
            ("room_test", 1, 1),
        }
    )

    options = explorer._collect_frontier_options("room_test", (0, 0))

    assert len(options) == 1
    option = options[0]
    assert option.option_id == "frontier_cluster:room_test:0:0"
    assert option.kind == "frontier_cluster"
    assert option.required_level is RecoveryLevel.FRONTIER
    assert option.metadata["frontier_cells"] == 3
    assert option.metadata["unknown_edges"] > 0
    assert option.metadata["expected_gain"] > 0


def test_jittered_portal_samples_become_one_aperture(
    monkeypatch,
    tmp_path: Path,
) -> None:
    explorer = _explorer(tmp_path)
    first = AutonomyOption(
        "warp:a:28:10:right:b",
        "learned_warp",
        RecoveryLevel.EVIDENCE,
        8.0,
        confidence=0.7,
        information_value=0.5,
        distance=4,
        loop_risk=0.1,
        metadata={
            "warp": ("a", 28, 10, "right", "b", 2, 3),
            "role": "unknown",
            "target_room": "b",
            "crossings": 1,
        },
    )
    second = AutonomyOption(
        "warp:a:34:11:right:b",
        "learned_warp",
        RecoveryLevel.EVIDENCE,
        9.0,
        confidence=0.9,
        information_value=0.3,
        distance=2,
        loop_risk=0.2,
        metadata={
            "warp": ("a", 34, 11, "right", "b", 3, 4),
            "role": "progression",
            "target_room": "b",
            "crossings": 2,
        },
    )
    monkeypatch.setattr(
        runtime_module.AutonomyV1RuntimeExplorer.__mro__[1],
        "_collect_warp_options",
        lambda self, room, cell: [first, second],
    )

    options = explorer._collect_warp_options("a", (20, 10))

    assert len(options) == 1
    aperture = options[0]
    assert aperture.option_id.startswith("portal_aperture:a:b:right")
    assert aperture.metadata["aperture_members"] == 2
    assert aperture.metadata["crossings"] == 3
    assert aperture.metadata["source_bounds"] == [28, 10, 34, 11]
    assert aperture.metadata["role"] == "progression"
    assert explorer.last_portal_sample_count == 2
    assert explorer.last_portal_aperture_count == 1


def test_active_goal_contract_reuses_cached_option_without_reranking(
    monkeypatch,
    tmp_path: Path,
) -> None:
    explorer = _explorer(tmp_path)
    explorer.observed_room = "room_test"
    explorer.open_edges.update(
        {
            ("room_test", 0, 0, "right", 1, 0),
            ("room_test", 1, 0, "right", 2, 0),
            ("room_test", 2, 0, "right", 3, 0),
        }
    )
    option = _option()
    explorer._activate_goal(option)
    monkeypatch.setattr(
        explorer,
        "_execute_option",
        lambda selected, room, cell: ("right", 1, "cached route"),
    )
    monkeypatch.setattr(
        explorer,
        "_collect_recovery_options",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("a valid contract must not rerank")
        ),
    )

    explorer.navigation_tick += 1
    plan = explorer._plan_autonomy_recovery("room_test", (0, 0))

    assert plan[:2] == ("right", 2)
    assert explorer.coherence_goal_reuses == 1
    assert explorer.last_autonomy_commitment_hold is True
    assert explorer.active_goal_contract is not None
    assert explorer.active_goal_contract.actions_spent == 2


def test_geodesic_non_progress_breaks_stalled_contract(tmp_path: Path) -> None:
    explorer = _explorer(tmp_path)
    explorer.observed_room = "room_test"
    explorer.open_edges.update(
        {
            ("room_test", 0, 0, "right", 1, 0),
            ("room_test", 1, 0, "right", 2, 0),
            ("room_test", 2, 0, "right", 3, 0),
        }
    )
    explorer._activate_goal(_option())
    assert explorer.active_goal_contract is not None
    explorer.active_goal_contract.last_checked_tick = explorer.navigation_tick
    explorer.navigation_tick += COHERENCE_ROUTE_STALL_TICKS

    valid = explorer._active_contract_is_valid(
        explorer.active_goal_contract,
        "room_test",
        (0, 0),
    )

    assert valid is False
    assert explorer.active_goal_contract is None
    assert explorer.coherence_route_stalls == 1
    assert "stalled" in explorer.last_coherence_replan_reason


def test_observed_room_change_completes_a_portal_contract(tmp_path: Path) -> None:
    explorer = _explorer(tmp_path)
    explorer.observed_room = "room_test"
    explorer._activate_goal(_option())

    explorer._clear_autonomy_goal("room changed")

    assert explorer.coherence_goal_completions == 1
    assert explorer.coherence_goal_interruptions == 0


def test_ordinary_map_growth_does_not_collapse_recovery_level(
    monkeypatch,
    tmp_path: Path,
) -> None:
    explorer = _explorer(tmp_path)
    explorer.recovery_level = RecoveryLevel.BOUNDED_TEST
    explorer.recovery_level_started_at = 0
    explorer.navigation_tick = 20
    explorer._last_autonomy_evidence_marker = explorer._autonomy_evidence_marker()
    monkeypatch.setattr(
        explorer,
        "_desired_recovery_level",
        lambda room, cell: RecoveryLevel.EVIDENCE,
    )
    explorer.seen_cells.add(("room_test", 1, 1))

    level = explorer._update_recovery_state("room_test", (1, 1))

    assert level is RecoveryLevel.BOUNDED_TEST
    assert explorer.coherence_hysteresis_holds == 1


def test_broad_reset_cooldown_is_released_by_material_evidence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    explorer = _explorer(tmp_path)
    broad = AutonomyOption(
        "broad_reset:room:0:0",
        "broad_reset",
        RecoveryLevel.BROAD_RESET,
        2.0,
    )
    monkeypatch.setattr(
        runtime_module.AutonomyV1RuntimeExplorer,
        "_collect_recovery_options",
        lambda self, room, cell, level: [broad],
    )
    explorer.navigation_tick = 100
    explorer._broad_reset_cooldown_until = (
        explorer.navigation_tick + COHERENCE_BROAD_RESET_COOLDOWN
    )
    explorer._broad_reset_material_marker = explorer._coherence_material_marker()

    assert explorer._collect_recovery_options(
        "room",
        (0, 0),
        RecoveryLevel.BROAD_RESET,
    ) == []

    explorer.interactables[("room", 1, 1)] = {"progressions": 0}
    assert explorer._collect_recovery_options(
        "room",
        (0, 0),
        RecoveryLevel.BROAD_RESET,
    ) == [broad]


def test_snapshot_exports_contract_route_and_cycle_diagnostics(tmp_path: Path) -> None:
    explorer = _explorer(tmp_path)
    explorer.observed_room = "room_test"
    explorer._activate_goal(_option(target=(2, 0)))
    explorer.recent_rooms.extend(("room_old", "room_test"))

    coherence = explorer.autonomy_snapshot()["coherence"]

    assert coherence["version"] == 1
    assert coherence["goal_contract"]["target_cell"] == [2, 0]
    assert coherence["goal_contract"]["expected_outcome"]
    assert coherence["recent_rooms"] == ["room_old", "room_test"]
