from deltarune_agent.actions import ACTIONS
from deltarune_agent.neural_env import NeuralObservationEncoder
from deltarune_agent.telemetry import TelemetrySample


def make_sample(**overrides):
    values = dict(
        mode="overworld",
        room_id=7,
        room_name="room_home",
        x=120.0,
        y=42.0,
        object_name="obj_mainchara",
        received_at=10.0,
        version=9,
        hspeed=4.0,
        vspeed=0.0,
        speed=4.0,
        direction=0.0,
        player_facing_direction="right",
        player_x=120.0,
        player_y=42.0,
        player_foot_x=120.0,
        player_foot_y=50.0,
        room_width=640.0,
        room_height=480.0,
        camera_x=32.0,
        camera_y=16.0,
        camera_width=320.0,
        camera_height=240.0,
        player_bbox_left=112.0,
        player_bbox_top=30.0,
        player_bbox_right=128.0,
        player_bbox_bottom=50.0,
        sprite_width=40.0,
        sprite_height=56.0,
        image_xscale=1.0,
        image_yscale=1.0,
        room_speed=30.0,
        fps=60.0,
        game_time_ms=5000.0,
        sample_delta_x=4.0,
        sample_delta_y=0.0,
        sample_interval_ms=33.3,
        interaction_state=0,
        player_controlled=True,
        visible=True,
        packet_sequence=12,
    )
    values.update(overrides)
    return TelemetrySample(**values)


def test_encoder_has_fixed_width_and_no_nan():
    encoder = NeuralObservationEncoder()
    observation = encoder.encode(
        make_sample(),
        previous_action="right",
        previous_action_duration=0.1,
    )

    assert observation.width == encoder.width
    assert observation.width == len(observation.feature_names)
    assert encoder.combined_width == observation.width * 2
    assert all(value == value for value in observation.combined())
    assert all(abs(value) <= 1.0 for value in observation.values)
    assert all(value in (0.0, 1.0) for value in observation.available)


def test_encoder_is_deterministic():
    encoder = NeuralObservationEncoder()
    first = encoder.encode(make_sample(), previous_action="right", previous_action_duration=0.1)
    second = encoder.encode(make_sample(), previous_action="right", previous_action_duration=0.1)

    assert first == second


def test_missing_optional_telemetry_uses_zero_plus_mask():
    encoder = NeuralObservationEncoder()
    observation = encoder.encode(
        make_sample(
            hspeed=None,
            vspeed=None,
            speed=None,
            camera_x=None,
            camera_y=None,
            camera_width=None,
            camera_height=None,
            player_controlled=None,
        )
    )

    for name in (
        "velocity.x",
        "velocity.y",
        "motion.speed",
        "camera.x",
        "camera.y",
        "camera.width",
        "camera.height",
        "state.player_controlled",
    ):
        index = observation.feature_names.index(name)
        assert observation.values[index] == 0.0
        assert observation.available[index] == 0.0


def test_previous_action_is_encoded_numerically():
    encoder = NeuralObservationEncoder()
    right = encoder.encode(make_sample(), previous_action="right", previous_action_duration=0.25)
    left = encoder.encode(make_sample(), previous_action="left", previous_action_duration=0.25)

    right_index = right.feature_names.index("action.previous.right")
    left_index = left.feature_names.index("action.previous.left")
    assert right.values[right_index] == 1.0
    assert right.values[left_index] == 0.0
    assert left.values[right_index] == 0.0
    assert left.values[left_index] == 1.0


def test_room_identity_does_not_expose_room_name_text():
    encoder = NeuralObservationEncoder()
    observation = encoder.encode(make_sample(room_name="room_secret_name"))

    assert "room_secret_name" not in observation.values
    assert all(isinstance(value, float) for value in observation.values)


def test_action_order_is_stable():
    encoder = NeuralObservationEncoder()
    assert encoder.width > 0
    assert tuple(ACTIONS.keys())[0] == "wait"
