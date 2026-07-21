from dataclasses import replace

from PIL import Image

from deltarune_agent.hierarchical_policy import HierarchicalPolicy
from deltarune_agent.observer import Observation
from deltarune_agent.perception import GameState, Perception, VisualFeatures
from deltarune_agent.telemetry import TelemetrySample


def test_specialized_battle_controller_preserves_interaction_progress():
    observation = Observation(
        Image.new("RGB", (320, 240)),
        step=0,
        visual_valid=False,
    )
    features = VisualFeatures(0, 0, 0, 0, 0)
    battle = Perception(GameState.BATTLE, 0.99, features, "telemetry")
    overworld = Perception(GameState.OVERWORLD, 0.99, features, "telemetry")
    sample = TelemetrySample(
        "battle",
        1,
        "room_test",
        40,
        40,
        "obj_mainchara",
        1,
    )
    policy = HierarchicalPolicy()
    policy.explorer.interaction_candidate = (
        "room_test",
        5,
        5,
        "right",
        None,
        None,
        6,
        5,
    )

    action = policy.choose(observation, battle, sample)

    assert action.name == "wait"
    assert policy.explorer.active_interaction_saw_battle

    policy.choose(
        replace(observation, step=1),
        overworld,
        replace(sample, mode="overworld", received_at=2),
    )

    record = policy.explorer.interactables[("room_test", 6, 5)]
    assert record["last_outcome"] == "battle_started"
    assert record["progressions"] == 1
