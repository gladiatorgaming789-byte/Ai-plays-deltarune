from deltarune_agent.experience import NeuralTransition, read_transitions, write_transitions
from deltarune_agent.neural_env import DEFAULT_ENCODER
from deltarune_agent.neural_rewards import assign_movement_rewards, movement_reward


def make_transition():
    width = DEFAULT_ENCODER.width
    observation = [0.0] * width
    next_observation = [0.0] * width
    next_observation[0] = 0.5
    available = [1.0] * width
    return NeuralTransition(
        tuple(observation),
        tuple(available),
        "right",
        0.0,
        tuple(next_observation),
        tuple(available),
        False,
        1,
        2,
    )


def test_movement_reward_is_observable_only():
    transition = make_transition()
    assert movement_reward(transition, displacement_scale=1.0, action_cost=0.0) == 0.5


def test_assign_movement_rewards_marks_dataset(tmp_path):
    source = tmp_path / "source.jsonl"
    destination = tmp_path / "rewarded.jsonl"
    write_transitions(source, [make_transition()])
    report = assign_movement_rewards(source, destination)
    assert report["reward_contract"] == "observable_movement_v1"
    loaded = read_transitions(destination)
    assert loaded[0].reward_status == "assigned"
    assert loaded[0].reward > 0
