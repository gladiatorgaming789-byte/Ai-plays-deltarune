from deltarune_agent.perception import GameState, Perception, VisualFeatures
from deltarune_agent.telemetry import (
    TelemetryReceiver,
    TelemetrySample,
    facing_from_sprite,
    fuse_perception,
    merge_samples,
    parse_packet,
)


class _PacketSocket:
    def __init__(self, packets):
        self.packets = list(packets)

    def recvfrom(self, _size):
        if not self.packets:
            raise BlockingIOError
        return self.packets.pop(0), ("127.0.0.1", 42069)


def test_parses_gamemaker_header_and_payload():
    packet = b"0123456789abDRTEL|1|overworld|7|room_home|120.5|42|obj_mainchara|end\x00"
    sample = parse_packet(packet, received_at=10.0)
    assert sample is not None
    assert sample.room_name == "room_home"
    assert sample.x == 120.5


def test_overworld_telemetry_overrides_false_battle():
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    visual = Perception(GameState.BATTLE, 0.7, features)
    sample = parse_packet(b"DRTEL|1|overworld|7|room_home|120|42|obj_mainchara|end")
    result = fuse_perception(visual, sample)
    assert result.state is GameState.OVERWORLD
    assert result.source == "telemetry"


def test_current_overworld_telemetry_overrides_false_visual_dialogue():
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    visual = Perception(GameState.DIALOGUE, 0.92, features)
    sample = TelemetrySample(
        "overworld",
        7,
        "room_torbathroom",
        201,
        131,
        "obj_mainchara",
        0,
        version=8,
    )

    result = fuse_perception(visual, sample)

    assert result.state is GameState.OVERWORLD
    assert result.source == "telemetry"


def test_v2_dialogue_telemetry_is_authoritative():
    features = VisualFeatures(0, 0, 0, 0, 0, 0)
    visual = Perception(GameState.OVERWORLD, 0.58, features)
    sample = parse_packet(b"DRTEL|2|dialogue|7|room_home|0|0|obj_writer|end")
    result = fuse_perception(visual, sample)
    assert sample is not None and sample.version == 2
    assert result.state is GameState.DIALOGUE


def test_v3_parses_extended_motion_context():
    packet = (
        b"DRTEL|3|overworld|7|room_krisroom|320|240|obj_mainchara|640|480|"
        b"spr_kris|2|270|4|0|4|0.2|end"
    )
    sample = parse_packet(packet)
    assert sample is not None
    assert sample.version == 3
    assert sample.room_width == 640
    assert sample.sprite_name == "spr_kris"
    assert sample.direction == 270
    assert sample.hspeed == 4


def test_v4_parses_collision_and_nearby_interaction_context():
    packet = (
        b"DRTEL|4|overworld|7|room_home|120|42|obj_mainchara|320|240|"
        b"spr_kris|2|270|4|0|4|0.2|100001|116|42|112|30|128|50|"
        b"-100|1|1|30|54321|obj_interactablesolid|100099|140|42|20|end"
    )
    sample = parse_packet(packet)

    assert sample is not None
    assert sample.version == 4
    assert sample.instance_id == 100001
    assert sample.previous_x == 116
    assert sample.bbox_right == 128
    assert sample.nearest_interactable_name == "obj_interactablesolid"
    assert sample.nearest_interactable_distance == 20
    assert sample.player_x == 120


def test_receiver_redacts_legacy_hidden_object_fields_before_policy_use():
    receiver = TelemetryReceiver.__new__(TelemetryReceiver)
    receiver.socket = _PacketSocket(
        [
            b"DRTEL|4|overworld|7|room_home|120|42|obj_mainchara|320|240|"
            b"spr_kris|2|270|4|0|4|0.2|100001|116|42|112|30|128|50|"
            b"-100|1|1|30|54321|obj_interactablesolid|100099|140|42|20|end"
        ]
    )
    receiver.latest = None
    receiver.by_mode = {}

    sample = receiver.poll()

    assert sample is not None
    assert sample.bbox_right == 128
    assert sample.nearest_interactable_name is None
    assert sample.nearest_interactable_id is None
    assert sample.nearest_interactable_x is None


