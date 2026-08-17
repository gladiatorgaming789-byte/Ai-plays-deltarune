from argparse import Namespace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deltarune_agent import multi_instance_training as multi
from deltarune_agent.strategy import StrategyGenome


def _profile(path: Path) -> Path:
    path.mkdir()
    (path / "navigation.json").write_text('{"version": 3}\n', encoding="utf-8")
    (path / "visual_states.json").write_text('{"version": 1}\n', encoding="utf-8")
    (path / "reinforcement.json").write_text('{"version": 1}\n', encoding="utf-8")
    StrategyGenome.default().save(path / "strategy.json")
    return path


def test_workspace_gives_every_ai_private_memory_port_save_and_strategy(tmp_path: Path) -> None:
    profile = _profile(tmp_path / "memory")
    workspace = multi.MultiInstanceWorkspace.create(
        tmp_path / "runs",
        profile,
        population_size=4,
        chapter=1,
        ports=(42100, 42101, 42102, 42103),
    )

    assert len(workspace.candidates) == 4
    assert len({candidate.port for candidate in workspace.candidates}) == 4
    assert len({candidate.save_id for candidate in workspace.candidates}) == 4
    assert len({candidate.memory for candidate in workspace.candidates}) == 4
    assert all((candidate.memory / "navigation.json").is_file() for candidate in workspace.candidates)
    genomes = [
        json.loads((candidate.memory / "strategy.json").read_text(encoding="utf-8"))
        for candidate in workspace.candidates
    ]
    assert genomes[0] != genomes[1]
    manifest = json.loads(
        (workspace.run_directory / "training_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["architecture"] == multi.MULTI_INSTANCE_ARCHITECTURE
    assert manifest["population_size"] == 4


def test_isolated_save_seed_copies_files_but_never_recurses_into_training(tmp_path: Path) -> None:
    save_root = tmp_path / "DELTARUNE"
    save_root.mkdir()
    (save_root / "filech1_0").write_text("main save", encoding="utf-8")
    (save_root / "dr.ini").write_text("settings", encoding="utf-8")
    old = save_root / "ai_training" / "old-agent"
    old.mkdir(parents=True)
    (old / "filech1_0").write_text("old training", encoding="utf-8")

    destination = multi.seed_isolated_save(save_root, "new-agent")

    assert (destination / "filech1_0").read_text(encoding="utf-8") == "main save"
    assert (destination / "dr.ini").is_file()
    assert not (destination / "ai_training").exists()
    assert (save_root / "filech1_0").read_text(encoding="utf-8") == "main save"


def test_game_install_requires_multi_instance_mod_marker(tmp_path: Path) -> None:
    root = tmp_path / "DELTARUNE"
    chapter = root / "chapter1_windows"
    chapter.mkdir(parents=True)
    (root / "DELTARUNE.exe").write_bytes(b"runner")
    data = chapter / "data.win"
    data.write_bytes(b"clean game")

    with pytest.raises(RuntimeError, match="AI Support"):
        multi.validate_game_install(root, 1)

    data.write_bytes(b"prefix AI_MULTI_INSTANCE|1| suffix")
    executable, working_directory = multi.validate_game_install(root, 1)
    assert executable == root / "DELTARUNE.exe"
    assert working_directory == chapter


def test_worker_arguments_target_only_one_process_port_memory_and_run_root(tmp_path: Path) -> None:
    profile = _profile(tmp_path / "memory")
    workspace = multi.MultiInstanceWorkspace.create(
        tmp_path / "runs",
        profile,
        population_size=2,
        chapter=1,
        ports=(42100, 42101),
    )
    candidate = workspace.candidates[1]
    candidate.game_process = SimpleNamespace(pid=7654)
    args = Namespace(steps=500, speed="2", seed=10, interval=0.03)

    arguments = multi._worker_arguments(candidate, args)

    assert "--training" not in arguments
    assert arguments[arguments.index("--game-pid") + 1] == "7654"
    assert arguments[arguments.index("--telemetry-port") + 1] == "42101"
    assert arguments[arguments.index("--memory") + 1] == str(candidate.memory / "navigation.json")
    assert arguments[arguments.index("--runs-root") + 1] == str(candidate.runs)
    assert "--background-input" in arguments


def test_snapshot_reports_all_independent_ais_without_one_segment_owner(tmp_path: Path) -> None:
    profile = _profile(tmp_path / "memory")
    workspace = multi.MultiInstanceWorkspace.create(
        tmp_path / "runs",
        profile,
        population_size=3,
        chapter=1,
        ports=(42100, 42101, 42102),
    )
    for index, candidate in enumerate(workspace.candidates):
        candidate.game_process = SimpleNamespace(pid=7000 + index)
        candidate.controller_process = SimpleNamespace(poll=lambda: None)
        candidate.status = "running"
        candidate.latest_action = f"move-{index}"
        candidate.decisions = 10 + index

    snapshot = multi._candidate_snapshot(workspace)

    assert snapshot["architecture"] == multi.MULTI_INSTANCE_ARCHITECTURE
    assert snapshot["active_candidate"] == ""
    assert snapshot["all_instances_active"] is True
    assert len(snapshot["candidates"]) == 3
    assert {
        candidate["process_id"] for candidate in snapshot["candidates"]
    } == {7000, 7001, 7002}
    assert snapshot["recommendations"] == snapshot["shadow_rankings"]


def test_candidate_run_gate_requires_clean_independent_runtime_evidence(tmp_path: Path) -> None:
    profile = _profile(tmp_path / "memory")
    workspace = multi.MultiInstanceWorkspace.create(
        tmp_path / "runs",
        profile,
        population_size=2,
        chapter=1,
        ports=(42100, 42101),
    )
    candidate = workspace.candidates[0]
    candidate.decisions = 100
    run = candidate.runs / "run-1"
    run.mkdir()
    candidate.summary_path = run / "summary.json"
    (run / "run.json").write_text(
        '{"stop_reason":"step_limit"}\n', encoding="utf-8"
    )
    (run / "run_doctor.json").write_text(
        '{"severity_counts":{"critical":0}}\n', encoding="utf-8"
    )
    summary = {
        "telemetry_diagnostics": {"valid_packets": 100, "invalid_packets": 1},
        "speed_synchronization": {"requested": "2", "verification_state": "matched"},
        "input_cleanup_succeeded": True,
        "oscillation_breaks": 2,
        "session_room_link_bounces": 1,
        "transitions": [{}, {}, {}, {}],
    }

    multi._validate_candidate_run(candidate, summary)

    assert candidate.disqualified is False
    assert candidate.telemetry_coverage == 1.0
    assert candidate.invalid_packet_rate < 0.05
    assert candidate.input_cleanup_succeeded is True
    assert candidate.doctor_critical_findings == 0


def test_candidate_run_gate_rejects_bad_telemetry_speed_cleanup_and_loops(tmp_path: Path) -> None:
    profile = _profile(tmp_path / "memory")
    workspace = multi.MultiInstanceWorkspace.create(
        tmp_path / "runs",
        profile,
        population_size=2,
        chapter=1,
        ports=(42100, 42101),
    )
    candidate = workspace.candidates[0]
    candidate.decisions = 100
    run = candidate.runs / "run-1"
    run.mkdir()
    candidate.summary_path = run / "summary.json"
    (run / "run.json").write_text('{"stop_reason":"error"}\n', encoding="utf-8")
    (run / "run_doctor.json").write_text(
        '{"severity_counts":{"critical":1}}\n', encoding="utf-8"
    )

    multi._validate_candidate_run(
        candidate,
        {
            "telemetry_diagnostics": {"valid_packets": 50, "invalid_packets": 10},
            "speed_synchronization": {"requested": "2", "verification_state": "stale"},
            "input_cleanup_succeeded": False,
            "oscillation_breaks": 8,
            "session_room_link_bounces": 4,
            "transitions": [{}, {}, {}, {}, {}, {}],
        },
    )

    assert candidate.disqualified is True
    joined = " | ".join(candidate.disqualification_reasons)
    assert "telemetry" in joined
    assert "speed" in joined
    assert "input cleanup" in joined
    assert "loop" in joined
    assert "room-bounce" in joined
    assert "Run Doctor" in joined


def test_promotion_replaces_profile_with_only_independent_winner_memory(tmp_path: Path) -> None:
    profile = _profile(tmp_path / "memory")
    workspace = multi.MultiInstanceWorkspace.create(
        tmp_path / "runs",
        profile,
        population_size=2,
        chapter=1,
        ports=(42100, 42101),
    )
    winner = workspace.candidates[1]
    (winner.memory / "winner.txt").write_text("explorer learned this", encoding="utf-8")
    eligibility = {
        "eligible_for_promotion": True,
        "recommended_winner": winner.candidate_id,
        "winner_explanation": "Explorer won an independent comparison.",
    }
    workspace.update_manifest(status="review_ready", eligibility=eligibility)

    audit = multi.promote_multi_instance_training_run(workspace.run_directory, profile)

    assert audit["winner"] == winner.candidate_id
    assert (profile / "winner.txt").read_text(encoding="utf-8") == "explorer learned this"
    assert (profile / "promotion.json").is_file()
    assert Path(audit["backup_directory"]).is_dir()
