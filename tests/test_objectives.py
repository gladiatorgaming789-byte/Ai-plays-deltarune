from deltarune_agent.objectives import ObjectiveKind, ObjectiveManager


def test_battle_has_highest_priority():
    manager = ObjectiveManager()
    objective = manager.objective_for_state("battle", "", "room_test")
    assert objective.kind is ObjectiveKind.SURVIVE_BATTLE


def test_loop_recovery_beats_normal_exploration():
    manager = ObjectiveManager()
    objective = manager.objective_for_state(
        "overworld",
        "recover from repeated movement loop",
        "room_test",
    )
    assert objective.kind is ObjectiveKind.RECOVER


def test_changing_reason_does_not_count_as_new_objective():
    manager = ObjectiveManager()

    manager.objective_for_state(
        "overworld",
        "follow learned warp through doorway",
        "room_test",
    )
    manager.objective_for_state(
        "overworld",
        "move down toward a different learned warp",
        "room_test",
    )

    assert len(manager.history) == 1
    assert manager.change_count == 1
    assert manager.current is not None
    assert manager.current.kind is ObjectiveKind.SEEK_EXIT


def test_objective_change_count_is_not_limited_by_retained_history():
    manager = ObjectiveManager()

    for index in range(150):
        manager.objective_for_state(
            "overworld",
            "explore unknown space",
            f"room_{index}",
        )

    assert len(manager.history) == 100
    assert manager.change_count == 150