def test_v5_minimal_fallback_packet_is_accepted():
    sample = parse_packet(
        b"DRTEL|5|overworld|7|room_home|120|42|obj_mainchara|end"
    )

    assert sample is not None
    assert sample.version == 5
    assert sample.room_name == "room_home"
    assert sample.player_x == 120
    assert sample.room_width is None


def test_v5_rich_packet_keeps_extended_fields():
    packet = (
        b"DRTEL|5|overworld|7|room_home|120|42|obj_mainchara|320|240|"
        b"spr_kris|2|270|4|0|4|0.2|100001|116|42|112|30|128|50|"
        b"-100|1|1|30|54321|obj_interactablesolid|100099|140|42|20|end"
    )
    sample = parse_packet(packet)

    assert sample is not None
    assert sample.version == 5
    assert sample.bbox_right == 128
    assert sample.nearest_interactable_id == 100099


def test_v6_minimal_fallback_packet_is_accepted():
    sample = parse_packet(
        b"DRTEL|6|overworld|7|room_home|120|42|obj_mainchara|end"
    )

    assert sample is not None
    assert sample.version == 6
    assert sample.room_name == "room_home"
    assert sample.sprite_name is None


def test_v6_motion_packet_supplies_sprite_and_direction_without_rich_fields():
    packet = (
        b"DRTEL|6|overworld|7|room_krisroom|320|240|obj_mainchara|640|480|"
        b"spr_krisd|2|0|4|0|4|0.2|end"
    )
    sample = parse_packet(packet)

    assert sample is not None
    assert sample.version == 6
    assert sample.sprite_name == "spr_krisd"
    assert sample.facing_direction == "down"
    assert sample.direction == 0
    assert sample.hspeed == 4
    assert sample.instance_id is None
    assert sample.nearest_interactable_name is None


def test_v6_rich_packet_keeps_collision_and_interaction_fields():
    packet = (
        b"DRTEL|6|overworld|7|room_home|120|42|obj_mainchara|320|240|"
        b"spr_krisr_dark|2|0|4|0|4|0.2|100001|116|42|112|30|128|50|"
        b"-100|1|1|30|54321|obj_interactablesolid|100099|140|42|20|end"
    )
    sample = parse_packet(packet)

    assert sample is not None
    assert sample.version == 6
    assert sample.sprite_name == "spr_krisr_dark"
    assert sample.facing_direction == "right"
    assert sample.direction == 0
    assert sample.bbox_right == 128
    assert sample.nearest_interactable_id == 100099


def test_v7_motion_packet_supplies_camera_view_without_hidden_objects():
    packet = (
        b"DRTEL|7|overworld|7|room_home|120|42|obj_mainchara|540|240|"
        b"spr_krisr|2|0|4|0|4|0.2|32|16|320|240|end"
    )
    sample = parse_packet(packet)

    assert sample is not None
    assert sample.version == 7
    assert sample.camera_x == 32
    assert sample.camera_y == 16
    assert sample.camera_width == 320
    assert sample.camera_height == 240
    assert sample.facing_direction == "right"
    assert sample.nearest_interactable_name is None


def test_v7_rich_packet_keeps_camera_and_collision_field_offsets():
    packet = (
        b"DRTEL|7|overworld|7|room_home|120|42|obj_mainchara|540|240|"
        b"spr_krisd|2|0|4|0|4|0.2|32|16|320|240|"
        b"100001|116|42|112|30|128|50|-100|1|1|30|54321||-4|0|0|-1|end"
    )
    sample = parse_packet(packet)

    assert sample is not None
    assert sample.camera_x == 32
    assert sample.camera_width == 320
    assert sample.instance_id == 100001
    assert sample.previous_x == 116
    assert sample.bbox_right == 128
    assert sample.nearest_interactable_name is None
    assert sample.nearest_interactable_id == -4


