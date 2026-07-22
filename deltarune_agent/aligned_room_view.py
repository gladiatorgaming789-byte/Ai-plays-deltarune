from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .room_view import RoomViewMemory, RoomViewTile
from .telemetry import TelemetrySample


PLAYER_RENDER_PADDING = 2.0


def player_render_box(
    telemetry: TelemetrySample,
) -> tuple[float, float, float, float] | None:
    """Return the player sprite's rendered world box when v9 data is present.

    Deltarune's collision box covers only Kris's feet and lower body. Using it
    as the room-view mask leaves most of the visible sprite baked into the
    remembered scenery. The render packet provides the sprite dimensions,
    origin, scale, and sprite origin needed to mask the full player image.
    """

    origin_x = (
        telemetry.player_origin_x
        if telemetry.player_origin_x is not None
        else telemetry.x
    )
    origin_y = (
        telemetry.player_origin_y
        if telemetry.player_origin_y is not None
        else telemetry.y
    )
    width = telemetry.sprite_width
    height = telemetry.sprite_height
    if width is None or height is None or width <= 0 or height <= 0:
        return None

    x_scale = telemetry.image_xscale if telemetry.image_xscale is not None else 1.0
    y_scale = telemetry.image_yscale if telemetry.image_yscale is not None else 1.0
    x_offset = telemetry.sprite_xoffset if telemetry.sprite_xoffset is not None else 0.0
    y_offset = telemetry.sprite_yoffset if telemetry.sprite_yoffset is not None else 0.0

    first_x = float(origin_x) - float(x_offset) * float(x_scale)
    first_y = float(origin_y) - float(y_offset) * float(y_scale)
    second_x = first_x + float(width) * float(x_scale)
    second_y = first_y + float(height) * float(y_scale)
    left, right = sorted((first_x, second_x))
    top, bottom = sorted((first_y, second_y))
    return (
        left - PLAYER_RENDER_PADDING,
        top - PLAYER_RENDER_PADDING,
        right + PLAYER_RENDER_PADDING,
        bottom + PLAYER_RENDER_PADDING,
    )


class AlignedRoomViewMemory(RoomViewMemory):
    """Room-view memory with full-sprite masking and exact room dimensions."""

    def __init__(self, root: Path):
        super().__init__(root)
        self.capture_failures = 0
        self.last_capture_error: str | None = None

    def capture(
        self,
        frame,
        telemetry: TelemetrySample,
        step: int,
    ) -> list[RoomViewTile]:
        render_box = player_render_box(telemetry)
        capture_sample = telemetry
        if render_box is not None:
            capture_sample = replace(
                telemetry,
                player_bbox_left=render_box[0],
                player_bbox_top=render_box[1],
                player_bbox_right=render_box[2],
                player_bbox_bottom=render_box[3],
            )

        # A partial camera rectangle can occasionally round to an invalid Pillow
        # tile at a room edge. Scene memory is useful evidence, but it must never
        # be able to terminate the controller. Skip only the malformed capture
        # and expose the error through the run summary for later diagnosis.
        try:
            changed = super().capture(frame, capture_sample, step)
            self.last_capture_error = None
        except (OSError, ValueError, SystemError) as exc:
            self.capture_failures += 1
            self.last_capture_error = f"{type(exc).__name__}: {exc}"
            changed = []

        room = telemetry.room_name or str(telemetry.room_id)
        rooms = self._rooms()
        room_data = rooms.get(room)
        dimensions_changed = False
        if isinstance(room_data, dict):
            for key, value in (
                ("room_width", telemetry.room_width),
                ("room_height", telemetry.room_height),
            ):
                if value is None or float(value) <= 0:
                    continue
                rounded = round(float(value), 3)
                if room_data.get(key) != rounded:
                    room_data[key] = rounded
                    dimensions_changed = True
            # The room coordinate origin is currently supplied by GameMaker as
            # (0, 0). Store it explicitly so exporters do not infer an origin
            # from whichever 32-pixel camera tile happened to be seen first.
            if room_data.get("origin_world") != [0.0, 0.0]:
                room_data["origin_world"] = [0.0, 0.0]
                dimensions_changed = True
        if dimensions_changed:
            self._save_index()
        return changed
