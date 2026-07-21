from dataclasses import asdict, dataclass, replace
import socket
import time

from .perception import GameState, Perception


MAGIC = b"DRTEL|"
SPECIALIZED_MAX_AGE = 0.20
KRIS_FACING_SPRITES = {
    "spr_krisd": "down",
    "spr_krisl": "left",
    "spr_krisr": "right",
    "spr_krisu": "up",
}


def facing_from_sprite(sprite_name: str | None) -> str | None:
    """Return Kris's facing from Chapter 1's verified overworld sprite names."""
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
    nearest_interactable_name: str | None = None
    nearest_interactable_id: int | None = None
    nearest_interactable_x: float | None = None
    nearest_interactable_y: float | None = None
    nearest_interactable_distance: float | None = None
    player_x: float | None = None
    player_y: float | None = None
    interaction_state: int | None = None
    player_controlled: bool | None = None

    def is_fresh(self, now: float | None = None, max_age: float = 1.0) -> bool:
        return (time.monotonic() if now is None else now) - self.received_at <= max_age

    def as_dict(self) -> dict[str, str | int | float | None]:
        data = asdict(self)
        data.pop("received_at")
        return data


def parse_packet(packet: bytes, received_at: float | None = None) -> TelemetrySample | None:
    # GameMaker prepends its own UDP header, so locate our marker within the datagram.
    start = packet.find(MAGIC)
    if start < 0:
        return None
    text = packet[start:].rstrip(b"\x00").decode("utf-8", errors="replace")
    fields = text.split("|")
    if len(fields) < 9 or fields[0] != "DRTEL" or fields[1] not in {"1", "2", "3", "4", "5", "6", "7", "8"}:
        return None
    try:
        version = int(fields[1])
        extra = {}
        has_extended_motion = (
            version in {3, 4}
            or (version == 5 and len(fields) >= 35)
            or (version >= 6 and len(fields) >= 18)
        )
        if has_extended_motion:
            if len(fields) < 18:
                return None
            sprite_name = fields[10] or None
            extra = {
                "room_width": float(fields[8]),
                "room_height": float(fields[9]),
                "sprite_name": sprite_name,
                "image_index": float(fields[11]),
                "facing_direction": facing_from_sprite(sprite_name),
                "direction": float(fields[12]),
                "hspeed": float(fields[13]),
                "vspeed": float(fields[14]),
                "speed": float(fields[15]),
                "image_speed": float(fields[16]),
            }
            if version >= 7:
                if len(fields) < 22:
                    return None
                extra.update(
                    {
                        "camera_x": float(fields[17]),
                        "camera_y": float(fields[18]),
                        "camera_width": float(fields[19]),
                        "camera_height": float(fields[20]),
                    }
                )
        has_rich_fields = (
            version in {4, 5, 6} and len(fields) >= 35
        ) or (version >= 7 and len(fields) >= 39)
        if has_rich_fields:
            offset = 4 if version >= 7 else 0
            extra.update(
                {
                    "instance_id": int(float(fields[17 + offset])),
                    "previous_x": float(fields[18 + offset]),
                    "previous_y": float(fields[19 + offset]),
                    "bbox_left": float(fields[20 + offset]),
                    "bbox_top": float(fields[21 + offset]),
                    "bbox_right": float(fields[22 + offset]),
                    "bbox_bottom": float(fields[23 + offset]),
                    "depth": float(fields[24 + offset]),
                    "image_xscale": float(fields[25 + offset]),
                    "image_yscale": float(fields[26 + offset]),
                    "room_speed": float(fields[27 + offset]),
                    "game_time_ms": float(fields[28 + offset]),
                    "nearest_interactable_name": fields[29 + offset] or None,
                    "nearest_interactable_id": int(float(fields[30 + offset])),
                    "nearest_interactable_x": float(fields[31 + offset]),
                    "nearest_interactable_y": float(fields[32 + offset]),
                    "nearest_interactable_distance": float(fields[33 + offset]),
                }
            )
        if version >= 8:
            # The control gate has its own short packet because collision fields
            # are not available in every Deltarune build. Keep accepting it from
            # the full rich packet as well for compatibility with existing v8.
            control_field = None
            if len(fields) >= 40:
                control_field = fields[38]
            elif len(fields) == 23:
                control_field = fields[21]
            if control_field is not None:
                interaction_state = int(float(control_field))
                extra.update(
                    {
                        "interaction_state": interaction_state,
                        "player_controlled": interaction_state == 0,
                    }
                )
        mode = fields[2]
        x = float(fields[5])
        y = float(fields[6])
        return TelemetrySample(
            mode=mode,
            room_id=int(float(fields[3])),
            room_name=fields[4],
            x=x,
            y=y,
            object_name=fields[7],
            received_at=time.monotonic() if received_at is None else received_at,
            version=version,
            player_x=x if mode == "overworld" else None,
            player_y=y if mode == "overworld" else None,
            **extra,
        )
    except ValueError:
        return None


class TelemetryReceiver:
    def __init__(self, port: int = 42069):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("127.0.0.1", port))
        self.socket.setblocking(False)
        self.latest: TelemetrySample | None = None
        self.by_mode: dict[str, TelemetrySample] = {}
        self.overworld_trace: list[TelemetrySample] = []

    def poll(self) -> TelemetrySample | None:
        self.overworld_trace = []
        while True:
            try:
                packet, _address = self.socket.recvfrom(4096)
            except BlockingIOError:
                break
            sample = parse_packet(packet)
            if sample is not None:
                if sample.mode == "overworld" and not has_stable_room_name(
                    sample.room_name
                ):
                    # GameMaker can emit a transient blank room during a warp.
                    # Keep the last stable sample instead of inventing an
                    # unknown room and two false transitions around it.
                    continue
                if sample.mode == "overworld":
                    self.overworld_trace.append(sample)
                self.latest = sample
                self.by_mode[sample.mode] = sample
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
            # they are destroyed. The v8 control gate is stronger evidence
            # that normal play has resumed, and prevents an extra confirm from
            # immediately interacting with the same object again. A battle
            # packet remains authoritative because its controller is separate.
            specialized = [sample for sample in specialized if sample.mode == "battle"]
        if specialized:
            selected = max(specialized, key=lambda sample: sample.received_at)
            if player is not None and player.is_fresh():
                replacements = {"player_x": player.x, "player_y": player.y}
                if not has_stable_room_name(selected.room_name):
                    replacements.update(
                        {"room_id": player.room_id, "room_name": player.room_name}
                    )
                selected = replace(selected, **replacements)
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