def test_v8_rich_packet_reports_the_verified_player_control_gate():
    packet = (
        b"DRTEL|8|overworld|7|room_home|120|42|obj_mainchara|540|240|"
        b"spr_krisd|2|0|4|0|4|0.2|32|16|320|240|"
        b"100001|116|42|112|30|128|50|-100|1|1|30|54321||-4|0|0|-1|1|end"
    )
    sample = parse_packet(packet)

    assert sample is not None
    assert sample.version == 8
    assert sample.player_x == 120
    assert sample.interaction_state == 1
    assert sample.player_controlled is False


def test_v8_control_packet_survives_without_optional_collision_fields():
    packet = (
        b"DRTEL|8|overworld|7|room_home|120|42|obj_mainchara|540|240|"
        b"spr_krisd|2|0|4|0|4|0.2|32|16|320|240|0|end"
    )
    sample = parse_packet(packet)

    assert sample is not None
    assert sample.camera_x == 32
    assert sample.camera_height == 240
    assert sample.instance_id is None
    assert sample.interaction_state == 0
    assert sample.player_controlled is True


def test_v9_named_motion_packet_parses_camera_control_and_player_origin():
    packet = (
        b"DRTEL|9|overworld|7|room_home|120|42|obj_mainchara|"
        b"part=motion|seq=31|room_width=540|room_height=240|"
        b"sprite=spr_krisr_dark|image_index=2|direction=0|hspeed=4|"
        b"vspeed=0|speed=4|image_speed=0.2|camera_x=32|camera_y=16|"
        b"camera_width=320|camera_height=240|camera_angle=0|control=0|end"
    )

    sample = parse_packet(packet, received_at=10.0)

    assert sample is not None
    assert sample.version == 9
    assert sample.packet_sequence == 31
    assert sample.packet_parts == ("motion",)
    assert sample.camera_x == 32
    assert sample.camera_width == 320
    assert sample.player_controlled is True
    assert sample.player_origin_x == 120
    assert sample.player_origin_y == 42
    assert sample.player_foot_x is None
    assert sample.facing_direction == "right"


def test_v9_collision_and_render_fields_are_parsed_without_hidden_objects():
    collision = parse_packet(
        b"DRTEL|9|overworld|7|room_home|120|42|obj_mainchara|"
        b"part=collision|seq=31|instance_id=100001|bbox_left=112|"
        b"bbox_top=30|bbox_right=128|bbox_bottom=50|end",
        received_at=10.01,
    )
    render = parse_packet(
        b"DRTEL|9|overworld|7|room_home|120|42|obj_mainchara|"
        b"part=render|seq=31|depth=-100|image_xscale=1|image_yscale=1|"
        b"image_alpha=1|visible=1|sprite_width=40|sprite_height=56|"
        b"sprite_xoffset=20|sprite_yoffset=44|end",
        received_at=10.02,
    )

    assert collision is not None
    assert collision.player_instance_id == 100001
    assert collision.player_foot_x == 120
    assert collision.player_foot_y == 50
    assert collision.nearest_interactable_name is None
    assert render is not None
    assert render.visible is True
    assert render.sprite_width == 40
    assert render.sprite_yoffset == 44


def test_v9_bad_optional_values_do_not_discard_valid_core_fields():
    sample = parse_packet(
        b"DRTEL|9|overworld|7|room_home|120|42|obj_mainchara|"
        b"part=motion|seq=4|camera_x=oops|camera_width=nan|"
        b"camera_height=-1|control=not-a-number|future_field=accepted|end"
    )

    assert sample is not None
    assert sample.room_name == "room_home"
    assert sample.x == 120
    assert sample.camera_x is None
    assert sample.camera_width is None
    assert sample.camera_height is None
    assert sample.player_controlled is None


