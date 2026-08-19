from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from deltarune_agent import multi_instance_training as legacy
from deltarune_agent import multi_instance_training_release as release
from deltarune_agent import multi_instance_training_v21 as v21
from deltarune_agent.strategy import StrategyGenome


def _profile(path: Path) -> Path:
    path.mkdir()
    (path / "navigation.json").write_text('{"version":3}\n', encoding="utf-8")
    (path / "visual_states.json").write_text('{"version":1}\n', encoding="utf-8")
    (path / "reinforcement.json").write_text('{"version":1}\n', encoding="utf-8")
    StrategyGenome.default().save(path / "strategy.json")
    return path


def _workspace(tmp_path: Path, count: int = 2):
    profile = _profile(tmp_path / "memory")
    return legacy.MultiInstanceWorkspace.create(
        tmp_path / "runs",
        profile,
        population_size=count,
        chapter=1,
        ports=tuple(42100 + index for index in range(count)),
    )


def test_preflight_requires_training_only_autosave_marker(tmp_path: Path) -> None:
    root = tmp_path / "DELTARUNE"
    chapter = root / "chapter1_windows"
    chapter.mkdir(parents=True)
    (root / "DELTARUNE.exe").write_bytes(b"runner")
    data = chapter / "data.win"
    data.write_bytes(
        b"AI_MULTI_INSTANCE|1| DRTEL|9| AI_SPEED_MOD|1| "
        b"AI_BACKGROUND_AUTOSAVE_V1"
    )
    with pytest.raises(RuntimeError, match="Update/re-import"):
        v21.validate_game_install(root, 1)

    data.write_bytes(
        b"AI_MULTI_INSTANCE|1| DRTEL|9| AI_SPEED_MOD|1| "
        b"AI_BACKGROUND_AUTOSAVE_V2"
    )
    executable, directory = v21.validate_game_install(root, 1)
    assert executable == root / "DELTARUNE.exe"
    assert directory == chapter


def test_passive_wait_and_control_lock_do_not_count_as_active_decisions(tmp_path: Path) -> None:
    candidate = _workspace(tmp_path).candidates[0]
    payload = {
        "step": 0,
        "state": "overworld",
        "action": "wait",
        "reason": "transition control locked; release movement until control returns",
        "telemetry": {"room_name": "room_a", "player_controlled": False},
        "map_updates": [],
    }
    release._update_candidate_event(candidate, payload)
    assert candidate.decisions == 0
    assert candidate.telemetry_decisions == 0

    payload = {
        "step": 1,
        "state": "overworld",
        "action": "right",
        "reason": "follow learned frontier",
        "telemetry": {"room_name": "room_a", "player_controlled": True},
        "map_updates": [],
    }
    release._update_candidate_event(candidate, payload)
    assert candidate.decisions == 1
    assert candidate.telemetry_decisions == 1


def test_telemetry_coverage_uses_active_decisions_not_udp_layer_count(tmp_path: Path) -> None:
    candidate = _workspace(tmp_path).candidates[0]
    candidate.decisions = 10
    candidate.telemetry_decisions = 7
    candidate.safe_to_stop = True
    candidate.safe_stop_sent = True
    run = candidate.runs / "run-1"
    run.mkdir()
    candidate.summary_path = run / "summary.json"
    (run / "run.json").write_text('{"stop_reason":"gui_stop"}\n', encoding="utf-8")
    (run / "run_doctor.json").write_text(
        '{"severity_counts":{"critical":0}}\n', encoding="utf-8"
    )
    summary = {
        "telemetry_diagnostics": {
            # Multipart v9 traffic can greatly exceed the number of decisions.
            "valid_packets": 400,
            "invalid_packets": 0,
        },
        "speed_synchronization": {
            "requested": "1",
            "verification_state": "not_required",
        },
        "input_cleanup_succeeded": True,
    }

    v21._validate_candidate_run(candidate, summary)

    assert candidate.telemetry_coverage == pytest.approx(0.7)
    assert candidate.disqualified is True
    assert any("90% of active decisions" in reason for reason in candidate.disqualification_reasons)


