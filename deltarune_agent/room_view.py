from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import ceil, floor
import json
from pathlib import Path
import re

from PIL import Image, ImageChops

from .screen_regions import REGION_PIXELS
from .telemetry import TelemetrySample


INDEX_VERSION = 2
PIXELS_PER_WORLD = 4
TILE_PIXELS = REGION_PIXELS * PIXELS_PER_WORLD


def room_view_image_is_usable(image: Image.Image) -> bool:
    """Reject white captures, including white strips in partial RGBA tiles."""
    sample = image.convert("RGBA").resize((24, 24), Image.Resampling.BILINEAR)
    visible = [
        (red, green, blue)
        for red, green, blue, alpha in sample.getdata()
        if alpha >= 32
    ]
    if not visible:
        return False
    white_ratio = sum(min(pixel) >= 245 for pixel in visible) / len(visible)
    return white_ratio < 0.95


def camera_viewport_box(
    image_size: tuple[int, int],
    camera_size: tuple[float, float],
) -> tuple[int, int, int, int] | None:
    """Find the centered screenshot area matching the camera aspect ratio."""
    image_width, image_height = image_size
    camera_width, camera_height = camera_size
    if (
        image_width <= 0
        or image_height <= 0
        or camera_width <= 0
        or camera_height <= 0
    ):
        return None
    image_ratio = image_width / image_height
    camera_ratio = camera_width / camera_height
    if abs(image_ratio - camera_ratio) <= 0.002:
        return (0, 0, image_width, image_height)
    if image_ratio > camera_ratio:
        viewport_width = max(1, round(image_height * camera_ratio))
        left = (image_width - viewport_width) // 2
        return (left, 0, left + viewport_width, image_height)
    viewport_height = max(1, round(image_width / camera_ratio))
    top = (image_height - viewport_height) // 2
    return (0, top, image_width, top + viewport_height)


def camera_region_coordinates(
    camera_x: float,
    camera_y: float,
    camera_width: float,
    camera_height: float,
) -> set[tuple[int, int]]:
    """Return only regions intersected by the camera, without inferred bounds."""
    if camera_width <= 0 or camera_height <= 0:
        return set()
    last_x = floor((camera_x + camera_width - 1e-6) / REGION_PIXELS)
    last_y = floor((camera_y + camera_height - 1e-6) / REGION_PIXELS)
    return {
        (region_x, region_y)
        for region_x in range(floor(camera_x / REGION_PIXELS), last_x + 1)
        for region_y in range(floor(camera_y / REGION_PIXELS), last_y + 1)
    }


def _room_directory_name(room: str) -> str:
    readable = re.sub(r"[^A-Za-z0-9_.-]+", "_", room).strip("._") or "room"
    # Room names from telemetry are normally unique and filesystem-safe. Keep a
    # deterministic suffix so unusual names cannot collide after sanitization.
    suffix = sha256(room.encode("utf-8")).hexdigest()[:8]
    return f"{readable}-{suffix}"


@dataclass(frozen=True)
class RoomViewTile:
    room: str
    region_x: int
    region_y: int
    path: Path
    coverage: float
    last_step: int

    def as_map_update(self) -> dict[str, object]:
        return {
            "type": "room_view_tile",
            "room": self.room,
            "region": [self.region_x, self.region_y],
            "path": str(self.path.resolve()),
            "coverage": round(self.coverage, 3),
            "pixels_per_world": PIXELS_PER_WORLD,
            "last_step": self.last_step,
        }


