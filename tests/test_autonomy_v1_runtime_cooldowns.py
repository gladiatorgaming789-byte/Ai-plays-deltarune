from __future__ import annotations

from deltarune_agent.autonomy_v1 import AutonomyOption, RecoveryLevel
from deltarune_agent.autonomy_v1_runtime import AutonomyV1RuntimeExplorer


def _geometry_option() -> AutonomyOption:
    return AutonomyOption(
        option_id="geometry_exit:guess:right",
        kind="geometry_exit_test",
        required_level=RecoveryLevel.BOUNDED_TEST,
        base_score=6.0,
        metadata={"key": ("room", 2, 3)},
    )


def test_active_geometry_cooldown_is_filtered(monkeypatch) -> None:
    explorer = AutonomyV1RuntimeExplorer.__new__(AutonomyV1RuntimeExplorer)
    monkeypatch.setattr(
        AutonomyV1RuntimeExplorer.__mro__[1],
        "_collect_geometry_exit_options",
        lambda self, room, cell: [_geometry_option()],
    )
    monkeypatch.setattr(explorer, "_visual_goal_is_cooling", lambda key: True)

    assert explorer._collect_geometry_exit_options("room", (0, 0)) == []


def test_expired_geometry_cooldown_becomes_eligible_again(monkeypatch) -> None:
    explorer = AutonomyV1RuntimeExplorer.__new__(AutonomyV1RuntimeExplorer)
    option = _geometry_option()
    monkeypatch.setattr(
        AutonomyV1RuntimeExplorer.__mro__[1],
        "_collect_geometry_exit_options",
        lambda self, room, cell: [option],
    )
    monkeypatch.setattr(explorer, "_visual_goal_is_cooling", lambda key: False)

    assert explorer._collect_geometry_exit_options("room", (0, 0)) == [option]