def test_same_sequence_layers_merge_without_mixing_later_frames():
    core = parse_packet(
        b"DRTEL|9|overworld|7|room_home|120|42|obj_mainchara|"
        b"part=core|seq=31|end",
        received_at=10.0,
    )
    motion = parse_packet(
        b"DRTEL|9|overworld|7|room_home|120|42|obj_mainchara|"
        b"part=motion|seq=31|sprite=spr_krisd|camera_x=0|camera_y=0|"
        b"camera_width=320|camera_height=240|control=0|end",
        received_at=10.01,
    )
    collision = parse_packet(
        b"DRTEL|9|overworld|7|room_home|120|42|obj_mainchara|"
        b"part=collision|seq=31|bbox_left=112|bbox_top=30|"
        b"bbox_right=128|bbox_bottom=50|end",
        received_at=10.02,
    )
    next_core = parse_packet(
        b"DRTEL|9|overworld|7|room_home|124|42|obj_mainchara|"
        b"part=core|seq=32|end",
        received_at=10.04,
    )

    assert core is not None
    assert motion is not None
    assert collision is not None
    assert next_core is not None
    merged = merge_samples(merge_samples(core, motion), collision)
    assert merged.packet_parts == ("core", "motion", "collision")
    assert merged.camera_width == 320
    assert merged.player_foot_x == 120

    next_sample = merge_samples(merged, next_core)
    assert next_sample.packet_sequence == 32
    assert next_sample.camera_width is None
    assert next_sample.sample_previous_x == 120
    assert next_sample.sample_delta_x == 4


def test_receiver_keeps_one_ordered_trace_sample_per_v9_sequence():
    receiver = TelemetryReceiver.__new__(TelemetryReceiver)
    receiver.socket = _PacketSocket(
        [
            b"DRTEL|9|overworld|7|room_home|120|42|obj_mainchara|"
            b"part=core|seq=31|end",
            b"DRTEL|9|overworld|7|room_home|120|42|obj_mainchara|"
            b"part=motion|seq=31|camera_x=0|camera_y=0|"
            b"camera_width=320|camera_height=240|control=0|end",
            b"DRTEL|9|overworld|7|room_home|120|42|obj_mainchara|"
            b"part=collision|seq=31|bbox_left=112|bbox_top=30|"
            b"bbox_right=128|bbox_bottom=50|end",
            b"DRTEL|9|overworld|7|room_home|124|42|obj_mainchara|"
            b"part=core|seq=32|end",
        ]
    )
    receiver.latest = None
    receiver.by_mode = {}

    sample = receiver.poll()
    trace = receiver.drain_overworld_trace()

    assert sample is not None and sample.packet_sequence == 32
    assert len(trace) == 2
    assert trace[0].packet_parts == ("core", "motion", "collision")
    assert trace[0].player_foot_x == 120
    assert trace[1].sample_delta_x == 4
    assert receiver.diagnostics() == {
        "received_packets": 4,
        "valid_packets": 4,
        "invalid_packets": 0,
        "unstable_room_packets": 0,
        "merged_layer_packets": 2,
        "out_of_order_packets": 0,
        "latest_version": 9,
        "latest_sequence": 32,
        "latest_parts": ["core"],
    }


def test_room_transition_records_the_last_observed_source_position_and_foot():
    receiver = TelemetryReceiver.__new__(TelemetryReceiver)
    receiver.socket = _PacketSocket(
        [
            b"DRTEL|9|overworld|5|room_torbathroom|300|120|obj_mainchara|"
            b"part=core|seq=80|end",
            b"DRTEL|9|overworld|5|room_torbathroom|300|120|obj_mainchara|"
            b"part=collision|seq=80|bbox_left=294|bbox_top=100|"
            b"bbox_right=306|bbox_bottom=126|end",
            b"DRTEL|9|overworld|6|room_torhouse|20|120|obj_mainchara|"
            b"part=core|seq=81|end",
        ]
    )
    receiver.latest = None
    receiver.by_mode = {}

    destination = receiver.poll()
    trace = receiver.drain_overworld_trace()

    assert destination is not None
    assert destination.room_name == "room_torhouse"
    assert destination.transition_from_room_name == "room_torbathroom"
    assert destination.transition_from_x == 300
    assert destination.transition_from_y == 120
    assert destination.transition_from_foot_x == 300
    assert destination.transition_from_foot_y == 126
    assert destination.transition_sequence == 81
    assert [item.room_name for item in trace] == [
        "room_torbathroom",
        "room_torhouse",
    ]


