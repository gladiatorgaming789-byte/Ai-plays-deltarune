from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
import math
import socket
import time
from typing import Any, Callable

from .perception import GameState, Perception


MAGIC = b"DRTEL|"
SPEED_MAGIC = b"DRSPEED|"
PROTOCOL_VERSION = 9
SPEED_PROTOCOL_VERSION = 1
SPECIALIZED_MAX_AGE = 0.20
LEGACY_LAYER_MERGE_AGE = 0.30
SESSION_RESTART_AGE = 1.0

KRIS_FACING_SPRITES = {
    "spr_krisd": "down",
    "spr_krisl": "left",
    "spr_krisr": "right",
    "spr_krisu": "up",
}

V9_PARTS = {
    "core",
    "motion",
    "collision",
    "render",
    "timing",
}


@dataclass(frozen=True)
class SpeedSample:
    multiplier: float
    base_fps: float
    target_fps: float
    received_at: float
    version: int = SPEED_PROTOCOL_VERSION
    agent_id: str | None = None

    def is_fresh(self, now: float | None = None, max_age: float = 2.0) -> bool:
        return (time.monotonic() if now is None else now) - self.received_at <= max_age

    def as_dict(self, now: float | None = None) -> dict[str, Any]:
        return {
            "version": self.version,
            "multiplier": self.multiplier,
            "base_fps": self.base_fps,
            "target_fps": self.target_fps,
            "agent_id": self.agent_id,
            "packet_age_seconds": max(
                0.0,
                (time.monotonic() if now is None else now) - self.received_at,
            ),
        }


def facing_from_sprite(sprite_name: str | None) -> str | None:
    """Return Kris's facing from the verified overworld sprite families."""
    if not sprite_name:
        return None
    for prefix, facing in KRIS_FACING_SPRITES.items():
        if sprite_name == prefix or sprite_name.startswith(prefix + "_"):
            return facing
    return None


def has_stable_room_name(room_name: str | None) -> bool:
    return bool(room_name and room_name.strip() and room_name.casefold() != "unknown")


@dataclass(frozen=True)
class TelemetrySample:
    mode: str
    room_id: int
    room_name: str
    x: float
    y: float
    object_name: str
    received_at: float
    version: int = 1
    room_width: float | None = None
    room_height: float | None = None
    camera_x: float | None = None
    camera_y: float | None = None
    camera_width: float | None = None
    camera_height: float | None = None
    sprite_name: str | None = None
    image_index: float | None = None
    facing_direction: str | None = None
    # GameMaker's built-in motion direction. Deltarune does not use this as
    # Kris's overworld facing direction, so consumers should use facing_direction.
    direction: float | None = None
    hspeed: float | None = None
    vspeed: float | None = None
    speed: float | None = None
    image_speed: float | None = None
    instance_id: int | None = None
    previous_x: float | None = None
    previous_y: float | None = None
    bbox_left: float | None = None
    bbox_top: float | None = None
    bbox_right: float | None = None
    bbox_bottom: float | None = None
    depth: float | None = None
    image_xscale: float | None = None
    image_yscale: float | None = None
    room_speed: float | None = None
    game_time_ms: float | None = None
    # Retained only to read old v4-v7 recordings. Telemetry v8+ deliberately
    # leaves these blank, and the decision policy does not consume them.
    nearest_interactable_name: str | None = None
    nearest_interactable_id: int | None = None
    nearest_interactable_x: float | None = None
    nearest_interactable_y: float | None = None
    nearest_interactable_distance: float | None = None
    player_x: float | None = None
    player_y: float | None = None
    interaction_state: int | None = None
    player_controlled: bool | None = None
    # v9 additions are appended so positional construction of older samples
    # keeps the v1-v8 dataclass field order.
    packet_sequence: int | None = None
    packet_parts: tuple[str, ...] = ()
    camera_angle: float | None = None
    image_alpha: float | None = None
    visible: bool | None = None
    sprite_width: float | None = None
    sprite_height: float | None = None
    sprite_xoffset: float | None = None
    sprite_yoffset: float | None = None
    fps: float | None = None
    player_instance_id: int | None = None
    player_origin_x: float | None = None
    player_origin_y: float | None = None
    player_foot_x: float | None = None
    player_foot_y: float | None = None
    player_sprite_name: str | None = None
    player_facing_direction: str | None = None
    player_bbox_left: float | None = None
    player_bbox_top: float | None = None
    player_bbox_right: float | None = None
    player_bbox_bottom: float | None = None
    sample_previous_x: float | None = None
    sample_previous_y: float | None = None
    sample_delta_x: float | None = None
    sample_delta_y: float | None = None
    sample_interval_ms: float | None = None
    transition_from_room_id: int | None = None
    transition_from_room_name: str | None = None
    transition_from_x: float | None = None
    transition_from_y: float | None = None
    transition_from_foot_x: float | None = None
    transition_from_foot_y: float | None = None
    transition_from_facing: str | None = None
    transition_sequence: int | None = None
    coordinate_space: str = "room_pixels"
    position_kind: str = "instance_origin"
    agent_id: str | None = None

    def is_fresh(self, now: float | None = None, max_age: float = 1.0) -> bool:
        return (time.monotonic() if now is None else now) - self.received_at <= max_age

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("received_at")
        return data