def test_event_scoring_uses_current_structured_policy_updates(tmp_path: Path) -> None:
    candidate = _workspace(tmp_path).candidates[0]
    release._update_candidate_event(
        candidate,
        {
            "step": 0,
            "state": "overworld",
            "action": "right",
            "reason": "explore",
            "telemetry": {"room_name": "room_a", "player_controlled": True},
            "map_updates": [{"type": "open_edge", "room": "room_a", "from_cell": [0, 0], "to_cell": [1, 0]}],
        },
    )
    release._update_candidate_event(
        candidate,
        {
            "step": 1,
            "state": "overworld",
            "action": "right",
            "reason": "cross room boundary",
            "telemetry": {"room_name": "room_b", "player_controlled": True},
            "map_updates": [
                {"type": "story_progress", "event": "discovered a new room", "room": "room_b"},
                {"type": "choice_outcome", "room": "room_a", "pattern": 1, "successful": False},
            ],
        },
    )
    release._update_candidate_event(
        candidate,
        {
            "step": 2,
            "state": "overworld",
            "action": "confirm",
            "reason": "observed interaction consequence",
            "telemetry": {"room_name": "room_b", "player_controlled": True},
            "map_updates": [
                {"type": "interaction_outcome", "room": "room_b", "cell": [3, 4], "usefulness": "flavor"},
                {"type": "choice_outcome", "room": "room_b", "pattern": 2, "successful": True},
                {"type": "story_progress", "event": "interaction caused a scripted sequence", "room": "room_b"},
            ],
        },
    )

    assert candidate.decisions == 3
    assert candidate.observed_non_discovery_progress == 1
    assert candidate.observed_choice_successes == 1
    assert candidate.observed_choice_failures == 1
    assert candidate.observed_new_interactables == 1
    assert candidate.observed_new_open_edges == 1
    assert candidate.observed_flavor_interactions == 1
    # 50 progress + 15 room + 10 choice + 3 interaction + .25 edge
    # - 5 flavor - 8 failed choice - .15 decision cost = 65.10
    assert candidate.total_points == pytest.approx(65.10)


def test_all_instances_active_requires_every_controller(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, 3)
    for candidate in workspace.candidates:
        candidate.controller_process = SimpleNamespace(poll=lambda: None)
    workspace.candidates[-1].controller_process = SimpleNamespace(poll=lambda: 0)
    assert v21._candidate_snapshot(workspace)["all_instances_active"] is False


def test_all_candidates_use_same_experiment_seed_and_receive_stop_reserve(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, 2)
    args = Namespace(steps=500, speed="1", seed=77, interval=None)
    seeds = []
    steps = []
    for index, candidate in enumerate(workspace.candidates):
        candidate.game_process = SimpleNamespace(pid=7000 + index)
        arguments = v21._worker_arguments(candidate, args)
        seeds.append(arguments[arguments.index("--seed") + 1])
        steps.append(int(arguments[arguments.index("--steps") + 1]))
    assert seeds == ["77", "77"]
    assert steps == [500 + v21.SAFE_STOP_RESERVE_STEPS] * 2


def test_safe_stop_waits_for_controlled_overworld(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, 2)
    candidate = workspace.candidates[0]
    candidate.controller_process = SimpleNamespace(poll=lambda: None)
    candidate.loop_steps = 100
    candidate.safe_to_stop = False
    v21._request_safe_stops(workspace, gui_stop=True, target_steps=100)
    assert not candidate.stop_file.exists()

    candidate.safe_to_stop = True
    v21._request_safe_stops(workspace, gui_stop=True, target_steps=100)
    assert candidate.stop_file.is_file()
    assert candidate.safe_stop_sent is True


def test_worker_event_does_not_embed_full_population_snapshot(tmp_path: Path, monkeypatch) -> None:
    candidate = _workspace(tmp_path).candidates[0]
    candidate.game_process = SimpleNamespace(pid=1234)
    emitted: list[dict[str, object]] = []
    monkeypatch.setattr(v21.legacy, "_emit", emitted.append)
    payload = {"step": 1, "action": "right", "reason": "explore"}
    v21._emit_worker_event(candidate, payload)
    assert "instance" in emitted[0]
    assert "training" not in emitted[0]
