from deltarune_agent.objectives import ObjectiveKind, ObjectiveManager


def test_battle_has_highest_priority():
    manager = ObjectiveManager()
    objective = manager.objective_for_state("battle", "", "room_test")
    assert objective.kind is ObjectiveKind.SURVIVE_BATTLE


def test_loop_recovery_beats_normal_exploration():
    manager = ObjectiveManager()
    objective = manager.objective_for_state(
        "overworld", "recover from repeated movement loop", "room_test"
    )
    assert objective.kind is ObjectiveKind.RECOVER
