from __future__ import annotations

from deltarune_agent.autonomy_v1 import AutonomyV1Explorer, RecoveryLevel
from deltarune_agent.run21_final import Run21Explorer


def _stub() -> AutonomyV1Explorer:
    explorer = AutonomyV1Explorer.__new__(AutonomyV1Explorer)
    explorer.last_ranked_autonomy_options = [{"old": True}]
    explorer.last_autonomy_selected_id = "old"
    explorer.last_autonomy_commitment_hold = True
    return explorer


def test_normal_recovery_delegates_to_run21_without_autonomy_ranking(monkeypatch) -> None:
    explorer = _stub()
    monkeypatch.setattr(
        explorer,
        "_update_recovery_state",
        lambda room, cell: RecoveryLevel.NORMAL,
    )
    monkeypatch.setattr(
        Run21Explorer,
        "_plan_exploration",
        lambda self, room, cell: ("left", 3, "run21 healthy-plan sentinel"),
    )

    plan = explorer._plan_exploration("room", (4, 4))

    assert plan == ("left", 3, "run21 healthy-plan sentinel")
    assert explorer.last_ranked_autonomy_options == []
    assert explorer.last_autonomy_selected_id is None
    assert explorer.last_autonomy_commitment_hold is False


def test_frontier_grace_also_delegates_to_run21(monkeypatch) -> None:
    explorer = _stub()
    monkeypatch.setattr(
        explorer,
        "_update_recovery_state",
        lambda room, cell: RecoveryLevel.FRONTIER,
    )
    monkeypatch.setattr(
        Run21Explorer,
        "_plan_exploration",
        lambda self, room, cell: ("up", 2, "run21 frontier sentinel"),
    )

    assert explorer._plan_exploration("room", (2, 2)) == (
        "up",
        2,
        "run21 frontier sentinel",
    )
