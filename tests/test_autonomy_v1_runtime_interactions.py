from __future__ import annotations

from deltarune_agent.autonomy_v1 import AutonomyOption, RecoveryLevel
from deltarune_agent.autonomy_v1_runtime import AutonomyV1RuntimeExplorer


def test_retry_interaction_prepares_concrete_visual_goal(monkeypatch) -> None:
    explorer = AutonomyV1RuntimeExplorer.__new__(AutonomyV1RuntimeExplorer)
    explorer.screen_regions = {}
    explorer.visual_goal = None
    monkeypatch.setattr(explorer, "_region", lambda cell: (cell[0] // 4, cell[1] // 4))
    monkeypatch.setattr(
        explorer,
        "_refresh_visual_guess_metadata",
        lambda region, record: None,
    )
    option = AutonomyOption(
        option_id="interaction:room:8:12",
        kind="retry_interaction",
        required_level=RecoveryLevel.EVIDENCE,
        base_score=10.0,
        metadata={
            "key": ("room", 8, 12),
            "source": (7, 12),
            "interaction_direction": "right",
            "first_direction": "right",
        },
    )

    plan = explorer._execute_option(option, "room", (7, 12))

    assert plan[0] == "right"
    assert explorer.visual_goal == ("room", 2, 3)
    record = explorer.screen_regions[("room", 2, 3)]
    assert record["choice_retry"] is True
    assert record["hypothesis"] == "possible_character"
