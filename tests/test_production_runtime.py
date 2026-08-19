from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def test_production_runtime_installs_same_repair_stack_before_policy_creation(
    tmp_path: Path,
) -> None:
    script = r'''
from pathlib import Path
import sys

from deltarune_agent.production_runtime import install_production_runtime
install_production_runtime()

from deltarune_agent.battle_v2 import BattleV2Controller
from deltarune_agent.frame_telemetry_sync import current_sync_status
from deltarune_agent.hierarchical_policy import HierarchicalPolicy
from deltarune_agent.special_gameplay import SpecialGameplayCoordinator

policy = HierarchicalPolicy(0, Path(sys.argv[1]))
assert isinstance(policy.battle, BattleV2Controller)
assert isinstance(policy.special_gameplay, SpecialGameplayCoordinator)
summary = policy.summary()
assert summary["battle_system"]["version"] == 2
assert summary["special_gameplay"]["version"] == 1
assert isinstance(current_sync_status(), dict)
print("production-runtime-ok")
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "navigation.json")],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "production-runtime-ok" in result.stdout
