from deltarune_agent.perception import GameState, Perception, VisualFeatures
from deltarune_agent.telemetry import (
    TelemetryReceiver,
    facing_from_sprite,
    fuse_perception,
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