class RoomViewMemory:
    """Persistent pixels from camera frames the agent has actually observed."""

    def __init__(self, root: Path):
        self.root = root
        self.index_path = root / "index.json"
        self.data: dict[str, object] = {
            "version": INDEX_VERSION,
            "region_pixels": REGION_PIXELS,
            "pixels_per_world": PIXELS_PER_WORLD,
            "tile_pixels": TILE_PIXELS,
            "rooms": {},
        }
        self.load_warning: str | None = None
        self._load()

    def _load(self) -> None:
        if not self.index_path.exists():
            return
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            version = int(data.get("version", 0))
            if version not in {1, INDEX_VERSION}:
                raise ValueError("unsupported room-view index version")
            if int(data.get("region_pixels", 0)) != REGION_PIXELS:
                raise ValueError("unsupported room-view tile size")
            if not isinstance(data.get("rooms"), dict):
                raise ValueError("room-view rooms must be an object")
            data["version"] = INDEX_VERSION
            data["pixels_per_world"] = PIXELS_PER_WORLD
            data["tile_pixels"] = TILE_PIXELS
            self.data = data
            removed_bad_tiles = False
            for room_data in data["rooms"].values():
                if not isinstance(room_data, dict):
                    continue
                tiles = room_data.get("tiles")
                if not isinstance(tiles, dict):
                    continue
                for tile_key, tile in list(tiles.items()):
                    if not isinstance(tile, dict):
                        tiles.pop(tile_key, None)
                        removed_bad_tiles = True
                        continue
                    path = self.root / str(tile.get("path") or "")
                    try:
                        with Image.open(path) as stored:
                            usable = room_view_image_is_usable(stored)
                    except OSError:
                        usable = False
                    if not usable:
                        tiles.pop(tile_key, None)
                        if path.is_file():
                            path.unlink(missing_ok=True)
                        removed_bad_tiles = True
            if removed_bad_tiles:
                self._save_index()
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            self.load_warning = f"Could not load {self.index_path}: {exc}."

    def _rooms(self) -> dict[str, dict[str, object]]:
        rooms = self.data.setdefault("rooms", {})
        assert isinstance(rooms, dict)
        return rooms

    def capture(
        self,
        frame: Image.Image,
        telemetry: TelemetrySample,
        step: int,
    ) -> list[RoomViewTile]:
        values = (
            telemetry.camera_x,
            telemetry.camera_y,
            telemetry.camera_width,
            telemetry.camera_height,
        )
        room = telemetry.room_name or str(telemetry.room_id)
        if (
            telemetry.mode != "overworld"
            or not room
            or room.casefold() == "unknown"
            or any(value is None for value in values)
        ):
            return []
        camera_x, camera_y, camera_width, camera_height = (
            float(value) for value in values if value is not None
        )
        if camera_width <= 0 or camera_height <= 0:
            return []
        if not room_view_image_is_usable(frame):
            return []
        viewport_box = camera_viewport_box(
            frame.size,
            (camera_width, camera_height),
        )
        if viewport_box is None:
            return []
        viewport = frame.convert("RGB").crop(viewport_box)
        regions = camera_region_coordinates(
            camera_x,
            camera_y,
            camera_width,
            camera_height,
        )
        # Do not freeze Kris or the immediately player-covered pixels into the
        # remembered scenery. Actual collision bounds are preferred when a
        # rich packet supplies them; otherwise use a conservative footprint
        # around the observed player origin.
        player_left = (
            float(telemetry.bbox_left) - 6
            if telemetry.bbox_left is not None
            else telemetry.x - 16
        )
        player_top = (
            float(telemetry.bbox_top) - 16
            if telemetry.bbox_top is not None
            else telemetry.y - 16
        )
        player_right = (
            float(telemetry.bbox_right) + 6
            if telemetry.bbox_right is not None
            else telemetry.x + 24
        )
        player_bottom = (
            float(telemetry.bbox_bottom) + 16
            if telemetry.bbox_bottom is not None
            else telemetry.y + 48
        )
        regions = {
            (region_x, region_y)
            for region_x, region_y in regions
            if (
                (region_x + 1) * REGION_PIXELS <= player_left
                or region_x * REGION_PIXELS >= player_right
                or (region_y + 1) * REGION_PIXELS <= player_top
                or region_y * REGION_PIXELS >= player_bottom
            )
        }

        rooms = self._rooms()
        room_data = rooms.setdefault(
            room,
            {
                "directory": _room_directory_name(room),
                "captures": 0,
                "last_step": step,
                "tiles": {},
            },
        )
        if not isinstance(room_data, dict):
            return []
        room_data["captures"] = int(room_data.get("captures", 0)) + 1
        room_data["last_step"] = step
        tiles = room_data.setdefault("tiles", {})
        if not isinstance(tiles, dict):
            return []
        room_directory = self.root / str(room_data["directory"])
        changed: list[RoomViewTile] = []

        for region_x, region_y in sorted(regions):
            tile_left = region_x * REGION_PIXELS
            tile_top = region_y * REGION_PIXELS
            world_left = max(float(tile_left), camera_x)
            world_top = max(float(tile_top), camera_y)
            world_right = min(float(tile_left + REGION_PIXELS), camera_x + camera_width)
            world_bottom = min(float(tile_top + REGION_PIXELS), camera_y + camera_height)
            if world_right <= world_left or world_bottom <= world_top:
                continue

            tile_key = f"{region_x},{region_y}"
            existing_record = tiles.get(tile_key)
            if (
                isinstance(existing_record, dict)
                and float(existing_record.get("coverage", 0.0)) >= 0.999
                and int(existing_record.get("pixels_per_world", 1))
                >= PIXELS_PER_WORLD
            ):
                # A complete high-resolution snapshot is intentionally stable.
                # Replacing it every few frames caused animated scenery and
                # camera jitter to flicker or tear across tile boundaries.
                continue

            source_left = floor(
                (world_left - camera_x) / camera_width * viewport.width
            )
            source_top = floor(
                (world_top - camera_y) / camera_height * viewport.height
            )
            source_right = ceil(
                (world_right - camera_x) / camera_width * viewport.width
            )
            source_bottom = ceil(
                (world_bottom - camera_y) / camera_height * viewport.height
            )
            source_box = (
                max(0, source_left),
                max(0, source_top),
                min(viewport.width, max(source_left + 1, source_right)),
                min(viewport.height, max(source_top + 1, source_bottom)),
            )
            destination_box = (
                max(0, floor((world_left - tile_left) * PIXELS_PER_WORLD)),
                max(0, floor((world_top - tile_top) * PIXELS_PER_WORLD)),
                min(TILE_PIXELS, ceil((world_right - tile_left) * PIXELS_PER_WORLD)),
                min(TILE_PIXELS, ceil((world_bottom - tile_top) * PIXELS_PER_WORLD)),
            )
            destination_width = destination_box[2] - destination_box[0]
            destination_height = destination_box[3] - destination_box[1]
            if destination_width <= 0 or destination_height <= 0:
                continue
            crop = viewport.crop(source_box).resize(
                (destination_width, destination_height),
                Image.Resampling.NEAREST,
            ).convert("RGBA")
            relative_path = Path(str(room_data["directory"])) / f"{region_x}_{region_y}.png"
            tile_path = self.root / relative_path
            if tile_path.exists():
                try:
                    with Image.open(tile_path) as existing_image:
                        previous = existing_image.convert("RGBA")
                except (OSError, ValueError):
                    previous = Image.new("RGBA", (TILE_PIXELS, TILE_PIXELS))
            else:
                previous = Image.new("RGBA", (TILE_PIXELS, TILE_PIXELS))
            if previous.size != (TILE_PIXELS, TILE_PIXELS):
                previous = Image.new("RGBA", (TILE_PIXELS, TILE_PIXELS))
            updated = previous.copy()
            # Remember each revealed pixel once. Replacing already opaque
            # pixels made partial camera-edge tiles tear whenever animated
            # scenery or a sub-pixel camera shift crossed the same region.
            previous_area = previous.crop(destination_box).getchannel("A")
            unseen_mask = ImageChops.invert(previous_area)
            updated.paste(crop, destination_box[:2], unseen_mask)
            difference = ImageChops.difference(previous, updated)
            # RGBA getbbox() can ignore RGB-only changes when the alpha
            # difference is zero, so inspect every band independently.
            if not any(band.getbbox() is not None for band in difference.split()):
                continue
            room_directory.mkdir(parents=True, exist_ok=True)
            temporary = tile_path.with_suffix(".tmp.png")
            try:
                updated.save(temporary, format="PNG", optimize=True)
                temporary.replace(tile_path)
            except OSError:
                # A single tile write can fail on a transient filesystem issue.
                # Keep the in-memory state and skip persisting this tile for now.
                continue
            alpha = updated.getchannel("A")
            coverage = sum(value > 0 for value in alpha.getdata()) / (
                TILE_PIXELS * TILE_PIXELS
            )
            tiles[tile_key] = {
                "region_x": region_x,
                "region_y": region_y,
                "path": relative_path.as_posix(),
                "coverage": round(coverage, 3),
                "pixels_per_world": PIXELS_PER_WORLD,
                "last_step": step,
            }
            changed.append(
                RoomViewTile(
                    room,
                    region_x,
                    region_y,
                    tile_path,
                    coverage,
                    step,
                )
            )

        self._save_index()
        return changed

    def _save_index(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.index_path.with_suffix(".tmp.json")
        try:
            temporary.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            temporary.replace(self.index_path)
        except OSError:
            # Preserve the in-memory model even if the on-disk index cannot be updated.
            pass