_BASE_FIELDS = {
    "mode",
    "room_id",
    "room_name",
    "x",
    "y",
    "object_name",
    "received_at",
    "version",
    "packet_sequence",
    "packet_parts",
}
_MERGEABLE_FIELDS = tuple(
    field.name for field in fields(TelemetrySample) if field.name not in _BASE_FIELDS
)


def _optional_value(
    values: dict[str, str],
    key: str,
    converter: Callable[[str], Any],
) -> Any | None:
    value = values.get(key)
    if value is None or value == "":
        return None
    try:
        return converter(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _optional_float(values: dict[str, str], key: str) -> float | None:
    value = _optional_value(values, key, float)
    return value if value is not None and math.isfinite(value) else None


def _optional_int(values: dict[str, str], key: str) -> int | None:
    return _optional_value(values, key, lambda value: int(float(value)))


def _positive_float(values: dict[str, str], key: str) -> float | None:
    value = _optional_float(values, key)
    return value if value is not None and value > 0 else None


def _valid_bbox(extra: dict[str, Any]) -> None:
    left = extra.get("bbox_left")
    top = extra.get("bbox_top")
    right = extra.get("bbox_right")
    bottom = extra.get("bbox_bottom")
    if left is not None and right is not None and right < left:
        extra["bbox_left"] = None
        extra["bbox_right"] = None
    if top is not None and bottom is not None and bottom < top:
        extra["bbox_top"] = None
        extra["bbox_bottom"] = None


def _legacy_packet_parts(
    *,
    has_motion: bool,
    has_rich: bool,
    has_control: bool,
) -> tuple[str, ...]:
    parts = ["core"]
    if has_motion:
        parts.append("motion")
    if has_control:
        parts.append("control")
    if has_rich:
        parts.extend(("collision", "render", "timing"))
    return tuple(parts)


def _parse_legacy_fields(
    fields_: list[str],
    version: int,
) -> dict[str, Any] | None:
    extra: dict[str, Any] = {}
    has_extended_motion = (
        version in {3, 4}
        or (version == 5 and len(fields_) >= 35)
        or (version >= 6 and len(fields_) >= 18)
    )
    if has_extended_motion:
        if len(fields_) < 18:
            return None
        sprite_name = fields_[10] or None
        try:
            extra.update(
                {
                    "room_width": float(fields_[8]),
                    "room_height": float(fields_[9]),
                    "sprite_name": sprite_name,
                    "image_index": float(fields_[11]),
                    "facing_direction": facing_from_sprite(sprite_name),
                    "direction": float(fields_[12]),
                    "hspeed": float(fields_[13]),
                    "vspeed": float(fields_[14]),
                    "speed": float(fields_[15]),
                    "image_speed": float(fields_[16]),
                }
            )
            if version >= 7:
                if len(fields_) < 22:
                    return None
                extra.update(
                    {
                        "camera_x": float(fields_[17]),
                        "camera_y": float(fields_[18]),
                        "camera_width": float(fields_[19]),
                        "camera_height": float(fields_[20]),
                    }
                )
        except ValueError:
            return None

    has_rich_fields = (
        version in {4, 5, 6} and len(fields_) >= 35
    ) or (version >= 7 and len(fields_) >= 39)
    if has_rich_fields:
        offset = 4 if version >= 7 else 0
        try:
            extra.update(
                {
                    "instance_id": int(float(fields_[17 + offset])),
                    "previous_x": float(fields_[18 + offset]),
                    "previous_y": float(fields_[19 + offset]),
                    "bbox_left": float(fields_[20 + offset]),
                    "bbox_top": float(fields_[21 + offset]),
                    "bbox_right": float(fields_[22 + offset]),
                    "bbox_bottom": float(fields_[23 + offset]),
                    "depth": float(fields_[24 + offset]),
                    "image_xscale": float(fields_[25 + offset]),
                    "image_yscale": float(fields_[26 + offset]),
                    "room_speed": float(fields_[27 + offset]),
                    "game_time_ms": float(fields_[28 + offset]),
                    "nearest_interactable_name": fields_[29 + offset] or None,
                    "nearest_interactable_id": int(float(fields_[30 + offset])),
                    "nearest_interactable_x": float(fields_[31 + offset]),
                    "nearest_interactable_y": float(fields_[32 + offset]),
                    "nearest_interactable_distance": float(fields_[33 + offset]),
                }
            )
        except ValueError:
            return None

    interaction_state = None
    if version >= 8:
        # v8 sent control either as the last rich field or as a short packet.
        control_field = None
        if len(fields_) >= 40:
            control_field = fields_[38]
        elif len(fields_) == 23:
            control_field = fields_[21]
        if control_field is not None:
            try:
                interaction_state = int(float(control_field))
            except ValueError:
                return None
            extra.update(
                {
                    "interaction_state": interaction_state,
                    "player_controlled": interaction_state == 0,
                }
            )

    _valid_bbox(extra)
    extra["packet_parts"] = _legacy_packet_parts(
        has_motion=has_extended_motion,
        has_rich=has_rich_fields,
        has_control=interaction_state is not None,
    )
    return extra


def _parse_v9_fields(fields_: list[str]) -> dict[str, Any]:
    values: dict[str, str] = {}
    for token in fields_[8:]:
        if token == "end":
            break
        key, separator, value = token.partition("=")
        if separator and key:
            values[key] = value

    part = values.get("part", "core")
    packet_parts = (part,) if part in V9_PARTS else ("unknown",)
    sprite_name = values.get("sprite") or None
    interaction_state = _optional_int(values, "control")
    visible_value = _optional_int(values, "visible")
    packet_sequence = _optional_int(values, "seq")
    if packet_sequence is not None and packet_sequence < 0:
        packet_sequence = None
    extra: dict[str, Any] = {
        "packet_sequence": packet_sequence,
        "packet_parts": packet_parts,
        "room_width": _positive_float(values, "room_width"),
        "room_height": _positive_float(values, "room_height"),
        "camera_x": _optional_float(values, "camera_x"),
        "camera_y": _optional_float(values, "camera_y"),
        "camera_width": _positive_float(values, "camera_width"),
        "camera_height": _positive_float(values, "camera_height"),
        "camera_angle": _optional_float(values, "camera_angle"),
        "sprite_name": sprite_name,
        "image_index": _optional_float(values, "image_index"),
        "facing_direction": facing_from_sprite(sprite_name),
        "direction": _optional_float(values, "direction"),
        "hspeed": _optional_float(values, "hspeed"),
        "vspeed": _optional_float(values, "vspeed"),
        "speed": _optional_float(values, "speed"),
        "image_speed": _optional_float(values, "image_speed"),
        "instance_id": _optional_int(values, "instance_id"),
        "previous_x": _optional_float(values, "previous_x"),
        "previous_y": _optional_float(values, "previous_y"),
        "bbox_left": _optional_float(values, "bbox_left"),
        "bbox_top": _optional_float(values, "bbox_top"),
        "bbox_right": _optional_float(values, "bbox_right"),
        "bbox_bottom": _optional_float(values, "bbox_bottom"),
        "depth": _optional_float(values, "depth"),
        "image_xscale": _optional_float(values, "image_xscale"),
        "image_yscale": _optional_float(values, "image_yscale"),
        "image_alpha": _optional_float(values, "image_alpha"),
        "visible": None if visible_value is None else bool(visible_value),
        "sprite_width": _positive_float(values, "sprite_width"),
        "sprite_height": _positive_float(values, "sprite_height"),
        "sprite_xoffset": _optional_float(values, "sprite_xoffset"),
        "sprite_yoffset": _optional_float(values, "sprite_yoffset"),
        "room_speed": _positive_float(values, "room_speed"),
        "game_time_ms": _optional_float(values, "game_time_ms"),
        "fps": _positive_float(values, "fps"),
        "interaction_state": interaction_state,
        "player_controlled": (
            None if interaction_state is None else interaction_state == 0
        ),
        "agent_id": values.get("agent") or None,
    }
    _valid_bbox(extra)
    return extra


def _with_player_fields(sample: TelemetrySample) -> TelemetrySample:
    if sample.mode != "overworld":
        return sample
    foot_x = None
    if sample.bbox_left is not None and sample.bbox_right is not None:
        foot_x = (sample.bbox_left + sample.bbox_right) / 2.0
    foot_y = sample.bbox_bottom
    return replace(
        sample,
        player_x=sample.x,
        player_y=sample.y,
        player_instance_id=sample.instance_id,
        player_origin_x=sample.x,
        player_origin_y=sample.y,
        player_foot_x=foot_x,
        player_foot_y=foot_y,
        player_sprite_name=sample.sprite_name,
        player_facing_direction=sample.facing_direction,
        player_bbox_left=sample.bbox_left,
        player_bbox_top=sample.bbox_top,
        player_bbox_right=sample.bbox_right,
        player_bbox_bottom=sample.bbox_bottom,
    )


def decision_safe_sample(sample: TelemetrySample) -> TelemetrySample:
    """Remove legacy hidden-world fields before a sample reaches the policy."""
    if (
        sample.nearest_interactable_name is None
        and sample.nearest_interactable_id is None
        and sample.nearest_interactable_x is None
        and sample.nearest_interactable_y is None
        and sample.nearest_interactable_distance is None
    ):
        return sample
    return replace(
        sample,
        nearest_interactable_name=None,
        nearest_interactable_id=None,
        nearest_interactable_x=None,
        nearest_interactable_y=None,
        nearest_interactable_distance=None,
    )


def parse_packet(packet: bytes, received_at: float | None = None) -> TelemetrySample | None:
    """Parse one DRTEL datagram without letting bad optional fields erase its core."""
    # GameMaker prepends its own UDP header, so locate our marker within the datagram.
    start = packet.find(MAGIC)
    if start < 0:
        return None
    text = packet[start:].rstrip(b"\x00").decode("utf-8", errors="replace")
    fields_ = text.split("|")
    if len(fields_) < 9 or fields_[0] != "DRTEL":
        return None
    try:
        version = int(fields_[1])
    except ValueError:
        return None
    if not 1 <= version <= PROTOCOL_VERSION:
        return None

    try:
        mode = fields_[2]
        room_id = int(float(fields_[3]))
        room_name = fields_[4]
        x = float(fields_[5])
        y = float(fields_[6])
        object_name = fields_[7]
    except (ValueError, IndexError):
        return None
    if not mode or not object_name or not math.isfinite(x) or not math.isfinite(y):
        return None

    extra = (
        _parse_v9_fields(fields_)
        if version >= 9
        else _parse_legacy_fields(fields_, version)
    )
    if extra is None:
        return None
    sample = TelemetrySample(
        mode=mode,
        room_id=room_id,
        room_name=room_name,
        x=x,
        y=y,
        object_name=object_name,
        received_at=time.monotonic() if received_at is None else received_at,
        version=version,
        **extra,
    )
    return _with_player_fields(sample)


def parse_speed_packet(
    packet: bytes,
    received_at: float | None = None,
) -> SpeedSample | None:
    """Parse one independently broadcast DRSPEED announcement."""
    start = packet.find(SPEED_MAGIC)
    if start < 0:
        return None
    text = packet[start:].rstrip(b"\x00").decode("utf-8", errors="replace")
    fields_ = text.split("|")
    if len(fields_) < 6 or fields_[0] != "DRSPEED":
        return None
    try:
        version = int(fields_[1])
    except ValueError:
        return None
    if version != SPEED_PROTOCOL_VERSION:
        return None

    values: dict[str, str] = {}
    for field in fields_[2:]:
        if field == "end":
            break
        key, separator, value = field.partition("=")
        if separator and key:
            values[key] = value
    try:
        multiplier = float(values["multiplier"])
        base_fps = float(values["base_fps"])
        target_fps = float(values["target_fps"])
    except (KeyError, ValueError, OverflowError):
        return None
    if (
        not all(math.isfinite(value) for value in (multiplier, base_fps, target_fps))
        or not 1 <= multiplier <= 10
        or base_fps <= 0
        or target_fps <= 0
        or abs(target_fps - base_fps * multiplier) > max(1.0, base_fps * 0.05)
    ):
        return None
    return SpeedSample(
        multiplier=multiplier,
        base_fps=base_fps,
        target_fps=target_fps,
        received_at=time.monotonic() if received_at is None else received_at,
        version=version,
        agent_id=values.get("agent") or None,
    )


def _same_sample_generation(
    previous: TelemetrySample,
    current: TelemetrySample,
) -> bool:
    if (
        previous.mode != current.mode
        or previous.object_name != current.object_name
    ):
        return False
    if (
        has_stable_room_name(previous.room_name)
        and has_stable_room_name(current.room_name)
        and previous.room_name != current.room_name
    ):
        return False
    age = current.received_at - previous.received_at
    if age < 0:
        return False
    if previous.packet_sequence is not None and current.packet_sequence is not None:
        return previous.packet_sequence == current.packet_sequence
    return age <= LEGACY_LAYER_MERGE_AGE


def merge_samples(
    previous: TelemetrySample | None,
    current: TelemetrySample,
) -> TelemetrySample:
    """Merge packet layers from one snapshot, preferring newly supplied fields."""
    if previous is None:
        return current
    if not _same_sample_generation(previous, current):
        same_room = (
            previous.mode == current.mode
            and previous.room_id == current.room_id
            and (
                previous.room_name == current.room_name
                or not has_stable_room_name(previous.room_name)
                or not has_stable_room_name(current.room_name)
            )
        )
        interval = current.received_at - previous.received_at
        if same_room and 0 <= interval <= SESSION_RESTART_AGE:
            return replace(
                current,
                sample_previous_x=previous.x,
                sample_previous_y=previous.y,
                sample_delta_x=current.x - previous.x,
                sample_delta_y=current.y - previous.y,
                sample_interval_ms=interval * 1000.0,
            )
        return current
    replacements: dict[str, Any] = {}
    for name in _MERGEABLE_FIELDS:
        current_value = getattr(current, name)
        if current_value is None:
            replacements[name] = getattr(previous, name)
    replacements["packet_parts"] = tuple(
        dict.fromkeys((*previous.packet_parts, *current.packet_parts))
    )
    merged = replace(current, **replacements)
    return _with_player_fields(merged)


def _is_older_sequence(
    previous: TelemetrySample | None,
    current: TelemetrySample,
) -> bool:
    if (
        previous is None
        or previous.version < 9
        or current.version < 9
        or previous.packet_sequence is None
        or current.packet_sequence is None
        or previous.mode != current.mode
        or previous.object_name != current.object_name
    ):
        return False
    if current.received_at - previous.received_at > SESSION_RESTART_AGE:
        return False
    return current.packet_sequence < previous.packet_sequence


def _same_trace_snapshot(
    previous: TelemetrySample,
    raw: TelemetrySample,
) -> bool:
    if raw.version >= 9:
        return _same_sample_generation(previous, raw)
    # Legacy senders mark the start of each 10 Hz burst with a core-only packet.
    return raw.packet_parts != ("core",) and _same_sample_generation(previous, raw)


def _player_context(
    state: TelemetrySample,
    player: TelemetrySample,
) -> TelemetrySample:
    replacements: dict[str, Any] = {
        "player_x": player.player_x if player.player_x is not None else player.x,
        "player_y": player.player_y if player.player_y is not None else player.y,
        "player_instance_id": player.player_instance_id,
        "player_origin_x": (
            player.player_origin_x
            if player.player_origin_x is not None
            else player.x
        ),
        "player_origin_y": (
            player.player_origin_y
            if player.player_origin_y is not None
            else player.y
        ),
        "player_foot_x": player.player_foot_x,
        "player_foot_y": player.player_foot_y,
        "player_sprite_name": player.player_sprite_name,
        "player_facing_direction": player.player_facing_direction,
        "player_bbox_left": player.player_bbox_left,
        "player_bbox_top": player.player_bbox_top,
        "player_bbox_right": player.player_bbox_right,
        "player_bbox_bottom": player.player_bbox_bottom,
        "interaction_state": player.interaction_state,
        "player_controlled": player.player_controlled,
    }
    if not has_stable_room_name(state.room_name):
        replacements.update(
            {"room_id": player.room_id, "room_name": player.room_name}
        )
    for name in (
        "room_width",
        "room_height",
        "camera_x",
        "camera_y",
        "camera_width",
        "camera_height",
        "camera_angle",
    ):
        if getattr(player, name) is not None:
            replacements[name] = getattr(player, name)
    return replace(state, **replacements)


def _with_transition_source(
    previous: TelemetrySample | None,
    current: TelemetrySample,
) -> TelemetrySample:
    if (
        previous is None
        or current.mode != "overworld"
        or previous.mode != "overworld"
        or not has_stable_room_name(previous.room_name)
        or not has_stable_room_name(current.room_name)
        or previous.room_name == current.room_name
        or not previous.is_fresh(
            now=current.received_at,
            max_age=SESSION_RESTART_AGE,
        )
    ):
        return current
    return replace(
        current,
        transition_from_room_id=previous.room_id,
        transition_from_room_name=previous.room_name,
        transition_from_x=previous.x,
        transition_from_y=previous.y,
        transition_from_foot_x=previous.player_foot_x,
        transition_from_foot_y=previous.player_foot_y,
        transition_from_facing=previous.player_facing_direction,
        transition_sequence=current.packet_sequence,
    )


class TelemetryReceiver:
    def __init__(self, port: int = 42069):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("127.0.0.1", port))
        self.socket.setblocking(False)
        self.latest: TelemetrySample | None = None
        self.latest_speed: SpeedSample | None = None
        self.by_mode: dict[str, TelemetrySample] = {}
        self.overworld_trace: list[TelemetrySample] = []
        self.received_packets = 0
        self.valid_packets = 0
        self.invalid_packets = 0
        self.unstable_room_packets = 0
        self.merged_layer_packets = 0
        self.out_of_order_packets = 0
        self.speed_packets = 0

    def poll(self) -> TelemetrySample | None:
        self.overworld_trace = []
        while True:
            try:
                packet, _address = self.socket.recvfrom(4096)
            except BlockingIOError:
                break
            self.received_packets = getattr(self, "received_packets", 0) + 1
            speed = parse_speed_packet(packet)
            if speed is not None:
                self.latest_speed = speed
                self.speed_packets = getattr(self, "speed_packets", 0) + 1
                continue
            raw = parse_packet(packet)
            if raw is None:
                self.invalid_packets = getattr(self, "invalid_packets", 0) + 1
                continue
            self.valid_packets = getattr(self, "valid_packets", 0) + 1
            raw = decision_safe_sample(raw)
            if raw.mode == "overworld" and not has_stable_room_name(raw.room_name):
                # GameMaker can emit a transient blank room during a warp.
                # Keep the last stable sample instead of inventing an unknown
                # room and two false transitions around it.
                self.unstable_room_packets = (
                    getattr(self, "unstable_room_packets", 0) + 1
                )
                continue
            previous = self.by_mode.get(raw.mode)
            raw = _with_transition_source(previous, raw)
            if _is_older_sequence(previous, raw):
                self.out_of_order_packets = (
                    getattr(self, "out_of_order_packets", 0) + 1
                )
                continue
            if previous is not None and _same_sample_generation(previous, raw):
                self.merged_layer_packets = (
                    getattr(self, "merged_layer_packets", 0) + 1
                )
            sample = merge_samples(previous, raw)
            self.by_mode[raw.mode] = sample
            self.latest = sample
            if raw.mode == "overworld":
                if self.overworld_trace and _same_trace_snapshot(
                    self.overworld_trace[-1], raw
                ):
                    self.overworld_trace[-1] = sample
                else:
                    self.overworld_trace.append(sample)

        # Specialized objects can coexist with obj_mainchara. Use the newest
        # active one so a just-opened dialogue supersedes a recently-ended battle.
        specialized = [
            sample
            for mode in ("battle", "choice", "dialogue")
            if (sample := self.by_mode.get(mode)) is not None
            and sample.is_fresh(max_age=SPECIALIZED_MAX_AGE)
        ]
        player = self.by_mode.get("overworld")
        controlled_overworld = (
            player is not None
            and player.is_fresh()
            and player.player_controlled is True
        )
        if controlled_overworld:
            # Dialogue and choice objects can leave one last packet behind as
            # they are destroyed. The verified control gate is stronger evidence
            # that normal play has resumed. A battle packet remains authoritative.
            specialized = [sample for sample in specialized if sample.mode == "battle"]
        if specialized:
            selected = max(specialized, key=lambda sample: sample.received_at)
            if player is not None and player.is_fresh():
                selected = _player_context(selected, player)
            return selected
        if controlled_overworld:
            return player
        if self.latest is not None and self.latest.is_fresh():
            return self.latest
        return None

    def drain_overworld_trace(self) -> list[TelemetrySample]:
        trace = self.overworld_trace
        self.overworld_trace = []
        return trace

    def diagnostics(self) -> dict[str, Any]:
        latest = getattr(self, "latest", None)
        return {
            "received_packets": getattr(self, "received_packets", 0),
            "valid_packets": getattr(self, "valid_packets", 0),
            "invalid_packets": getattr(self, "invalid_packets", 0),
            "unstable_room_packets": getattr(
                self,
                "unstable_room_packets",
                0,
            ),
            "merged_layer_packets": getattr(
                self,
                "merged_layer_packets",
                0,
            ),
            "out_of_order_packets": getattr(
                self,
                "out_of_order_packets",
                0,
            ),
            "speed_packets": getattr(self, "speed_packets", 0),
            "latest_speed": (
                self.latest_speed.as_dict()
                if getattr(self, "latest_speed", None) is not None
                else None
            ),
            "latest_version": latest.version if latest is not None else None,
            "latest_sequence": (
                latest.packet_sequence if latest is not None else None
            ),
            "latest_parts": list(latest.packet_parts) if latest is not None else [],
        }

    def close(self) -> None:
        self.socket.close()


def fuse_perception(visual: Perception, telemetry: TelemetrySample | None) -> Perception:
    if telemetry is None:
        return visual
    if telemetry.mode == "battle":
        return replace(visual, state=GameState.BATTLE, confidence=0.99, source="telemetry")
    if telemetry.mode == "choice":
        return replace(visual, state=GameState.MENU, confidence=0.99, source="telemetry")
    if telemetry.mode == "dialogue":
        return replace(visual, state=GameState.DIALOGUE, confidence=0.99, source="telemetry")
    if telemetry.mode == "overworld" and telemetry.version >= 2:
        return replace(
            visual,
            state=GameState.OVERWORLD,
            confidence=0.99,
            source="telemetry",
        )
    if telemetry.mode == "overworld" and visual.state is GameState.BATTLE:
        return replace(visual, state=GameState.OVERWORLD, confidence=0.99, source="telemetry")
    return replace(visual, source="visual+telemetry")
