import pytest

torch = pytest.importorskip("torch")

from deltarune_agent.neural_controller import NeuralTemporalAgent
from deltarune_agent.neural_env import DEFAULT_ENCODER
from deltarune_agent.neural_temporal import NeuralTemporalConfig, build_temporal_policy_network


def test_temporal_agent_waits_until_history_is_full():
    model = build_temporal_policy_network(
        DEFAULT_ENCODER.combined_width,
        NeuralTemporalConfig(sequence_length=3),
    )
    agent = NeuralTemporalAgent(model, torch.device("cpu"), sequence_length=3)

    class Sample:
        packet_sequence = 1
        room_id = 1
        room_name = "test"
        mode = "overworld"
        x = 10.0
        y = 20.0
        player_x = 10.0
        player_y = 20.0
        player_foot_x = None
        player_foot_y = None
        hspeed = 0.0
        vspeed = 0.0
        speed = 0.0
        direction = 0.0
        room_width = 640.0
        room_height = 480.0
        camera_x = 0.0
        camera_y = 0.0
        camera_width = 320.0
        camera_height = 240.0
        player_bbox_left = None
        player_bbox_top = None
        player_bbox_right = None
        player_bbox_bottom = None
        sprite_width = None
        sprite_height = None
        image_xscale = None
        image_yscale = None
        room_speed = 60.0
        fps = 60.0
        game_time_ms = 1000.0
        sample_interval_ms = 16.0
        sample_delta_x = 0.0
        sample_delta_y = 0.0
        interaction_state = 0
        player_controlled = True
        visible = True
        player_facing_direction = "down"

    observation = agent.observe(Sample())
    assert agent.predict(observation).action == "wait"
    assert len(agent.history) == 1
