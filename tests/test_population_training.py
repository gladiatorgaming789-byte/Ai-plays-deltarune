from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deltarune_agent.autonomy_v1 import AutonomyOption, RecoveryLevel
from deltarune_agent.population_training import PopulationCoordinator
from deltarune_agent.reinforcement import ReinforcementMemory, RewardSettings
from deltarune_agent.strategy import (
    MAX_POPULATION_SIZE,
    MIN_POPULATION_SIZE,
    StrategyGenome,
    population_genomes,
    validate_population_size,
)
from deltarune_agent.training_workspace import (
    TrainingWorkspace,
    memory_inventory,
    promote_training_run,
)


def _coordinator(tmp_path: Path, *, known_rooms=("room_a",)) -> PopulationCoordinator:
    return PopulationCoordinator(
        session_id="session-test",
        baseline_genome=StrategyGenome.default(),
        baseline_reinforcement=ReinforcementMemory(),
        candidates_directory=tmp_path / "candidates",
        events_path=tmp_path / "population_events.jsonl",
        reward_settings=RewardSettings.for_preset(),
        known_rooms=known_rooms,
    )


def test_default_genome_reproduces_exact_legacy_autonomy_formula() -> None:
    values = {
        "base_score": 8.7,
        "confidence": 0.63,
        "information_value": 0.71,
        "novelty": 0.42,
        "distance": 7,
        "loop_risk": 0.18,
        "failure_cost": 1.25,
        "budget_fraction": 3 / 8,
    }
    legacy = round(
        8.7 + 0.63 * 3.0 + 0.71 * 2.8 + 0.42 * 2.0
        - min(12, 7) * 0.30 - 0.18 * 4.0 - 1.25 * 1.3 - (3 / 8) * 2.2,
        4,
    )
    assert StrategyGenome.default().score(**values) == legacy


def test_population_mutations_are_deterministic_and_clamped() -> None:
    baseline = StrategyGenome(
        confidence=9.0,
        information=9.0,
        novelty=9.0,
        loop_cost=9.0,
    )
    first = population_genomes(baseline)
    second = population_genomes(baseline)
    assert first == second
    values = {candidate_id: genome for candidate_id, _label, genome in first}
    assert values["balanced"] == baseline
    assert values["explorer"].information == 10.0
    assert values["explorer"].novelty == 10.0
    assert values["progress"].confidence == 10.0
    assert values["loop_safe"].loop_cost == 10.0
    assert all(
        0.0 <= float(value) <= 10.0
        for _candidate_id, _label, genome in first
        for key, value in genome.to_dict().items()
        if key != "schema_version"
    )


def test_population_size_is_configurable_deterministic_and_distinct() -> None:
    baseline = StrategyGenome.default()
    for size in (MIN_POPULATION_SIZE, 3, 4, 7, MAX_POPULATION_SIZE):
        first = population_genomes(baseline, size)
        second = population_genomes(baseline, size)
        assert first == second
        assert len(first) == size
        assert len({candidate_id for candidate_id, _label, _genome in first}) == size
        assert all(
            0.0 <= float(value) <= 10.0
            for _candidate_id, _label, genome in first
            for key, value in genome.to_dict().items()
            if key != "schema_version"
        )
    seven = population_genomes(baseline, 7)
    assert [candidate_id for candidate_id, _label, _genome in seven] == [
        "balanced",
        "explorer",
        "progress",
        "loop_safe",
        "explorer_125",
        "progress_125",
        "loop_safe_125",
    ]
    assert len({genome for _candidate_id, _label, genome in seven}) == 7


@pytest.mark.parametrize("value", (MIN_POPULATION_SIZE - 1, MAX_POPULATION_SIZE + 1, "many"))
def test_population_size_rejects_unsupported_values(value: object) -> None:
    with pytest.raises(ValueError, match="population size"):
        validate_population_size(value)


