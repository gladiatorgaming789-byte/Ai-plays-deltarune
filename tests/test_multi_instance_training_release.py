from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from deltarune_agent import multi_instance_training_release as release
from deltarune_agent import multi_instance_training_v21 as v21
from deltarune_agent.strategy import StrategyGenome


def _candidate(tmp_path: Path):
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "navigation.json").write_text('{"version":3}\n', encoding="utf-8")
    (memory / "visual_states.json").write_text('{"version":1}\n', encoding="utf-8")
    (memory / "reinforcement.json").write_text('{"version":1}\n', encoding="utf-8")
    StrategyGenome.default().save(memory / "strategy.json")
    workspace = v21.legacy.MultiInstanceWorkspace.create(
        tmp_path / "runs",
        memory,
        population_size=2,
        chapter=1,
        ports=(42100, 42101),
    )
    candidate = workspace.candidates[0]
    candidate.game_process = type("Process", (), {"pid": 12345})()
    return candidate


def test_safe_worker_builder_does_not_call_redirected_legacy_symbol(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    args = Namespace(steps=400, speed="1", seed=42, interval=None)
    original = v21.legacy._worker_arguments
    try:
        # Reproduce the supervisor's temporary redirection. The release builder
        # must use its captured base function rather than recurse through this
        # symbol.
        v21.legacy._worker_arguments = release._safe_worker_arguments
        arguments = release._safe_worker_arguments(candidate, args)
    finally:
        v21.legacy._worker_arguments = original

    assert arguments[arguments.index("--seed") + 1] == "42"
    assert int(arguments[arguments.index("--steps") + 1]) == 400 + v21.SAFE_STOP_RESERVE_STEPS