def test_out_of_order_v9_layers_cannot_replace_a_newer_sequence():
    receiver = TelemetryReceiver.__new__(TelemetryReceiver)
    receiver.socket = _PacketSocket(
        [
            b"DRTEL|9|overworld|7|room_home|124|42|obj_mainchara|"
            b"part=core|seq=32|end",
            b"DRTEL|9|overworld|7|room_home|120|42|obj_mainchara|"
            b"part=motion|seq=31|camera_width=320|camera_height=240|end",
        ]
    )
    receiver.latest = None
    receiver.by_mode = {}

    sample = receiver.poll()

    assert sample is not None
    assert sample.packet_sequence == 32
    assert sample.x == 124
    assert sample.camera_width is None
    assert receiver.out_of_order_packets == 1


def test_facing_uses_verified_kris_sprite_names_and_variants():
    assert facing_from_sprite("spr_krisd") == "down"
    assert facing_from_sprite("spr_krisl_bright") == "left"
    assert facing_from_sprite("spr_krisr_heart") == "right"
    assert facing_from_sprite("spr_krisu_dark") == "up"


def test_non_directional_kris_sprites_are_not_misclassified():
    assert facing_from_sprite("spr_kris_drop") is None
    assert facing_from_sprite("spr_kris_fall") is None
    assert facing_from_sprite("spr_krisb_idle") is None


def test_dialogue_sample_keeps_latest_player_position():
    receiver = TelemetryReceiver.__new__(TelemetryReceiver)
    receiver.socket = _PacketSocket(
        [
            b"DRTEL|2|overworld|7|room_home|120|42|obj_mainchara|end",
            b"DRTEL|2|dialogue|7|room_home|10|10|obj_writer|end",
        ]
    )
    receiver.latest = None
    receiver.by_mode = {}

    sample = receiver.poll()

    assert sample is not None and sample.mode == "dialogue"
    assert sample.x == 10
    assert sample.player_x == 120
    assert sample.player_y == 42


def test_v8_control_return_suppresses_lingering_dialogue_packet():
    receiver = TelemetryReceiver.__new__(TelemetryReceiver)
    receiver.socket = _PacketSocket(
        [
            b"DRTEL|8|overworld|7|room_home|120|42|obj_mainchara|540|240|"
            b"spr_krisd|2|0|0|0|0|0.2|32|16|320|240|0|end",
            b"DRTEL|8|dialogue|7|room_home|10|10|obj_writer|end",
        ]
    )
    receiver.latest = None
    receiver.by_mode = {}

    sample = receiver.poll()

    assert sample is not None
    assert sample.mode == "overworld"
    assert sample.player_controlled is True
    assert sample.x == 120


def test_blank_transition_room_is_ignored_and_ordered_room_trace_is_preserved():
    receiver = TelemetryReceiver.__new__(TelemetryReceiver)
    receiver.socket = _PacketSocket(
        [
            b"DRTEL|6|overworld|3|room_krishallway|289|112|obj_mainchara|end",
            b"DRTEL|6|overworld|0||0|0|obj_mainchara|end",
            b"DRTEL|6|overworld|4|room_thouse|20|112|obj_mainchara|end",
        ]
    )
    receiver.latest = None
    receiver.by_mode = {}

    sample = receiver.poll()
    trace = receiver.drain_overworld_trace()

    assert sample is not None and sample.room_name == "room_thouse"
    assert [item.room_name for item in trace] == [
        "room_krishallway",
        "room_thouse",
    ]


def test_blank_room_does_not_replace_last_stable_overworld_sample():
    receiver = TelemetryReceiver.__new__(TelemetryReceiver)
    receiver.socket = _PacketSocket(
        [b"DRTEL|6|overworld|3|room_krishallway|289|112|obj_mainchara|end"]
    )
    receiver.latest = None
    receiver.by_mode = {}
    stable = receiver.poll()
    receiver.socket = _PacketSocket(
        [b"DRTEL|6|overworld|0||0|0|obj_mainchara|end"]
    )

    during_transition = receiver.poll()

    assert stable is not None
    assert during_transition is stable
    assert during_transition.room_name == "room_krishallway"