def test_shadow_ranking_is_pure_and_does_not_mutate_policy_state(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    option = AutonomyOption(
        option_id="visual:g1",
        kind="semantic_entity",
        required_level=RecoveryLevel.EVIDENCE,
        base_score=8.7,
        confidence=0.7,
        information_value=0.6,
        novelty=0.5,
        distance=4,
        loop_risk=0.1,
        failure_cost=0.2,
        budget_key="visual:g1",
        budget_limit=6,
        budget_spent=2,
        budget_remaining=4,
    )
    candidate_before = [candidate.reinforcement.summary() for candidate in coordinator.candidates]
    option_before = dict(option.__dict__)
    coordinator.record_legal_options([option])
    assert option.__dict__ == option_before
    assert [candidate.reinforcement.summary() for candidate in coordinator.candidates] == candidate_before
    assert all(not candidate.reinforcement.trace for candidate in coordinator.candidates)
    assert all(candidate.last_shadow_ranking[0]["id"] == "visual:g1" for candidate in coordinator.candidates)


def test_candidate_local_reinforcement_changes_only_that_heads_score(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    option = AutonomyOption(
        option_id="frontier_cluster:room_a:1:1",
        kind="frontier_cluster",
        required_level=RecoveryLevel.FRONTIER,
        base_score=8.0,
    )
    option._population_reinforcement_key = "mode:room_a:frontier_exploration"  # type: ignore[attr-defined]
    coordinator.record_legal_options([option])
    initial = {
        candidate.candidate_id: candidate.last_shadow_ranking[0]["score"]
        for candidate in coordinator.candidates
    }
    progress = next(candidate for candidate in coordinator.candidates if candidate.candidate_id == "progress")
    progress.reinforcement.begin_action(
        "mode:room_a:frontier_exploration",
        kind="navigation_mode",
        context={"room": "room_a"},
        step=1,
        settings=coordinator.reward_settings,
    )
    progress.reinforcement.reward_key(
        "mode:room_a:frontier_exploration",
        12.0,
        event="observed progress",
        step=2,
    )
    coordinator.record_legal_options([option])
    updated = {
        candidate.candidate_id: candidate.last_shadow_ranking[0]["score"]
        for candidate in coordinator.candidates
    }
    assert updated["progress"] > initial["progress"]
    assert updated["balanced"] == initial["balanced"]
    assert updated["explorer"] == initial["explorer"]
    assert updated["loop_safe"] == initial["loop_safe"]


def test_one_candidate_owns_each_64_decision_segment_and_trace_is_cleared(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    explorer = SimpleNamespace(reinforcement=None)
    coordinator.bind_explorer(explorer)
    old_memory = coordinator.active.reinforcement
    old_memory.trace.appendleft({"key": "old", "step": 1})
    for step in range(64):
        coordinator.observe_step(
            step=step,
            state="overworld",
            telemetry_present=True,
            room="room_a",
            player_controlled=True,
            reason="explore",
            map_updates=[],
            safe_overworld=True,
        )
    assert coordinator.active.candidate_id == "balanced"
    assert coordinator.snapshot()["handoff_pending"] is True
    coordinator.commit_handoff()
    assert coordinator.active.candidate_id == "explorer"
    assert explorer.reinforcement is coordinator.active.reinforcement
    assert not old_memory.trace
    assert coordinator.candidates[0].active_decisions == 64
    assert all(candidate.active_decisions == 0 for candidate in coordinator.candidates[1:])


def test_segment_handoff_waits_through_dialogue_until_safe_overworld(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    explorer = SimpleNamespace(reinforcement=None)
    coordinator.bind_explorer(explorer)
    coordinator.observe_step(
        step=4,
        state="dialogue",
        telemetry_present=True,
        room="room_a",
        player_controlled=False,
        reason="advance dialogue",
        map_updates=[
            {
                "type": "navigation_goal_contract_end",
                "outcome": "completed",
                "reason": "target interaction opened dialogue",
            }
        ],
        safe_overworld=False,
    )
    coordinator.commit_handoff()
    assert coordinator.active.candidate_id == "balanced"
    assert coordinator.segment.pending_end_reason == "goal contract completed"
    coordinator.observe_step(
        step=5,
        state="overworld",
        telemetry_present=True,
        room="room_a",
        player_controlled=True,
        reason="control returned",
        map_updates=[],
        safe_overworld=True,
    )
    assert coordinator.snapshot()["handoff_pending"] is True
    coordinator.commit_handoff()
    assert coordinator.active.candidate_id == "explorer"


def test_two_round_robins_precede_deterministic_ucb_selection(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    observed = []
    for index in range(8):
        observed.append(coordinator.active.candidate_id)
        coordinator.active.total_points += index
        coordinator._handoff("synthetic completed segment")
    assert observed == [
        "balanced",
        "explorer",
        "progress",
        "loop_safe",
        "balanced",
        "explorer",
        "progress",
        "loop_safe",
    ]
    # Loop-safe received the highest mean points in the seeded round robin.
    assert coordinator.active.candidate_id == "loop_safe"


def test_scoring_events_normalization_and_safety_disqualification(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    updates = [
        {"type": "story_progress", "event": "interaction changed rooms"},
        {"type": "choice_outcome", "successful": True},
        {"type": "choice_outcome", "successful": False},
        {"type": "interactable", "confirmations": 1},
        {"type": "interaction_outcome", "last_outcome": "ordinary_dialogue"},
        {"type": "character_probe", "result": "no response"},
        {"type": "open_edge"},
        {"type": "navigation_goal_contract_end", "outcome": "failed", "reason": "stalled"},
    ]
    coordinator.observe_step(
        step=1,
        state="overworld",
        telemetry_present=True,
        room="room_a",
        player_controlled=True,
        reason="autonomy broad reset after forced loop escape",
        map_updates=updates,
        safe_overworld=False,
    )
    candidate = coordinator.active
    expected = -0.05 + 50 + 10 - 8 + 3 - 5 - 5 + 0.25 - 4 - 10 - 2
    assert candidate.total_points == pytest.approx(expected)
    assert candidate.normalized_score == pytest.approx(100 * expected / 65)
    candidate.loop_escapes = 8
    coordinator._apply_candidate_safety_gates(candidate)
    assert candidate.disqualified


def test_room_discovery_and_a_b_a_bounce_are_observed_outcomes(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, known_rooms=("room_a", "room_b"))
    for step, room in enumerate(("room_a", "room_b", "room_a", "room_new")):
        coordinator.observe_step(
            step=step,
            state="overworld",
            telemetry_present=True,
            room=room,
            player_controlled=True,
            reason="move",
            map_updates=[],
            safe_overworld=False,
        )
    assert coordinator.active.room_bounces == 1
    assert coordinator.active.breakdown["room_bounce"] == -15
    assert coordinator.active.breakdown["new_room"] == 15


def _write_profile(memory: Path) -> None:
    memory.mkdir(parents=True)
    (memory / "navigation.json").write_text('{"version":3,"cells":[]}\n', encoding="utf-8")
    (memory / "visual_states.json").write_text('{"version":1}\n', encoding="utf-8")
    views = memory / "room_views"
    views.mkdir()
    (views / "index.json").write_text('{"version":3,"rooms":{}}\n', encoding="utf-8")
    reinforcement = ReinforcementMemory(memory / "reinforcement.json")
    reinforcement.flush(force=True)
    StrategyGenome.default().save(memory / "strategy.json")


def _eligible_training_run(tmp_path: Path) -> tuple[Path, Path, TrainingWorkspace, PopulationCoordinator]:
    memory = tmp_path / "profile" / "memory"
    _write_profile(memory)
    run = tmp_path / "run"
    run.mkdir()
    workspace = TrainingWorkspace.create(run, memory)
    coordinator = workspace.coordinator()
    for index, candidate in enumerate(coordinator.candidates):
        candidate.active_decisions = 64
        candidate.segments_completed = 2
        candidate.total_points = {
            "balanced": 12.0,
            "explorer": 10.0,
            "progress": 18.0,
            "loop_safe": 11.0,
        }[candidate.candidate_id]
    coordinator.total_decisions = 100
    coordinator.telemetry_decisions = 95
    workspace.finalize(
        coordinator,
        stop_reason="gui_stop",
        telemetry_diagnostics={"received_packets": 104, "valid_packets": 100, "invalid_packets": 4},
        speed_diagnostics={"requested": "1", "verification_state": "not_required"},
        input_cleanup_succeeded=True,
        doctor_payload={"severity_counts": {"critical": 0}},
    )
    return memory, run, workspace, coordinator


def test_workspace_stages_all_mutable_memory_and_recommends_winner(tmp_path: Path) -> None:
    memory, run, workspace, coordinator = _eligible_training_run(tmp_path)
    baseline = memory_inventory(memory)
    (workspace.navigation_path).write_text('{"version":3,"cells":[["new"]]}\n', encoding="utf-8")
    coordinator.candidates[0].reinforcement.reward_key(
        "test", 3, event="test", step=1
    )
    coordinator.flush_candidates()
    assert memory_inventory(memory) == baseline
    scores = json.loads((run / "training_scores.json").read_text(encoding="utf-8"))
    assert scores["eligible_for_promotion"] is True
    assert scores["recommended_winner"] == "progress"
    assert scores["population_size"] == 4
    assert (run / "population_events.jsonl").is_file()
    assert (workspace.baseline_memory / "reinforcement.json").is_file()
    assert (workspace.baseline_memory / "strategy.json").is_file()
    assert all((workspace.candidates / name / "strategy.json").is_file() for name in ("balanced", "explorer", "progress", "loop_safe"))


def test_workspace_stages_selected_population_and_records_it_in_manifest(tmp_path: Path) -> None:
    memory = tmp_path / "profile" / "memory"
    _write_profile(memory)
    run = tmp_path / "run"
    run.mkdir()
    workspace = TrainingWorkspace.create(run, memory, population_size=7)
    coordinator = workspace.coordinator()
    manifest = json.loads((run / "training_manifest.json").read_text(encoding="utf-8"))

    assert workspace.population_size == 7
    assert len(workspace.candidate_ids) == 7
    assert manifest["population_size"] == 7
    assert manifest["candidate_ids"] == list(workspace.candidate_ids)
    assert len(coordinator.candidates) == 7
    assert coordinator.snapshot()["population_size"] == 7
    assert all((workspace.candidates / candidate_id).is_dir() for candidate_id in workspace.candidate_ids)


def test_promotion_checks_baseline_and_applies_transaction_with_backup(tmp_path: Path) -> None:
    memory, run, workspace, _coordinator = _eligible_training_run(tmp_path)
    workspace.navigation_path.write_text('{"version":3,"cells":[["promoted"]]}\n', encoding="utf-8")
    audit = promote_training_run(run, memory)
    assert audit["winner"] == "progress"
    assert Path(str(audit["backup_directory"])).is_dir()
    assert "promoted" in (memory / "navigation.json").read_text(encoding="utf-8")
    assert (memory / "promotion.json").is_file()
    assert json.loads((memory / "training_history.json").read_text(encoding="utf-8"))[-1]["winner"] == "progress"
    promoted, warning = StrategyGenome.load(memory / "strategy.json")
    assert warning is None
    original_progress = next(
        genome
        for candidate_id, _label, genome in population_genomes(StrategyGenome.default())
        if candidate_id == "progress"
    )
    assert promoted == original_progress
    next_population = population_genomes(promoted)
    assert next_population[0][2] == promoted


def test_promotion_refuses_profile_conflict(tmp_path: Path) -> None:
    memory, run, _workspace, _coordinator = _eligible_training_run(tmp_path)
    (memory / "navigation.json").write_text("changed\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="changed after training began"):
        promote_training_run(run, memory)


def test_promotion_rolls_back_if_final_replacement_fails(tmp_path: Path, monkeypatch) -> None:
    memory, run, _workspace, _coordinator = _eligible_training_run(tmp_path)
    baseline = memory_inventory(memory)
    from deltarune_agent import training_workspace as module

    real_replace = module.os.replace
    transaction_replaces = 0
    injected = False

    def fail_second(source, destination):
        nonlocal transaction_replaces, injected
        source_path = Path(source)
        if source_path == memory or source_path.name.endswith(".promoting"):
            transaction_replaces += 1
        if transaction_replaces == 2 and not injected:
            injected = True
            raise OSError("injected final replacement failure")
        return real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", fail_second)
    with pytest.raises(OSError, match="injected"):
        promote_training_run(run, memory)
    assert memory_inventory(memory) == baseline


@pytest.mark.parametrize(
    ("failure", "failed_check"),
    (
        ("stop", "clean_stop"),
        ("exposure", "all_candidates_exposed"),
        ("coverage", "telemetry_coverage"),
        ("invalid", "invalid_packet_rate"),
        ("speed", "speed_verification"),
        ("cleanup", "input_cleanup"),
        ("doctor", "run_doctor"),
    ),
)
def test_every_global_gate_blocks_recommendation(
    tmp_path: Path,
    failure: str,
    failed_check: str,
) -> None:
    memory = tmp_path / failure / "profile" / "memory"
    _write_profile(memory)
    run = tmp_path / failure / "run"
    run.mkdir()
    workspace = TrainingWorkspace.create(run, memory)
    coordinator = workspace.coordinator()
    for candidate in coordinator.candidates:
        candidate.active_decisions = 64
        candidate.segments_completed = 2
        candidate.total_points = 5
    if failure == "exposure":
        coordinator.candidates[0].active_decisions = 63
    coordinator.total_decisions = 100
    coordinator.telemetry_decisions = 50 if failure == "coverage" else 95
    stop_reason = "error" if failure == "stop" else "step_limit"
    invalid = 6 if failure == "invalid" else 4
    speed = (
        {"requested": "auto", "verification_state": "mismatch"}
        if failure == "speed"
        else {"requested": "1", "verification_state": "not_required"}
    )
    result = workspace.finalize(
        coordinator,
        stop_reason=stop_reason,
        telemetry_diagnostics={"received_packets": 104, "valid_packets": 100, "invalid_packets": invalid},
        speed_diagnostics=speed,
        input_cleanup_succeeded=failure != "cleanup",
        doctor_payload={"severity_counts": {"critical": 1 if failure == "doctor" else 0}},
    )
    assert result["eligible_for_promotion"] is False
    assert result["global_checks"][failed_check] is False
    assert memory_inventory(memory) == json.loads(
        (run / "baseline_fingerprints.json").read_text(encoding="utf-8")
    )["inventory"]
