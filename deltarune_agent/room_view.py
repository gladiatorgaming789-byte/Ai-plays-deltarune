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
from .viewport import camera_viewport_box


INDEX_VERSION = 3
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
        # A changed opaque pixel is replaced only after two matching player-
        # free observations. This repairs old sprite ghosts and camera seams
        # without making animated scenery flicker on every analysis frame.
        self._candidate_tiles: dict[tuple[str, int, int], Image.Image] = {}
        self._load()

    def _load(self) -> None:
        if not self.index_path.exists():
            return
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            version = int(data.get("version", 0))
            if version not in {1, 2, INDEX_VERSION}:
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
        # Do not freeze Kris into the remembered scenery. Mask only the exact
        # player rectangle rather than throwing away every 32x32 region it
        # touches; the old coarse exclusion produced conspicuous black holes.
        player_left = (
            float(getattr(telemetry, "player_bbox_left", None)) - 2
            if getattr(telemetry, "player_bbox_left", None) is not None
            else float(getattr(telemetry, "bbox_left", None)) - 2
            if getattr(telemetry, "bbox_left", None) is not None
            else telemetry.x - 8
        )
        player_top = (
            float(getattr(telemetry, "player_bbox_top", None)) - 2
            if getattr(telemetry, "player_bbox_top", None) is not None
            else float(getattr(telemetry, "bbox_top", None)) - 2
            if getattr(telemetry, "bbox_top", None) is not None
            else telemetry.y - 16
        )
        player_right = (
            float(getattr(telemetry, "player_bbox_right", None)) + 2
            if getattr(telemetry, "player_bbox_right", None) is not None
            else float(getattr(telemetry, "bbox_right", None)) + 2
            if getattr(telemetry, "bbox_right", None) is not None
            else telemetry.x + 8
        )
        player_bottom = (
            float(getattr(telemetry, "player_bbox_bottom", None)) + 2
            if getattr(telemetry, "player_bbox_bottom", None) is not None
            else float(getattr(telemetry, "bbox_bottom", None)) + 2
            if getattr(telemetry, "bbox_bottom", None) is not None
            else telemetry.y + 16
        )

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
            overlap_left = max(world_left, player_left)
            overlap_top = max(world_top, player_top)
            overlap_right = min(world_right, player_right)
            overlap_bottom = min(world_bottom, player_bottom)
            if overlap_right > overlap_left and overlap_bottom > overlap_top:
                alpha = crop.getchannel("A")
                mask_box = (
                    max(0, floor((overlap_left - world_left) * PIXELS_PER_WORLD)),
                    max(0, floor((overlap_top - world_top) * PIXELS_PER_WORLD)),
                    min(
                        destination_width,
                        ceil((overlap_right - world_left) * PIXELS_PER_WORLD),
                    ),
                    min(
                        destination_height,
                        ceil((overlap_bottom - world_top) * PIXELS_PER_WORLD),
                    ),
                )
                if mask_box[2] > mask_box[0] and mask_box[3] > mask_box[1]:
                    alpha.paste(0, mask_box)
                    crop.putalpha(alpha)
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
            previous_area = previous.crop(destination_box).getchannel("A")
            unseen_mask = ImageChops.invert(previous_area)
            paste_mask = ImageChops.multiply(unseen_mask, crop.getchannel("A"))

            candidate_key = (room, region_x, region_y)
            candidate = self._candidate_tiles.get(candidate_key)
            if candidate is not None:
                prior_crop = candidate.crop(destination_box)
                delta = ImageChops.difference(
                    prior_crop.convert("RGB"),
                    crop.convert("RGB"),
                )
                bands = delta.split()
                max_delta = ImageChops.lighter(
                    ImageChops.lighter(bands[0], bands[1]),
                    bands[2],
                )
                stable_mask = max_delta.point(
                    lambda value: 255 if value <= 6 else 0
                )
                stable_mask = ImageChops.multiply(
                    stable_mask,
                    prior_crop.getchannel("A"),
                )
                stable_mask = ImageChops.multiply(
                    stable_mask,
                    crop.getchannel("A"),
                )
                stable_mask = ImageChops.multiply(stable_mask, previous_area)
                paste_mask = ImageChops.lighter(paste_mask, stable_mask)
            updated.paste(crop, destination_box[:2], paste_mask)

            next_candidate = (
                candidate.copy()
                if candidate is not None
                else Image.new("RGBA", (TILE_PIXELS, TILE_PIXELS))
            )
            next_candidate.paste(
                crop,
                destination_box[:2],
                crop.getchannel("A"),
            )
            self._candidate_tiles[candidate_key] = next_candidate
            difference = ImageChops.difference(previous, updated)
            # RGBA getbbox() can ignore RGB-only changes when the alpha
            # difference is zero, so inspect every band independently.
            if not any(band.getbbox() is not None for band in difference.split()):
                continue
            room_directory.mkdir(parents=True, exist_ok=True)
            temporary = tile_path.with_suffix(".tmp.png")
            try:
                # PNG is lossless at every compression level. Fast encoding
                # keeps scene-memory writes from pausing the control loop.
                updated.save(temporary, format="PNG", compress_level=1)
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

        if changed:
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
